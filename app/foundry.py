"""Thin wrapper around Foundry Local's OpenAI-compatible endpoint.

Lifecycle strategy
------------------
Foundry Local lets a client choose the web-service port via the SDK's
``Configuration(web={"urls": "http://host:port"})``. So instead of guessing a
dynamic port, ViveEnglish (when ``VIVE_MANAGE_FOUNDRY`` is on and the SDK is
installed):

1. Picks a FREE TCP port at startup (or uses ``VIVE_FOUNDRY_PORT``).
2. Starts Foundry Local bound to exactly that port.
3. Optionally loads the chat model if it is already cached.
4. Talks to ``http://host:port/v1`` via the OpenAI client.

If the SDK is not installed, it falls back to: explicit ``FOUNDRY_BASE_URL`` ->
``foundry service status`` parsing -> a probe of common ports. Every AI helper
degrades gracefully to an OFFLINE response so the rest of the app keeps working.
"""
from __future__ import annotations

import json
import re
import socket
import threading
from typing import Any

from . import config

_client = None
_base_url: str | None = None
_model: str | None = None
_manager = None  # live foundry-local-sdk manager (when managed)
_status: dict[str, Any] = {"online": False, "base_url": None, "model": None,
                           "note": "not initialised", "managed": False, "port": None}
_lock = threading.Lock()

# First-launch setup progress (execution-provider + model download/load).
# state: idle | checking | preparing | downloading | loading | ready | offline | error
_setup: dict[str, Any] = {"state": "idle", "progress": 0.0, "phase": "",
                          "model": None, "message": "", "detail": ""}
_setup_lock = threading.Lock()
_setup_thread: "threading.Thread | None" = None


# --- URL / port helpers ----------------------------------------------------

def _normalize(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _reachable(base: str) -> bool:
    """Quick check that an OpenAI-compatible endpoint answers at base url."""
    try:
        import urllib.request
        with urllib.request.urlopen(base.rstrip("/") + "/models", timeout=2) as r:
            return r.status < 500
    except Exception:
        return False


def _free_port() -> int:
    """Ask the OS for an unused TCP port on the configured host."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((config.FOUNDRY_HOST, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _port_is_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((config.FOUNDRY_HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _choose_port() -> int:
    if config.FOUNDRY_PORT.isdigit():
        p = int(config.FOUNDRY_PORT)
        return p  # honour explicit request even if busy (user's choice)
    return _free_port()


# --- Managed start (preferred) ---------------------------------------------

def _start_managed() -> str | None:
    """Start Foundry Local on a port we choose. Returns base url or None."""
    global _manager
    try:  # pragma: no cover - depends on local install
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore
    except Exception:
        return None

    host = config.FOUNDRY_HOST
    # Try the chosen port, then a couple of fresh free ports on collision.
    candidate_ports = []
    p0 = _choose_port()
    candidate_ports.append(p0)
    for _ in range(2):
        candidate_ports.append(_free_port())

    last_err = ""
    for port in candidate_ports:
        url = f"http://{host}:{port}"
        try:
            cfg = Configuration(app_name="viveenglish", web={"urls": url})
            FoundryLocalManager.initialize(cfg)
            manager = FoundryLocalManager.instance
            manager.start_web_service()
            base = _normalize(url)
            # Give the service a moment, then verify.
            import time
            for _ in range(15):
                if _reachable(base):
                    break
                time.sleep(0.4)
            if _reachable(base):
                _manager = manager
                _status["managed"] = True
                _status["port"] = port
                _maybe_load_model(manager)
                return base
            last_err = f"started but not reachable on {url}"
        except Exception as exc:
            last_err = str(exc)
            continue
    if last_err:
        _status["note"] = f"managed start failed: {last_err}"
    return None


# Model IDs that are NOT text chat-completion models (vision, embeddings,
# speech/whisper, rerankers, image/audio generators). Used to avoid selecting
# e.g. a *-vl* vision model or an embedding model for the chat features.
_NON_CHAT = re.compile(
    r"(embed|whisper|speech|transcrib|audio|rerank|clip|stable-?diffusion|"
    r"sdxl|\btts\b|-vl-|-vl\b|vlm|vision|florence|moondream)", re.I)


def _catalog_task(manager, model_id: str) -> str | None:
    """Best-effort lookup of a model's task type from the SDK catalog."""
    try:  # pragma: no cover
        listers = []
        for name in ("list_models", "get_models", "get_cached_models", "get_loaded_models"):
            fn = getattr(manager.catalog, name, None)
            if callable(fn):
                listers.append(fn)
        for fn in listers:
            try:
                items = fn()
            except Exception:
                continue
            for m in items or []:
                mid = getattr(m, "id", "") or getattr(m, "alias", "")
                if mid and (mid == model_id or mid in model_id or model_id in mid):
                    task = (getattr(m, "task", "") or getattr(m, "task_type", "") or "")
                    if task:
                        return str(task).lower()
    except Exception:
        pass
    return None


def _pick_chat_model(ids: list[str], manager=None) -> str | None:
    """Choose a text/chat-completion model, never a vision/embedding/audio one."""
    if not ids:
        return None
    alias = config.CHAT_MODEL.lower()
    # 1) configured alias, as long as it isn't an obviously non-chat variant
    preferred = [m for m in ids if alias in m.lower() and not _NON_CHAT.search(m)]
    if preferred:
        return preferred[0]
    # 2) drop clearly non-chat models
    chat_ids = [m for m in ids if not _NON_CHAT.search(m)]
    # 3) if the catalog exposes task info, keep only chat-completion/text-gen
    if manager is not None and chat_ids:
        verified = []
        for m in chat_ids:
            task = _catalog_task(manager, m)
            if task is None or "chat" in task or "text-generation" in task or "text-gen" in task:
                verified.append(m)
        if verified:
            chat_ids = verified
    pool = chat_ids or ids
    # 4) prefer instruct/chat-named models
    for m in pool:
        if "instruct" in m.lower() or "chat" in m.lower():
            return m
    return pool[0]


def _maybe_load_model(manager) -> None:
    """Load a cached CHAT model if available (no surprise multi-GB downloads)."""
    if not config.AUTOLOAD_MODEL:
        return
    try:  # pragma: no cover
        model = manager.catalog.get_model(config.CHAT_MODEL)
        mid = getattr(model, "id", "") or config.CHAT_MODEL
        if getattr(model, "is_cached", False) and not _NON_CHAT.search(mid):
            model.load()
            return
    except Exception:
        pass
    # Otherwise load the first cached chat-capable model we can find.
    try:  # pragma: no cover
        getter = getattr(manager.catalog, "get_cached_models", None)
        for m in (getter() if callable(getter) else []) or []:
            mid = getattr(m, "id", "") or getattr(m, "alias", "")
            task = (getattr(m, "task", "") or "").lower()
            if not mid or _NON_CHAT.search(mid):
                continue
            if "chat" in task or "text-generation" in task or "instruct" in mid.lower():
                manager.catalog.get_model(mid).load()
                return
    except Exception:
        pass  # not fatal; chat will report if no model is ready


# --- Fallback discovery (SDK not installed / managed disabled) -------------

def _from_cli() -> str | None:
    """Parse `foundry service status` to find an already-running endpoint."""
    try:  # pragma: no cover - depends on local install
        import subprocess
        out = subprocess.run(["foundry", "service", "status"],
                             capture_output=True, text=True, timeout=8)
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"https?://[\w.\-]+:\d+", text)
        if m:
            return _normalize(m.group(0))
    except Exception:
        pass
    return None


def _discover_unmanaged() -> str | None:
    candidates: list[str] = []
    if config.FOUNDRY_BASE_URL:
        candidates.append(_normalize(config.FOUNDRY_BASE_URL))
    u = _from_cli()
    if u:
        candidates.append(u)
    if config.FOUNDRY_FALLBACK_URL:
        candidates.append(_normalize(config.FOUNDRY_FALLBACK_URL))
    for port in (5273, 5272, 8000, 1234):
        candidates.append(f"http://127.0.0.1:{port}/v1")
    seen, first = set(), None
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        first = first or c
        if _reachable(c):
            return c
    return first


def _resolve_base_url() -> str | None:
    # Reuse an already-running managed service rather than starting a new one.
    if _manager is not None and _base_url and _reachable(_base_url):
        return _base_url
    # Explicit override always wins (attach to an external service).
    if config.FOUNDRY_BASE_URL:
        return _normalize(config.FOUNDRY_BASE_URL)
    if config.MANAGE_FOUNDRY:
        managed = _start_managed()
        if managed:
            return managed
    return _discover_unmanaged()


# --- Init / status ---------------------------------------------------------

def init() -> dict[str, Any]:
    """Probe / start Foundry Local. Safe to call repeatedly."""
    global _client, _base_url, _model, _status
    with _lock:
        try:
            from openai import OpenAI
        except Exception as exc:
            _status = {"online": False, "base_url": None, "model": None,
                       "note": f"openai client unavailable: {exc}",
                       "managed": False, "port": None}
            return _status

        base = _resolve_base_url()
        if not base:
            _status.update(online=False, base_url=None, model=None,
                           note="Foundry Local endpoint not available")
            return _status

        model = config.CHAT_MODEL
        try:
            client = OpenAI(base_url=base, api_key=config.FOUNDRY_API_KEY,
                            timeout=config.AI_TIMEOUT, max_retries=0)
            models = client.models.list()
            ids = [m.id for m in getattr(models, "data", [])]
            picked = _pick_chat_model(ids, _manager)
            if picked:
                model = picked
            _client, _base_url, _model = client, base, model
            note = "ready"
            if ids and _NON_CHAT.search(model or ""):
                note = ("warning: selected model may not support chat. "
                        "Set VIVE_CHAT_MODEL to a text/chat-completion model.")
            _status.update(online=True, base_url=base, model=model, note=note)
        except Exception as exc:
            _client, _base_url, _model = None, base, model
            _status.update(online=False, base_url=base, model=model,
                           note=f"endpoint not reachable: {exc}")
        return _status


def status() -> dict[str, Any]:
    return dict(_status)


def get_manager():
    """Expose the managed SDK manager (used for on-device transcription)."""
    return _manager


# --- First-launch setup: download execution providers + chat model ---------

def setup_state() -> dict[str, Any]:
    with _setup_lock:
        return dict(_setup)


def _set_setup(**kw: Any) -> None:
    with _setup_lock:
        _setup.update(kw)


def ensure_model_async() -> dict[str, Any]:
    """Kick off (once) the first-launch download/load in a background thread."""
    global _setup_thread
    with _setup_lock:
        if _setup["state"] in ("checking", "preparing", "downloading", "loading"):
            return dict(_setup)
        if _setup_thread is not None and _setup_thread.is_alive():
            return dict(_setup)
        _setup_thread = threading.Thread(target=_run_setup, daemon=True)
        _setup_thread.start()
        _setup.update(state="checking", message="準備を確認しています…")
        return dict(_setup)


def _run_setup() -> None:
    """Download accelerators (EPs/DLLs) and the chat model, with progress."""
    try:
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore  # noqa
    except Exception:
        _set_setup(state="offline", progress=0,
                   message="AI SDK が見つかりません。オフラインのまま学習を続けられます。")
        return

    _set_setup(state="checking", progress=0, phase="init",
               message="ローカルAIを初期化しています…")
    # Make sure the managed service is up (also sets _manager).
    init()
    mgr = _manager
    if mgr is None:
        _set_setup(state="offline", message="ローカルAIサービスを起動できませんでした。"
                                            "オフラインのまま学習を続けられます。")
        return

    # 1) Execution providers (hardware acceleration libraries / DLLs).
    try:
        dl = getattr(mgr, "download_and_register_eps", None)
        if callable(dl):
            _set_setup(state="preparing", phase="eps", progress=0,
                       message="ハードウェア対応モジュールを準備中…")

            def ep_cb(ep_name, percent):
                try:
                    _set_setup(progress=float(percent), detail=str(ep_name))
                except Exception:
                    pass
            try:
                dl(progress_callback=ep_cb)
            except TypeError:
                dl(ep_cb)  # alternate signature
    except Exception:
        pass  # EP prep is best-effort; model can still run on CPU fallback

    # 2) Chat model download + load.
    try:
        alias = config.CHAT_MODEL
        model = mgr.catalog.get_model(alias)
        mid = getattr(model, "id", "") or alias
        if _NON_CHAT.search(mid):
            _set_setup(state="error", model=mid,
                       message=f"設定モデル {alias} はチャット非対応です。VIVE_CHAT_MODEL を見直してください。")
            return
        if getattr(model, "is_cached", False):
            _set_setup(state="loading", phase="load", progress=100, model=mid,
                       message="モデルを読み込んでいます…")
        else:
            _set_setup(state="downloading", phase="model", progress=0, model=mid,
                       message=f"AIモデル（{alias}）をダウンロード中…")

            def cb(percent):
                try:
                    _set_setup(progress=float(percent))
                except Exception:
                    pass
            model.download(cb)
            _set_setup(state="loading", phase="load", progress=100,
                       message="モデルを読み込んでいます…")
        model.load()
    except Exception as exc:
        _set_setup(state="error",
                   message=f"モデルの準備に失敗しました: {exc}. "
                           f"`foundry model run {config.CHAT_MODEL}` を一度お試しください。")
        return

    # Refresh the OpenAI client so chat uses the freshly loaded model.
    init()
    _set_setup(state="ready", progress=100, phase="done", message="準備が完了しました。")


def _chat(messages: list[dict[str, str]], *, temperature: float = 0.4,
          max_tokens: int = 512, json_mode: bool = False) -> str | None:
    """Low-level chat call. Returns None when offline so callers can fall back."""
    if _client is None:
        init()
    if _client is None:
        return None
    base: dict[str, Any] = dict(model=_model, messages=messages,
                                temperature=temperature, max_tokens=max_tokens)
    # Try with JSON mode first (if requested), then retry plain — some local
    # models reject response_format. Either way callers tolerate plain text.
    attempts = ([{**base, "response_format": {"type": "json_object"}}] if json_mode else []) + [base]
    last = None
    for kw in attempts:
        try:
            resp = _client.chat.completions.create(**kw)
            return resp.choices[0].message.content
        except Exception as exc:  # pragma: no cover
            last = exc
            continue
    _status["online"] = False
    _status["note"] = f"call failed: {last}"
    return None


# --- Public helpers --------------------------------------------------------

def translate(text: str, mode: str = "auto", glossary: dict | None = None) -> dict[str, Any]:
    """Translate English -> Japanese.

    mode: 'word' (concise gloss + part of speech), 'phrase'/'sentence'
    (natural translation + short note). Falls back to a supplied glossary
    (pre-authored per-lesson vocab) when offline.
    """
    text = (text or "").strip()
    if not text:
        return {"source": text, "translation": "", "note": "", "online": _status["online"]}

    if mode == "auto":
        mode = "word" if len(text.split()) == 1 else "sentence"

    if mode == "word":
        instruction = (
            "あなたは日本人英語学習者向けの辞書です。次の英単語の意味を答えてください。"
            "JSONで {\"translation\": \"日本語の意味(簡潔に)\", \"pos\": \"品詞\", "
            "\"note\": \"使い方の一言メモ(任意)\"} の形式のみ返してください。"
        )
    else:
        instruction = (
            "あなたは日本人英語学習者向けの翻訳者です。次の英文を自然な日本語に訳してください。"
            "JSONで {\"translation\": \"自然な和訳\", \"note\": \"文法やニュアンスの一言メモ(任意)\"} "
            "の形式のみ返してください。"
        )

    raw = _chat(
        [{"role": "system", "content": instruction},
         {"role": "user", "content": text}],
        temperature=0.2, max_tokens=300, json_mode=True,
    )
    if raw:
        parsed = _safe_json(raw)
        if parsed and parsed.get("translation"):
            parsed.setdefault("note", "")
            return {"source": text, "online": True, **parsed}
        # Model returned plain text (no JSON): use it directly as the translation.
        cleaned = _plain_text(raw)
        if cleaned:
            return {"source": text, "translation": cleaned, "note": "", "online": True}

    if glossary:
        gloss = glossary.get(text.lower().strip(".,!?\"'"))
        if gloss:
            return {"source": text, "translation": gloss, "note": "",
                    "online": False, "offline_fallback": True}
    return {"source": text, "translation": "(AIオフライン: 訳を取得できませんでした)",
            "note": "Foundry Local を起動すると和訳が利用できます。",
            "online": False, "offline_fallback": True}


def tutor_reply(history: list[dict[str, str]], scenario: str = "",
                level: str = "beginner", name: str = "Vivi",
                gender: str = "female") -> dict[str, Any]:
    """Conversation partner that replies in English and gently corrects."""
    g = "male" if str(gender).lower().startswith("m") else "female"
    sys = (
        f"You are '{name}', a warm, encouraging {g} English conversation partner for a "
        f"Japanese {level} learner. Stay strictly in the role-play scenario: {scenario or 'free talk'}. "
        "Rules: (1) Reply in short, natural English suited to the learner's level. "
        "(2) If the learner's last message had a notable mistake, add ONE gentle correction. "
        "(3) Always end with a simple follow-up question to keep the conversation going. "
        "Return ONLY JSON: {\"reply\":\"English reply\", \"reply_ja\":\"返信の和訳\", "
        "\"correction\":\"訂正(無ければ空文字)\", \"tip\":\"短い学習ヒント(任意)\"}."
    )
    messages = [{"role": "system", "content": sys}] + history
    raw = _chat(messages, temperature=0.6, max_tokens=400, json_mode=True)
    if raw:
        parsed = _safe_json(raw)
        if parsed and parsed.get("reply"):
            for k in ("reply_ja", "correction", "tip"):
                parsed.setdefault(k, "")
            return {"online": True, **parsed}
        # Plain-text reply (model didn't emit JSON): use it as Vivi's reply.
        cleaned = _plain_text(raw)
        if cleaned:
            return {"online": True, "reply": cleaned, "reply_ja": "",
                    "correction": "", "tip": ""}
    return {
        "online": False,
        "reply": "(AI offline) Let's keep practising! Start Foundry Local to chat with Vivi.",
        "reply_ja": "(AIオフライン) Foundry Local を起動すると会話できます。",
        "correction": "", "tip": "",
    }


def check_speech(target: str, said: str, level: str = "beginner") -> dict[str, Any]:
    """Compare what the learner said (from transcription) to the target line."""
    target = (target or "").strip()
    said = (said or "").strip()
    score = _word_overlap_score(target, said)

    sys = (
        "You are a friendly English pronunciation/accuracy coach for a Japanese "
        f"{level} learner. Compare the TARGET sentence and what the learner SAID "
        "(auto-transcribed, so ignore punctuation/case). Return ONLY JSON: "
        "{\"score\":0-100, \"good\":\"褒めポイント(日本語)\", "
        "\"improve\":\"直すと良い点(日本語, 具体的に)\", "
        "\"missed_words\":[\"言えていない/違った単語\"]}."
    )
    raw = _chat(
        [{"role": "system", "content": sys},
         {"role": "user", "content": f"TARGET: {target}\nSAID: {said}"}],
        temperature=0.2, max_tokens=300, json_mode=True,
    )
    if raw:
        parsed = _safe_json(raw)
        if parsed and "score" in parsed:
            parsed.setdefault("missed_words", [])
            parsed.setdefault("good", "")
            parsed.setdefault("improve", "")
            try:
                parsed["score"] = int(float(parsed["score"]))
            except Exception:
                parsed["score"] = score
            return {"online": True, **parsed}
        # Plain-text feedback: keep the word-overlap score, show the model's note.
        cleaned = _plain_text(raw)
        if cleaned:
            return {"online": True, "score": score, "good": "",
                    "improve": cleaned, "missed_words": _missing_words(target, said)}

    missed = _missing_words(target, said)
    return {
        "online": False,
        "score": score,
        "good": "発話が記録されました。" if said else "",
        "improve": ("もう少しはっきり発音してみましょう。" if score < 80
                    else "とても良いです！この調子で続けましょう。"),
        "missed_words": missed,
    }


# --- Small utilities -------------------------------------------------------

def _safe_json(text: str) -> dict | None:
    """Parse a JSON object from model output. Returns a dict or None.

    Small models often ignore json_mode and return a bare string / prose, so we
    only accept dict results and otherwise signal failure to the caller.
    """
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict):
                return v
        except Exception:
            return None
    return None


def _plain_text(raw: str) -> str:
    """Clean a non-JSON model reply into a short usable string."""
    if not raw:
        return ""
    t = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # If it's a bare JSON string literal like "..." -> unwrap it.
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        try:
            v = json.loads(t)
            if isinstance(v, str):
                return v.strip()
        except Exception:
            pass
    return t.strip().strip('"').strip()


def _tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z']+", s.lower()) if w]


def _word_overlap_score(target: str, said: str) -> int:
    t, s = _tokens(target), set(_tokens(said))
    if not t:
        return 0
    hit = sum(1 for w in t if w in s)
    return round(100 * hit / len(t))


def _missing_words(target: str, said: str) -> list[str]:
    s = set(_tokens(said))
    return [w for w in _tokens(target) if w not in s][:8]
