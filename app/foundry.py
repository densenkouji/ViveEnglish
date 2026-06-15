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

import io
import json
import os
import random
import re
import socket
import tempfile
import threading
import wave
from typing import Any

from . import config, database

_client = None
_base_url: str | None = None
_model: str | None = None
_translate_model: str | None = None
_manager = None  # live foundry-local-sdk manager (when managed)
_status: dict[str, Any] = {"online": False, "base_url": None, "model": None,
                           "translate_model": None, "note": "not initialised",
                           "managed": False, "port": None}
_lock = threading.Lock()

# First-launch setup progress (execution-provider + model download/load).
# state: idle | checking | preparing | downloading | loading | ready | offline | error
_setup: dict[str, Any] = {"state": "idle", "progress": 0.0, "phase": "",
                          "model": None, "message": "", "detail": ""}
_setup_lock = threading.Lock()
_setup_thread: "threading.Thread | None" = None


def _base_status() -> dict[str, Any]:
    return dict(_status)


# --- User-selected model preferences (persisted in profile.settings) --------
# The settings UI lets the learner pick which cached model to use for chat,
# translation, and speech-to-text. These override the config/env defaults.

def _prefs() -> dict[str, Any]:
    try:
        return database.get_profile().get("settings") or {}
    except Exception:
        return {}


def eff_chat_model() -> str:
    return (_prefs().get("chat_model") or "").strip() or config.CHAT_MODEL


def eff_translate_model() -> str:
    """Translate model preference; empty falls back to the chat model."""
    return (_prefs().get("translate_model") or "").strip() or config.TRANSLATE_MODEL


def eff_transcribe_model() -> str:
    return (_prefs().get("transcribe_model") or "").strip() or config.TRANSCRIBE_MODEL


def set_model_preference(kind: str, alias: str | None) -> dict[str, Any]:
    """Persist a model choice ('chat'|'translate'|'transcribe') and reconnect."""
    key = {"chat": "chat_model", "translate": "translate_model",
           "transcribe": "transcribe_model"}.get(kind)
    if not key:
        raise ValueError(f"unknown model kind: {kind}")
    settings = _prefs()
    settings[key] = (alias or "").strip()
    database.update_profile(settings=settings)
    # Reconnect so chat/translate immediately use the newly chosen model.
    if kind in ("chat", "translate"):
        reconnect()
    return status()


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
    import time

    # The SDK's FoundryLocalManager is a process-wide singleton: initialize()
    # can only be called once and binds the web URL from config at that time.
    # So we choose ONE port, build the config, and (re)use the singleton.
    manager = getattr(FoundryLocalManager, "instance", None)

    def _verify_and_finish(base_url: str, port: int) -> str | None:
        for _ in range(15):
            if _reachable(base_url):
                break
            time.sleep(0.4)
        if _reachable(base_url):
            _status["managed"] = True
            _status["port"] = port
            return base_url
        return None

    # If a singleton already exists (e.g. created earlier for speech), reuse it
    # and just (re)start the web service on its configured URL.
    if manager is not None:
        try:
            urls = getattr(manager, "urls", None)
            if not urls:
                manager.start_web_service()
                urls = getattr(manager, "urls", None)
            bound = urls[0] if isinstance(urls, (list, tuple)) and urls else (urls or "")
            if not bound:
                # No web URL bound to the existing singleton; fall back to a probe.
                return None
            base = _normalize(str(bound))
            port = int(str(bound).rstrip("/").rsplit(":", 1)[-1]) if ":" in str(bound) else 0
            _manager = manager
            done = _verify_and_finish(base, port)
            if done:
                _maybe_load_model(manager)
                return done
        except Exception as exc:
            _status["note"] = f"managed reuse failed: {exc}"
        return None

    # Fresh start: pick a port and initialize the singleton with a WebService.
    port = _choose_port()
    url = f"http://{host}:{port}"
    try:
        web = Configuration.WebService(urls=url)
        cfg = Configuration(app_name="viveenglish", web=web)
        FoundryLocalManager.initialize(cfg)
        manager = FoundryLocalManager.instance
        manager.start_web_service()
        # The service reports the actually-bound URL(s); prefer them.
        bound = getattr(manager, "urls", None)
        actual = (bound[0] if isinstance(bound, (list, tuple)) and bound else (bound or url))
        base = _normalize(str(actual))
        try:
            port = int(str(actual).rstrip("/").rsplit(":", 1)[-1])
        except Exception:
            pass
        _manager = manager
        done = _verify_and_finish(base, port)
        if done:
            _maybe_load_model(manager)
            return done
        _status["note"] = f"started but not reachable on {base}"
    except Exception as exc:
        _status["note"] = f"managed start failed: {exc}"
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


def _strip_variant(s: str) -> str:
    """Normalize a model id for matching: lowercase, drop the ':<n>' variant suffix."""
    return (s or "").split(":", 1)[0].strip().lower()


def _model_ref_matches(ref: str | None, model_id: str | None, alias: str | None = "") -> bool:
    """Return True when a preference/active model ref points at a catalog model."""
    ref = (ref or "").strip().lower()
    if not ref:
        return False
    candidates = []
    for value in (model_id, alias):
        value = (value or "").strip().lower()
        if value:
            candidates.append(value)
            base = _strip_variant(value)
            if base and base not in candidates:
                candidates.append(base)
    ref_base = _strip_variant(ref)
    refs = [ref]
    if ref_base and ref_base != ref:
        refs.append(ref_base)
    for r in refs:
        for c in candidates:
            if r == c:
                return True
            # Foundry often reports a concrete variant id where the setting uses
            # a short alias. Treat prefix/contains matches as the same model,
            # but only after exact checks so unrelated empty strings never match.
            if len(r) >= 4 and len(c) >= 4 and (
                c.startswith(r) or r.startswith(c) or r in c or c in r
            ):
                return True
    return False


def _pick_chat_model(ids: list[str], manager=None) -> str | None:
    """Choose a text/chat-completion model, never a vision/embedding/audio one."""
    if not ids:
        return None
    alias = eff_chat_model().lower()
    # 1) configured model — match ignoring the ':<n>' variant suffix on either
    # side, since /v1/models reports ids without it but prefs may carry it.
    preferred = [m for m in ids
                 if not _NON_CHAT.search(m)
                 and _model_ref_matches(alias, m)]
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


def _resolve_configured_model(ids: list[str], configured: str) -> str | None:
    """Resolve a configured alias/prefix to a currently loaded model id."""
    key = (configured or "").strip().lower()
    if not key:
        return None
    for mid in ids:
        if _model_ref_matches(key, mid):
            return mid
    return configured


def _maybe_load_model(manager) -> None:
    """Load a cached CHAT model if available (no surprise multi-GB downloads)."""
    if not config.AUTOLOAD_MODEL:
        return
    try:  # pragma: no cover
        chat_alias = eff_chat_model()
        model = _find_catalog_model(manager, chat_alias)
        mid = getattr(model, "id", "") or chat_alias
        if model is not None and getattr(model, "is_cached", False) and not _NON_CHAT.search(mid):
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
                m.load()
                return
    except Exception:
        pass  # not fatal; chat will report if no model is ready


def _prepare_catalog_model(manager, alias: str, *, phase: str, label: str) -> str | None:
    """Download/load a configured catalog model and return its resolved id."""
    if not alias:
        return None
    model = _find_catalog_model(manager, alias)
    if model is None:
        raise ValueError(f"model '{alias}' not found in catalog")
    mid = getattr(model, "id", "") or alias
    if _NON_CHAT.search(mid):
        raise ValueError(f"{alias} is not a text/chat model ({mid})")
    if getattr(model, "is_cached", False):
        _set_setup(state="loading", phase=phase, progress=100, model=mid,
                   message=f"{label}を読み込んでいます…")
    else:
        _set_setup(state="downloading", phase=phase, progress=0, model=mid,
                   message=f"{label}（{alias}）をダウンロード中…")

        def cb(percent):
            try:
                _set_setup(progress=float(percent))
            except Exception:
                pass
        model.download(cb)
        _set_setup(state="loading", phase=phase, progress=100, model=mid,
                   message=f"{label}を読み込んでいます…")
    model.load()
    return mid


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


def _own_port() -> int:
    """Port this ViveEnglish web server listens on (so we never probe ourselves)."""
    try:
        return int(os.getenv("PORT", "8000"))
    except Exception:
        return 8000


def _discover_unmanaged() -> str | None:
    candidates: list[str] = []
    if config.FOUNDRY_BASE_URL:
        candidates.append(_normalize(config.FOUNDRY_BASE_URL))
    u = _from_cli()
    if u:
        candidates.append(u)
    if config.FOUNDRY_FALLBACK_URL:
        candidates.append(_normalize(config.FOUNDRY_FALLBACK_URL))
    # Common Foundry Local ports. Exclude our own web-server port so we never
    # probe ViveEnglish itself (which would hit /v1/models and log a 404).
    own = _own_port()
    for port in (5273, 5272, 1234):
        if port != own:
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
    unmanaged = _discover_unmanaged()
    if unmanaged and _reachable(unmanaged):
        return unmanaged
    # Do not return a stale fallback URL such as localhost:5273 unless it
    # actually answers; init() would otherwise report that dead URL forever.
    return None


def _force_managed_base_url() -> str | None:
    """Start/restart the SDK-managed web service and return its /v1 base URL."""
    global _manager, _base_url
    _manager = None
    managed = _start_managed()
    if managed:
        _base_url = managed
        return managed
    return None


# --- Init / status ---------------------------------------------------------

def _ensure_loaded(model_id: str) -> bool:
    """Make sure a model is loaded into memory before chat calls hit it.

    Foundry Local lists cached models at /v1/models even when they are not yet
    loaded, and a chat completion against an unloaded model fails with 400
    'Model ... is not loaded'. Load it via the SDK catalog if needed.
    """
    if not model_id or _manager is None:
        return False
    try:  # pragma: no cover - depends on local install
        m = _find_catalog_model(_manager, model_id)
        if m is None:
            return False
        if getattr(m, "is_loaded", False):
            return True
        m.load()
        return True
    except Exception as exc:
        _status["note"] = f"model load failed: {exc}"
        return False


def init() -> dict[str, Any]:
    """Probe / start Foundry Local. Safe to call repeatedly."""
    global _client, _base_url, _model, _translate_model, _status
    with _lock:
        try:
            from openai import OpenAI
        except Exception as exc:
            _status = {"online": False, "base_url": None, "model": None,
                       "translate_model": None,
                       "note": f"openai client unavailable: {exc}",
                       "managed": False, "port": None}
            return _status

        base = _resolve_base_url()
        if not base:
            _status.update(online=False, base_url=None, model=None,
                           note="Foundry Local endpoint not available")
            return _status

        chat_pref = eff_chat_model()
        translate_pref = eff_translate_model()
        model = chat_pref
        try:
            client = OpenAI(base_url=base, api_key=config.FOUNDRY_API_KEY,
                            timeout=config.AI_TIMEOUT, max_retries=0)
            models = client.models.list()
            ids = [m.id for m in getattr(models, "data", [])]
            picked = _pick_chat_model(ids, _manager)
            if picked:
                model = picked
            # Ensure the chosen chat model is actually loaded (cached-but-not-
            # loaded models are listed by /v1/models but reject completions).
            _ensure_loaded(model)
            translate_model = _resolve_configured_model(ids, translate_pref) if translate_pref else model
            if translate_model and translate_model != model:
                _ensure_loaded(translate_model)
            _client, _base_url, _model, _translate_model = client, base, model, translate_model
            note = "ready"
            if ids and _NON_CHAT.search(model or ""):
                note = ("warning: selected model may not support chat. "
                        "設定画面でテキスト/チャット対応モデルを選んでください。")
            _status.update(online=True, base_url=base, model=model,
                           translate_model=translate_model,
                           note=note)
        except Exception as exc:
            _client, _base_url, _model, _translate_model = None, base, model, (translate_pref or model)
            _status.update(online=False, base_url=base, model=model,
                           translate_model=(translate_pref or model),
                           note=f"endpoint not reachable: {exc}")
        return _status


_init_thread: "threading.Thread | None" = None


def init_async() -> None:
    """Start init() in a background thread so app startup never blocks.

    Starting/probing Foundry Local can take several seconds (service spin-up,
    SDK init, model load). Doing that inside FastAPI's startup event would stop
    uvicorn from accepting requests until it finishes — the web UI would appear
    to hang. Running it in a daemon thread lets the server answer immediately;
    the UI polls /api/health and /api/ai/* for readiness.
    """
    global _init_thread
    if _init_thread is not None and _init_thread.is_alive():
        return
    _status["note"] = "AIサービスを初期化しています…"
    _init_thread = threading.Thread(target=init, daemon=True)
    _init_thread.start()


def reconnect(force_managed: bool = False) -> dict[str, Any]:
    """Reconnect AI, optionally discarding stale discovered URLs."""
    global _client, _base_url, _model
    if force_managed or (_status.get("base_url") and not _reachable(str(_status["base_url"]))):
        _client = None
        _model = None
        if config.MANAGE_FOUNDRY and not config.FOUNDRY_BASE_URL:
            _force_managed_base_url()
    return init()


def status() -> dict[str, Any]:
    st = _base_status()
    st["speech"] = speech_status()
    return st


def get_manager():
    """Expose the managed SDK manager (used for on-device transcription)."""
    return _manager


# --- Speech-to-text (Whisper via the SDK audio client) ---------------------

# Tried in order; first one the catalog resolves wins.
ASR_CANDIDATES = ["whisper-base", "whisper-small", "whisper-tiny",
                  "whisper-large-v3", "nemotron-speech-streaming-en-0.6b"]


def _sdk_manager(initialize: bool = True):
    """Return a FoundryLocalManager instance for SDK-only features.

    The OpenAI-compatible chat endpoint can be discovered without the SDK, but
    on-device transcription needs the SDK manager/catalog. If the managed web
    service was not started by this process, initialize the SDK singleton here
    so speech status and transcription can still use local models.
    """
    global _manager
    if _manager is not None:
        return _manager, None
    try:  # pragma: no cover - depends on local install
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore
    except Exception as exc:
        return None, f"sdk unavailable: {exc}"

    try:  # pragma: no cover
        manager = getattr(FoundryLocalManager, "instance", None)
        if manager is None and not initialize:
            return None, "sdk manager not initialized"
        if manager is None:
            cfg = Configuration(app_name="viveenglish")
            FoundryLocalManager.initialize(cfg)
            manager = FoundryLocalManager.instance
        if manager is not None:
            _manager = manager
            return manager, None
    except Exception as exc:
        return None, f"sdk manager unavailable: {exc}"
    return None, "sdk manager unavailable"


def _resolve_asr_model(manager):
    want = eff_transcribe_model()
    aliases = [want] + [a for a in ASR_CANDIDATES if a != want]
    for alias in aliases:
        try:
            m = _find_catalog_model(manager, alias)
            if m:
                return m
        except Exception:
            continue
    return None


def _model_label(model, fallback: str) -> str:
    return (getattr(model, "id", "") or getattr(model, "alias", "") or fallback)


def speech_status(init_manager: bool = False) -> dict[str, Any]:
    """Return SDK/Whisper readiness without downloading or loading a model."""
    mgr, err = _sdk_manager(initialize=init_manager)
    want = eff_transcribe_model()
    base = {
        "online": False,
        "sdk": not (err and err.startswith("sdk unavailable")),
        "model": want,
        "cached": False,
        "note": "",
    }
    if mgr is None:
        if err and err.startswith("sdk unavailable"):
            base["note"] = "Foundry Local SDK が見つかりません。"
        elif err == "sdk manager not initialized":
            base["note"] = "音声認識はまだ初期化されていません。設定画面で再接続または準備を実行してください。"
        else:
            base["note"] = "Foundry Local SDK の初期化に失敗しました。"
        base["detail"] = err or ""
        return base

    model = _resolve_asr_model(mgr)
    if model is None:
        base["note"] = (f"音声認識モデル {want} が見つかりません。"
                        "設定またはモデル取得状況を確認してください。")
        return base

    cached = bool(getattr(model, "is_cached", False))
    base.update(
        online=True,
        model=_model_label(model, want),
        cached=cached,
        note=("Whisper音声認識を利用できます。"
              if cached else "Whisperモデルは見つかりました。初回録音時にダウンロード/準備します。"),
    )
    return base


def _wav_pcm16(data: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.readframes(w.getnframes())
    except Exception:
        return data


def transcribe(wav_bytes: bytes) -> dict[str, Any]:
    """Transcribe a 16kHz mono WAV clip to English text, on-device.

    Returns {online, text, note}. Never raises — the UI falls back to letting
    the learner type what they said when STT is unavailable.
    """
    try:
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore  # noqa
    except Exception:
        return {"online": False, "text": "",
                "note": ("音声認識SDK(foundry-local-sdk)が見つかりません。"
                         "run.bat / run.sh で再起動するか、SDKをインストールしてください。"
                         "聞き取った内容を入力してチェックできます。"),
                "speech": speech_status()}

    mgr, mgr_err = _sdk_manager(initialize=False)
    if mgr is None and config.MANAGE_FOUNDRY:
        init()
        mgr, mgr_err = _sdk_manager(initialize=False)
    if mgr is None:
        mgr, mgr_err = _sdk_manager(initialize=True)
    if mgr is None:
        return {"online": False, "text": "",
                "note": ("ローカルAIサービスに接続できません。"
                         "設定画面のAI接続で音声認識の状態を確認してください。"),
                "detail": mgr_err or "", "speech": speech_status()}

    model = _resolve_asr_model(mgr)
    if model is None:
        return {"online": False, "text": "",
                "note": (f"音声認識モデルが見つかりません。`foundry model run {eff_transcribe_model()}` "
                         "で取得するか、環境変数 VIVE_TRANSCRIBE_MODEL を設定してください。"),
                "speech": speech_status()}
    try:
        if not getattr(model, "is_cached", False):
            model.download(lambda p: None)   # first-use download (blocking)
        model.load()
    except Exception as exc:
        return {"online": False, "text": "",
                "note": f"音声認識モデルの準備に失敗しました: {exc}",
                "speech": speech_status()}

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(wav_bytes)
        tmp.close()
        ac = model.get_audio_client()
        try:
            ac.settings.language = "en"
        except Exception:
            pass
        text = _do_transcribe(ac, tmp.name, wav_bytes)
        return {"online": True, "text": (text or "").strip(), "speech": speech_status()}
    except Exception as exc:
        return {"online": False, "text": "", "note": f"音声認識に失敗しました: {exc}",
                "speech": speech_status()}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _chunk_text(chunk) -> str:
    t = getattr(chunk, "text", None)
    if t:
        return t
    content = getattr(chunk, "content", None)
    if content:
        try:
            return content[0].text
        except Exception:
            try:
                return content[0]["text"]
            except Exception:
                return ""
    return ""


def _do_transcribe(ac, path: str, wav_bytes: bytes) -> str:
    # 1) File-based transcribe (stable SDK audio client).
    fn = getattr(ac, "transcribe", None)
    if callable(fn):
        r = fn(path)
        return getattr(r, "text", None) or (r if isinstance(r, str) else "")
    # 2) File-based streaming transcribe.
    fn = getattr(ac, "transcribe_audio_streaming", None)
    if callable(fn):
        return " ".join(_chunk_text(c) for c in fn(path)).strip()
    # 3) Live transcription session fed raw PCM (streaming ASR models).
    session = ac.create_live_transcription_session()
    try:
        session.settings.sample_rate = 16000
        session.settings.channels = 1
        session.settings.language = "en"
    except Exception:
        pass
    session.start()
    pcm = _wav_pcm16(wav_bytes)
    for i in range(0, len(pcm), 960):
        session.append(pcm[i:i + 960])
    out = []
    for result in session.get_stream():
        if getattr(result, "is_final", False):
            out.append(_chunk_text(result))
    session.stop()
    return " ".join(out).strip()


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
        _setup.update(state="checking", progress=0, phase="",
                      message="準備を確認しています…", detail="")
        _setup_thread = threading.Thread(target=_run_setup, daemon=True)
        _setup_thread.start()
        return dict(_setup)


def _run_setup() -> None:
    """Download accelerators (EPs/DLLs) and the chat model, with progress."""
    try:
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore  # noqa
    except Exception:
        current = init()
        if current.get("online"):
            _set_setup(state="ready", progress=100, phase="attached",
                       model=current.get("model"),
                       message="既存のローカルAIサービスに接続済みです。")
            return
        _set_setup(state="offline", progress=0,
                   message="AI SDK が見つかりません。オフラインのまま学習を続けられます。")
        return

    _set_setup(state="checking", progress=0, phase="init",
               message="ローカルAIを初期化しています…")
    # Make sure the managed service is up (also sets _manager).
    init()
    mgr = _manager
    if mgr is None:
        current = status()
        if current.get("online"):
            _set_setup(state="ready", progress=100, phase="attached",
                       model=current.get("model"),
                       message="既存のローカルAIサービスに接続済みです。")
            return
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
    chat_alias = eff_chat_model()
    translate_alias = eff_translate_model()
    try:
        _prepare_catalog_model(mgr, chat_alias, phase="model", label="AIモデル")
    except Exception as exc:
        _set_setup(state="error",
                   message=f"モデルの準備に失敗しました: {exc}. "
                           f"`foundry model run {chat_alias}` を一度お試しください。")
        return

    if translate_alias and translate_alias != chat_alias:
        try:
            _prepare_catalog_model(mgr, translate_alias, phase="translate",
                                   label="和訳・添削モデル")
        except Exception as exc:
            _set_setup(state="error",
                       message=f"和訳・添削モデルの準備に失敗しました: {exc}. "
                               f"`foundry model run {translate_alias}` を一度お試しください。")
            return

    # Refresh the OpenAI client so chat uses the freshly loaded model.
    init()

    # Pre-fetch the speech-to-text (Whisper) model so the first recording in
    # the speaking check doesn't trigger a slow synchronous download.
    try:
        asr = _resolve_asr_model(mgr)
        if asr is not None and not getattr(asr, "is_cached", False):
            _set_setup(state="downloading", phase="asr", progress=0,
                       message="発話認識モデル（Whisper）をダウンロード中…")
            asr.download(lambda p: _safe_progress(p))
    except Exception:
        pass  # best-effort; transcribe() will download on demand otherwise

    _set_setup(state="ready", progress=100, phase="done", message="準備が完了しました。")


def _safe_progress(p):
    try:
        _set_setup(progress=float(p))
    except Exception:
        pass


# --- Model catalog: list / download (for the settings UI) ------------------

def _model_kind(model_id: str) -> str:
    """Classify a catalog model id into 'speech' | 'chat' | 'other'."""
    if re.search(r"(whisper|speech|transcrib|nemotron-speech)", model_id, re.I):
        return "speech"
    if _NON_CHAT.search(model_id):
        return "other"
    return "chat"


def _iter_catalog(manager):
    """Yield (model_obj, id) for every model the SDK catalog exposes."""
    seen: set[str] = set()
    for name in ("list_catalog_models", "list_models", "get_models",
                 "get_cached_models", "get_loaded_models"):
        fn = getattr(manager.catalog, name, None)
        if not callable(fn):
            continue
        try:
            items = fn()
        except Exception:
            continue
        for m in items or []:
            mid = getattr(m, "id", "") or getattr(m, "alias", "")
            if mid and mid not in seen:
                seen.add(mid)
                yield m, mid


def list_models() -> dict[str, Any]:
    """Return catalog models grouped for the settings UI.

    {"online": bool, "note": str,
     "selected": {"chat":..., "translate":..., "transcribe":...},
     "current": {"chat":..., "translate":..., "transcribe":...},
     "models": [{"id","alias","task","kind","cached","loaded",
                 "selected_kinds","active_kinds"}]}
    """
    selected = {
        "chat": eff_chat_model(),
        "translate": eff_translate_model(),
        "transcribe": eff_transcribe_model(),
    }
    speech = speech_status()
    chat_active = bool(_status.get("online"))
    speech_active = bool(speech.get("online") and speech.get("cached"))
    current = {
        "chat": (_model or str(_status.get("model") or "")) if chat_active else "",
        "translate": (_translate_model or str(_status.get("translate_model") or "")) if chat_active else "",
        "transcribe": str(speech.get("model") or "") if speech_active else "",
    }
    status_snapshot = _base_status()
    status_snapshot["speech"] = speech
    mgr, err = _sdk_manager(initialize=True)
    if mgr is None:
        return {"online": False,
                "note": "Foundry Local SDK が利用できないため、モデル一覧を取得できません。",
                "detail": err or "", "selected": selected, "current": current,
                "status": status_snapshot, "models": []}

    loaded_ids: set[str] = set()
    try:
        getter = getattr(mgr.catalog, "get_loaded_models", None)
        for m in (getter() if callable(getter) else []) or []:
            mid = getattr(m, "id", "") or getattr(m, "alias", "")
            if mid:
                loaded_ids.add(mid)
    except Exception:
        pass

    models = []
    try:
        for m, mid in _iter_catalog(mgr):
            alias = getattr(m, "alias", "") or ""
            task = (getattr(m, "task", "") or getattr(m, "task_type", "") or "")
            selected_kinds = [
                kind for kind, ref in selected.items()
                if ref and _model_ref_matches(ref, mid, alias)
            ]
            active_kinds = [
                kind for kind, ref in current.items()
                if ref and _model_ref_matches(ref, mid, alias)
            ]
            models.append({
                "id": mid,
                "alias": alias,
                "task": str(task),
                "kind": _model_kind(mid),
                "cached": bool(getattr(m, "is_cached", False)),
                "loaded": any(_model_ref_matches(lid, mid, alias) for lid in loaded_ids),
                "selected_kinds": selected_kinds,
                "active_kinds": active_kinds,
            })
    except Exception as exc:
        return {"online": True, "note": f"モデル一覧の取得に失敗しました: {exc}",
                "selected": selected, "current": current,
                "status": status_snapshot, "models": []}

    models.sort(key=lambda x: (x["kind"] != "chat", not x["cached"], x["id"]))
    return {"online": True, "note": "", "selected": selected,
            "current": current, "status": status_snapshot, "models": models}


def download_model_async(alias: str, *, kind: str = "chat") -> dict[str, Any]:
    """Download a catalog model in the background.

    Reuses the _setup progress channel so the existing /api/ai/setup-state
    polling and overlay show the download. Chat/translate models are loaded only
    when they already match the configured model preference.
    """
    global _setup_thread
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("model alias is required")
    with _setup_lock:
        if _setup["state"] in ("checking", "preparing", "downloading", "loading"):
            return dict(_setup)
        if _setup_thread is not None and _setup_thread.is_alive():
            return dict(_setup)
        _setup.update(state="checking", progress=0, phase="model",
                      model=alias, message=f"{alias} を準備しています…", detail="")
        _setup_thread = threading.Thread(
            target=_run_download, args=(alias, kind), daemon=True)
        _setup_thread.start()
        return dict(_setup)


def _find_catalog_model(manager, ident: str):
    """Resolve a catalog model by full variant id OR short alias.

    The SDK splits these: ``catalog.get_model(alias)`` resolves a short alias
    (e.g. ``qwen2.5-1.5b``) and returns a multi-variant Model, while
    ``catalog.get_model_variant(id)`` resolves a full variant id (e.g.
    ``qwen2.5-1.5b-instruct-generic-cpu:4``). We try the variant lookup first
    because the settings UI sends full ids, then fall back to the alias form.
    """
    cat = manager.catalog
    # 1) Exact variant-id lookup (handles the full '...-cpu:4' ids from the UI).
    getv = getattr(cat, "get_model_variant", None)
    if callable(getv):
        try:
            m = getv(ident)
            if m is not None:
                return m
        except Exception:
            pass
    # 2) Short-alias lookup.
    try:
        m = cat.get_model(ident)
        if m is not None:
            return m
    except Exception:
        pass
    # 3) Scan the catalog matching id/alias (and a base alias without ':<n>').
    key = (ident or "").lower()
    base = key.split(":", 1)[0]
    for m, mid in _iter_catalog(manager):
        if _model_ref_matches(key, mid, getattr(m, "alias", "") or ""):
            return m
    # 4) Last resort: alias form derived from the base id.
    if base and base != key:
        try:
            return cat.get_model(base)
        except Exception:
            pass
    return None


def _run_download(alias: str, kind: str) -> None:
    """Background worker for download_model_async."""
    mgr, err = _sdk_manager(initialize=True)
    if mgr is None:
        _set_setup(state="error",
                   message=f"ローカルAIサービスに接続できません: {err or ''}")
        return
    label = {"chat": "AIモデル", "translate": "和訳・添削モデル",
             "transcribe": "音声認識モデル"}.get(kind, "AIモデル")
    try:
        model = _find_catalog_model(mgr, alias)
        if model is None:
            _set_setup(state="error",
                       message=f"{label}「{alias}」がカタログに見つかりませんでした。"
                               "モデル名を確認してください。")
            return
        mid = getattr(model, "id", "") or alias
        model_alias = getattr(model, "alias", "") or alias
        if not getattr(model, "is_cached", False):
            _set_setup(state="downloading", phase="model", progress=0, model=mid,
                       message=f"{label}（{alias}）をダウンロード中…")
            model.download(lambda p: _safe_progress(p))
        selected_refs = [eff_chat_model(), eff_translate_model()]
        should_load = (
            kind in ("chat", "translate") and _model_kind(mid) != "speech"
            and any(_model_ref_matches(ref, mid, model_alias)
                    for ref in selected_refs if ref)
        )
        if should_load:
            _set_setup(state="loading", phase="model", progress=100, model=mid,
                       message=f"{label}を読み込んでいます…")
            model.load()
    except Exception as exc:
        _set_setup(state="error",
                   message=f"{label}の準備に失敗しました: {exc}. "
                           f"`foundry model run {alias}` を一度お試しください。")
        return
    # Refresh the chat client only when the download was already the selected
    # model. Downloading a catalog item should not silently switch the app.
    if kind in ("chat", "translate") and locals().get("should_load"):
        init()
    _set_setup(state="ready", progress=100, phase="done",
               message=f"{label}（{alias}）の準備が完了しました。")


def _chat(messages: list[dict[str, str]], *, temperature: float = 0.4,
          max_tokens: int = 512, json_mode: bool = False,
          model: str | None = None) -> str | None:
    """Low-level chat call. Returns None when offline so callers can fall back."""
    if _client is None:
        init()
    if _client is None:
        return None
    base: dict[str, Any] = dict(model=(model or _model), messages=messages,
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
        model=(_translate_model or config.TRANSLATE_MODEL or None),
    )
    if raw:
        parsed = _safe_json(raw)
        if parsed and parsed.get("translation"):
            if mode != "word" and _looks_untranslated(text, str(parsed.get("translation", ""))):
                return _translation_failed(text, "モデルが原文を返したため、和訳としては表示しません。")
            parsed.setdefault("note", "")
            return {"source": text, "online": True, **parsed}
        # Model returned plain text (no JSON): use it directly as the translation.
        cleaned = _plain_text(raw)
        if cleaned:
            if mode != "word" and _looks_untranslated(text, cleaned):
                return _translation_failed(text, "モデルが原文を返したため、和訳としては表示しません。")
            return {"source": text, "translation": cleaned, "note": "", "online": True}

    if glossary:
        gloss = glossary.get(text.lower().strip(".,!?\"'"))
        if gloss:
            return {"source": text, "translation": gloss, "note": "",
                    "online": False, "offline_fallback": True}
    return {"source": text, "translation": "(AIオフライン: 訳を取得できませんでした)",
            "note": "Foundry Local を起動すると和訳が利用できます。",
            "online": False, "offline_fallback": True}


def _translation_failed(source: str, note: str) -> dict[str, Any]:
    extra = (" VIVE_TRANSLATE_MODEL に日本語対応の大きめのチャットモデルを指定すると改善できます。"
             if not config.TRANSLATE_MODEL else f" 現在の翻訳モデル: {config.TRANSLATE_MODEL}")
    return {"source": source, "translation": "", "note": note + extra,
            "online": False, "translation_failed": True}


def _looks_untranslated(source: str, translated: str) -> bool:
    src = _norm_text(source)
    out = _norm_text(translated)
    if not src or not out:
        return False
    if src == out:
        return True
    # Japanese translations should normally contain Japanese characters. If the
    # model emits mostly ASCII and shares most words with the source, it copied.
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", translated):
        return False
    src_words = set(re.findall(r"[a-z']+", src.lower()))
    out_words = set(re.findall(r"[a-z']+", out.lower()))
    if len(src_words) >= 3 and src_words:
        overlap = len(src_words & out_words) / max(1, len(src_words))
        return overlap >= 0.72
    return False


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().strip('"“”')).strip()


_JP_CHARS = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")


def _is_mostly_japanese(s: str) -> bool:
    """True when the text is predominantly Japanese (kana/kanji) rather than English.

    Used to detect when a model put Japanese in the English-only ``story`` field
    (or swapped the English/Japanese fields).
    """
    s = s or ""
    jp = len(_JP_CHARS.findall(s))
    latin = len(re.findall(r"[A-Za-z]", s))
    if jp == 0:
        return False
    return jp >= latin


def tutor_reply(history: list[dict[str, str]], scenario: str = "",
                level: str = "beginner", name: str = "Vivi",
                gender: str = "female") -> dict[str, Any]:
    """Conversation partner that replies in English and gently corrects."""
    g = "male" if str(gender).lower().startswith("m") else "female"
    sys = (
        f"You are '{name}', a warm, encouraging {g} English conversation partner for a "
        f"Japanese {level} learner. Stay strictly in the role-play scenario: {scenario or 'free talk'}. "
        "Rules: (1) Reply in short, natural English suited to the learner's level. "
        "(2) Always provide reply_ja, a natural Japanese translation of your English reply. "
        "(3) Check the learner's latest English carefully for spelling, word choice, grammar, "
        "missing words, unnatural phrasing, or Japanese-English literal wording. If there is any "
        "issue, put a concise Japanese explanation in correction, including the better phrase. "
        "If it is fully natural, set correction to an empty string. "
        "(4) Always include one short, practical Japanese tip in tip. "
        "(5) Always end the English reply with a simple follow-up question to keep the conversation going. "
        "Return ONLY JSON: {\"reply\":\"English reply\", \"reply_ja\":\"返信の和訳\", "
        "\"correction\":\"訂正や改善点(無ければ空文字)\", \"tip\":\"短い学習ヒント\"}."
    )
    messages = [{"role": "system", "content": sys}] + history
    raw = _chat(messages, temperature=0.6, max_tokens=400, json_mode=True)
    learner_text = _last_user_text(history)
    if raw:
        parsed = _safe_json(raw)
        if parsed and parsed.get("reply"):
            for k in ("reply_ja", "correction", "tip"):
                parsed.setdefault(k, "")
            parsed["reply_ja"] = _ensure_reply_ja(parsed["reply"], parsed.get("reply_ja", ""))
            review = _review_learner_text(learner_text, level)
            if review.get("correction") and not parsed.get("correction"):
                parsed["correction"] = review["correction"]
            if review.get("tip") and not parsed.get("tip"):
                parsed["tip"] = review["tip"]
            return {"online": True, **parsed}
        # Plain-text reply (model didn't emit JSON): use it as Vivi's reply.
        cleaned = _plain_text(raw)
        if cleaned:
            review = _review_learner_text(learner_text, level)
            return {"online": True, "reply": cleaned, "reply_ja": _ensure_reply_ja(cleaned, ""),
                    "correction": review.get("correction", ""), "tip": review.get("tip", "")}
    return {
        "online": False,
        "reply": "(AI offline) Let's keep practising! Start Foundry Local to chat with Vivi.",
        "reply_ja": "(AIオフライン) Foundry Local を起動すると会話できます。",
        "correction": "", "tip": "",
    }


def _last_user_text(history: list[dict[str, str]]) -> str:
    for msg in reversed(history or []):
        if msg.get("role") == "user":
            return (msg.get("content") or "").strip()
    return ""


def _ensure_reply_ja(reply: str, current: str = "") -> str:
    if (current or "").strip() and not _looks_untranslated(reply, current):
        return current.strip()
    if not (reply or "").strip():
        return ""
    tr = translate(reply, "sentence")
    text = (tr.get("translation") or "").strip()
    if text and not text.startswith("(AIオフライン"):
        return text
    note = tr.get("note") or "和訳を生成できませんでした。"
    return f"（{note}）"


def _review_learner_text(text: str, level: str = "beginner") -> dict[str, str]:
    text = (text or "").strip()
    if not text:
        return {"correction": "", "tip": ""}

    local = _local_chat_feedback(text)
    sys = (
        "You are an English writing coach for a Japanese learner. Review ONLY the learner's latest message. "
        "Find spelling mistakes, wrong word choice, grammar mistakes, missing articles/prepositions, or unnatural phrasing. "
        "If there is any issue, give one concise Japanese correction with a better English phrase. "
        "If the sentence is natural and correct, set correction to an empty string. "
        "Return ONLY JSON: {\"correction\":\"日本語の訂正説明。Better: ...\", \"tip\":\"短い日本語ヒント\"}."
    )
    raw = _chat(
        [{"role": "system", "content": sys},
         {"role": "user", "content": f"LEVEL: {level}\nLEARNER: {text}"}],
        temperature=0.1, max_tokens=240, json_mode=True,
        model=(_translate_model or config.TRANSLATE_MODEL or None),
    )
    parsed = _safe_json(raw or "")
    correction = (parsed or {}).get("correction", "")
    tip = (parsed or {}).get("tip", "")
    correction = str(correction or "").strip()
    tip = str(tip or "").strip()
    if local.get("correction"):
        if correction:
            correction = f"{local['correction']} / {correction}"
        else:
            correction = local["correction"]
    if local.get("tip") and not tip:
        tip = local["tip"]
    return {"correction": correction, "tip": tip}


def _local_chat_feedback(text: str) -> dict[str, str]:
    low = text.lower()
    checks = [
        (r"\bgoed\b", "goed は不規則動詞なので went が自然です。Better: I went ..."),
        (r"\beated\b", "eated ではなく ate を使います。Better: I ate ..."),
        (r"\bbuyed\b", "buyed ではなく bought を使います。Better: I bought ..."),
        (r"\bspeaked\b", "speaked ではなく spoke を使います。Better: I spoke ..."),
        (r"\bwrited\b", "writed ではなく wrote を使います。Better: I wrote ..."),
        (r"\bteached\b", "teached ではなく taught を使います。Better: taught"),
        (r"\bstuding\b", "studing は綴りが違います。Better: studying"),
        (r"\bbecouse\b", "becouse は綴りが違います。Better: because"),
        (r"\bfreind\b", "freind は綴りが違います。Better: friend"),
        (r"\brecieve\b", "recieve は綴りが違います。Better: receive"),
        (r"\bi am agree\b", "I am agree ではなく I agree が自然です。"),
        (r"\bi very like\b", "I very like ではなく I really like が自然です。"),
        (r"\bpeoples\b", "people は通常それ自体で複数扱いです。Better: people"),
        (r"\binformations\b", "information は数えられない名詞です。Better: information"),
        (r"\badvices\b", "advice は数えられない名詞です。Better: advice"),
    ]
    for pat, correction in checks:
        if re.search(pat, low):
            return {"correction": correction, "tip": "不規則動詞や数えられない名詞は、形が変わりやすいので注意しましょう。"}
    return {"correction": "", "tip": ""}


def chat_suggestions(history: list[dict[str, str]], scenario: str = "",
                     level: str = "beginner") -> dict[str, Any]:
    """Generate short candidate replies when the learner is stuck."""
    sys = (
        "You help a Japanese English learner continue a role-play conversation. "
        f"Scenario: {scenario or 'free talk'}. Learner level: {level}. "
        "Create 3 short, natural English replies the learner can choose from. "
        "Keep them easy to say aloud and relevant to the latest assistant message. "
        "Return ONLY JSON: {\"suggestions\":[{\"en\":\"English sentence\","
        "\"ja\":\"自然な日本語訳\",\"note\":\"短い使いどころ\"}]}."
    )
    raw = _chat(
        [{"role": "system", "content": sys}] + history[-10:],
        temperature=0.5, max_tokens=420, json_mode=True,
    )
    if raw:
        parsed = _safe_json(raw)
        items = parsed.get("suggestions") if parsed else None
        if isinstance(items, list):
            clean = []
            for item in items[:3]:
                if isinstance(item, dict) and item.get("en"):
                    clean.append({
                        "en": str(item.get("en", "")).strip(),
                        "ja": str(item.get("ja", "")).strip(),
                        "note": str(item.get("note", "")).strip(),
                    })
            if clean:
                return {"online": True, "suggestions": clean}
    return {
        "online": False,
        "suggestions": [
            {"en": "Could you say that again?", "ja": "もう一度言ってもらえますか？", "note": "聞き返したいとき"},
            {"en": "Let me think for a moment.", "ja": "少し考えさせてください。", "note": "返答に迷ったとき"},
            {"en": "I think so, but I'm not sure.", "ja": "そう思いますが、確信はありません。", "note": "やわらかく意見を言うとき"},
        ],
    }


# --- Reading passage generation -------------------------------------------
# The reading assistant can analyse pasted text without AI, but generating a
# fresh longer passage is useful practice material when a local chat model is on.

_READING_LENGTHS = {
    "medium": {
        "paragraphs": 2,
        "words": "about 170 to 220 words total",
        "roles": "Paragraph 1 introduces the topic. Paragraph 2 explains examples/results and ends with a short conclusion.",
    },
    "long": {
        "paragraphs": 3,
        "words": "about 260 to 340 words total",
        "roles": "Paragraph 1 introduces the topic. Paragraph 2 develops reasons/examples. Paragraph 3 explains results and concludes.",
    },
    "exam": {
        "paragraphs": 3,
        "words": "about 300 to 360 words total",
        "roles": "Paragraph 1 states the issue/claim. Paragraph 2 gives evidence or a counterexample. Paragraph 3 develops the logical conclusion.",
    },
}

_READING_ANGLES = [
    {
        "genre": "a school magazine article",
        "angle": "a small conflict between convenience and responsibility",
        "structure": "problem, concrete example, lesson learned",
    },
    {
        "genre": "a short opinion essay",
        "angle": "an unexpected benefit that appears after a challenge",
        "structure": "claim, two reasons, cautious conclusion",
    },
    {
        "genre": "a narrative report",
        "angle": "one person's change of mind after meeting someone",
        "structure": "situation, turning point, result",
    },
    {
        "genre": "an explainer for young readers",
        "angle": "how a hidden cause creates a visible result",
        "structure": "question, cause-and-effect chain, advice",
    },
    {
        "genre": "a community newsletter column",
        "angle": "how different people solve the same problem in different ways",
        "structure": "introduction, comparison, conclusion",
    },
    {
        "genre": "an exam-style reading passage",
        "angle": "why a common belief is only partly true",
        "structure": "common belief, counterexample, balanced conclusion",
    },
]


def generate_reading_passage(topic: str = "", level: str = "beginner",
                             length: str = "medium") -> dict[str, Any]:
    """Generate an English passage for the reading-support screen.

    Returns {online, title, passage, passage_ja, note}. The fallback sample is
    deliberately structured so the frontend can still demonstrate every layer.
    """
    topic_txt = re.sub(r"\s+", " ", str(topic or "")).strip() or "technology and daily life"
    plan = _READING_LENGTHS.get(length, _READING_LENGTHS["medium"])
    para_count = int(plan["paragraphs"])
    angle = random.choice(_READING_ANGLES)
    seed = random.randint(1000, 9999)
    sys = (
        "You are an English reading-material writer for Japanese learners. "
        f"Write EXACTLY {para_count} paragraphs ({plan['words']}) for a {level} learner "
        f"about \"{topic_txt}\" as {angle['genre']}. "
        f"Required paragraph roles: {plan['roles']} "
        f"Use this unique angle: {angle['angle']}. "
        f"Use this paragraph structure: {angle['structure']}. "
        f"Variation seed: {seed}. Do not reuse stock examples about phones, studying desks, "
        "or generic daily habits unless the topic explicitly asks for them. "
        "Include varied sentence patterns (SV, SVC, SVO, SVOO, SVOC), connectors, "
        "pronouns, reasons, causes, results, and demonstratives. "
        "Make the situation, examples, nouns, and conclusion meaningfully different each time. "
        "The passage must be ENGLISH ONLY. Return ONLY JSON: "
        "{\"title\":\"short English title\", \"passage\":\"English passage\", "
        "\"passage_paragraphs\":[\"paragraph 1\", \"paragraph 2\"], "
        "\"passage_ja\":\"natural Japanese translation of the whole passage\"}."
        f" The passage_paragraphs array MUST contain exactly {para_count} English strings. "
        "Separate paragraphs in passage with a blank line."
    )
    raw = _chat(
        [{"role": "system", "content": sys},
         {"role": "user", "content": (
             f"Topic: {topic_txt}\nLevel: {level}\nLength: {length}\n"
             f"Genre: {angle['genre']}\nAngle: {angle['angle']}\n"
             f"Structure: {angle['structure']}\nSeed: {seed}"
         )}],
        temperature=0.85, max_tokens=1200, json_mode=True,
    )
    if raw:
        parsed = _safe_json(raw)
        if parsed and (parsed.get("passage") or parsed.get("passage_paragraphs")):
            para_items = parsed.get("passage_paragraphs")
            passage = _reading_paragraphs_to_text(para_items) if isinstance(para_items, list) else ""
            if not passage:
                passage = _plain_text(str(parsed.get("passage", "")))
            passage = _ensure_reading_paragraphs(passage, para_count)
            passage_ja = str(parsed.get("passage_ja", "")).strip()
            if passage and not _JP_CHARS.search(passage):
                if not passage_ja or _looks_untranslated(passage, passage_ja):
                    passage_ja = _ensure_reply_ja(passage, "")
                return {"online": True,
                        "title": str(parsed.get("title", "")).strip(),
                        "passage": passage, "passage_ja": passage_ja, "note": ""}
        cleaned = _plain_text(raw)
        if cleaned and not _JP_CHARS.search(cleaned):
            cleaned = _ensure_reading_paragraphs(cleaned, para_count)
            return {"online": True, "title": topic_txt.title(), "passage": cleaned,
                    "passage_ja": _ensure_reply_ja(cleaned, ""), "note": ""}

    fallback_topic = topic_txt if not _JP_CHARS.search(topic_txt) else "daily learning"
    sample = _fallback_reading_passage(fallback_topic, length=length)
    return {"online": False, **sample,
            "note": "AIオフラインのため、構造が見えやすいサンプル英文を表示しています。"}


def _reading_paragraphs_to_text(value: list[Any]) -> str:
    parts = []
    for item in value:
        text = _plain_text(str(item or ""))
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _ensure_reading_paragraphs(text: str, target: int) -> str:
    text = _plain_text(text)
    if target <= 1 or not text:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) == target:
        return "\n\n".join(paragraphs)
    if len(paragraphs) > target:
        head = paragraphs[:target - 1]
        tail = " ".join(paragraphs[target - 1:])
        return "\n\n".join(head + [tail])

    sentences = [s.strip() for s in re.findall(r"[^.!?]+(?:[.!?]+|$)", text) if s.strip()]
    if len(sentences) < target:
        return "\n\n".join(paragraphs) if paragraphs else text
    groups = []
    base = len(sentences) // target
    extra = len(sentences) % target
    pos = 0
    for i in range(target):
        take = base + (1 if i < extra else 0)
        groups.append(" ".join(sentences[pos:pos + take]))
        pos += take
        extra -= 1
    return "\n\n".join(g for g in groups if g.strip())


def _fallback_reading_passage(topic: str, length: str = "long") -> dict[str, str]:
    title = "A Small Change with a Big Effect"
    paragraphs = [
        f"Many people think about {topic} only when a problem appears, but small daily choices often shape the result. "
        "For example, a student may put a phone in another room before studying. "
        "This simple action makes the desk quieter and gives the student a better chance to focus. "
        "Because there are fewer interruptions, the first ten minutes become easier, and that beginning often leads to deeper work.",
        "However, the change does not help everyone in the same way. "
        "Some learners need music, while others need silence. "
        "A teacher can show students several methods and call the best method a personal routine. "
        "When students test those methods, they find the routine useful and keep it for a longer time.",
        "Therefore, the most important point is not to copy another person's habit blindly. "
        "People should notice what helps them, choose one small action, and repeat it. "
        "In conclusion, steady attention to cause and result can turn an ordinary habit into real progress.",
    ]
    count = int(_READING_LENGTHS.get(length, _READING_LENGTHS["long"])["paragraphs"])
    passage = "\n\n".join(paragraphs[:count])
    passage_ja = (
        f"多くの人は問題が起きたときだけ{topic}について考えますが、日々の小さな選択が結果を形作ることがよくあります。"
        "たとえば、学生が勉強前にスマートフォンを別の部屋に置くことがあります。"
        "この単純な行動は机を静かにし、集中する機会を増やします。"
        "邪魔が少ないので最初の10分が楽になり、その始まりがより深い作業につながることがよくあります。"
    )
    return {"title": title, "passage": passage, "passage_ja": passage_ja}


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


# --- Vocabulary story generation -------------------------------------------
# Turn the learner's chosen words and phrases into a short, themed passage so
# they can see target vocabulary used naturally in context.

_STORY_FORMATS = {
    "story": "a short, coherent story",
    "dialogue": "a short, natural dialogue between two people (prefix each line "
                "with the speaker's name and a colon)",
    "diary": "a first-person diary entry",
    "email": "a short, friendly email (with a greeting and sign-off)",
}
_STORY_FORMATS_JA = {
    "story": "物語", "dialogue": "会話", "diary": "日記", "email": "メール",
}


def _force_english_story(words: list[str], theme: str, level: str,
                         fmt_desc: str, len_desc: str) -> str:
    """Second-pass generation that returns ONLY an English passage (no JSON).

    Used when the first JSON attempt put Japanese in the English ``story`` field
    or skipped required target terms.
    """
    required_lines = "\n".join(f"- {w}" for w in words)
    missing_hint = ""
    for attempt in range(2):
        sys = (
            f"Write {fmt_desc} ({len_desc}) in ENGLISH ONLY for a Japanese {level} "
            f"English learner, on the theme \"{theme}\". You MUST include every "
            "required term exactly as written at least once. Do not translate, "
            "inflect, replace, or omit any required term. Output ONLY the English "
            "passage as plain text: no Japanese, no labels, no JSON, no quotes."
        )
        if attempt:
            sys += missing_hint or " This is a retry because the previous answer missed required terms."
        raw = _chat(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"Required terms:\n{required_lines}\nTheme: {theme}"}],
            temperature=0.2, max_tokens=800,
        )
        text = _plain_text(raw or "")
        if _valid_story_text(text, words):
            return text
        missing = _story_missing_terms(text, words)
        if missing:
            missing_hint = " This is a retry. The previous answer missed: " + ", ".join(missing) + "."
    return ""


_STORY_LENGTHS = {
    "short": "2 to 3 sentences",
    "medium": "a short paragraph of 4 to 6 sentences",
    "long": "two short paragraphs",
}


def generate_story(words: list[str], theme: str = "", level: str = "beginner",
                   fmt: str = "story", length: str = "short") -> dict[str, Any]:
    """Write a short, themed English passage that uses the learner's terms.

    Returns {online, title, story, story_ja, used_words, vocab_notes, note}.
    Falls back to a clear OFFLINE notice (rest of the UI keeps working).
    """
    seen_words = set()
    clean_words = []
    for raw in words or []:
        word = re.sub(r"\s+", " ", str(raw)).strip()
        key = word.lower()
        if word and key not in seen_words:
            seen_words.add(key)
            clean_words.append(word)
    words = clean_words
    if not words:
        return {"online": _status["online"], "title": "", "story": "",
                "story_ja": "", "used_words": [], "vocab_notes": [],
                "note": "単語やフレーズを1つ以上選んでください。"}

    fmt_desc = _STORY_FORMATS.get(fmt, _STORY_FORMATS["story"])
    len_desc = _STORY_LENGTHS.get(length, _STORY_LENGTHS["short"])
    theme_txt = (theme or "").strip() or "an everyday situation"
    word_list = ", ".join(words)
    required_lines = "\n".join(f"- {w}" for w in words)

    sys = (
        f"You are an English teacher writing practice material for a Japanese "
        f"{level} learner. Write {fmt_desc} ({len_desc}) on the theme: "
        f"\"{theme_txt}\". You MUST use EVERY required term naturally and "
        f"correctly: {word_list}. Copy each required term exactly as written at "
        "least once in the English story. Do not translate, inflect, replace, or "
        "omit required terms. Keep the English natural and suited to the "
        "learner's level, and make the theme clearly recognisable. "
        "CRITICAL: the \"story\" field MUST be written in ENGLISH only — never in "
        "Japanese, and not mixed Japanese/English. The \"story_ja\" field is the "
        "Japanese translation of that "
        "English passage. Do not swap them. "
        "Return ONLY JSON: {\"title\":\"a short English title\", "
        "\"story\":\"the passage IN ENGLISH\", "
        "\"story_ja\":\"その英文の全文の自然な和訳\", "
        "\"used_words\":[\"words you actually used\"], "
        "\"vocab_notes\":[{\"en\":\"word\",\"ja\":\"この文脈での意味\"}]}."
    )
    raw = _chat(
        [{"role": "system", "content": sys},
         {"role": "user", "content": f"Required terms:\n{required_lines}\nTheme: {theme_txt}"}],
        temperature=0.4, max_tokens=1000, json_mode=True,
    )
    if raw:
        parsed = _safe_json(raw)
        if parsed and parsed.get("story"):
            story = str(parsed.get("story", "")).strip()
            story_ja = str(parsed.get("story_ja", "")).strip()
            # Small models sometimes swap the fields (English in story_ja, Japanese
            # in story). If story is mostly Japanese but story_ja is mostly English,
            # un-swap them so the learner always sees an English passage.
            if _is_mostly_japanese(story) and story_ja and not _is_mostly_japanese(story_ja):
                story, story_ja = story_ja, story
            # If story still contains Japanese or misses required terms,
            # regenerate plainly in English so we never show invalid practice text.
            if not _valid_story_text(story, words):
                regenerated = _force_english_story(words, theme_txt, level, fmt_desc, len_desc)
                if regenerated:
                    story, story_ja = regenerated, ""
            if not _valid_story_text(story, words):
                return _story_generation_failed(words, story)
            if not story_ja or _looks_untranslated(story, story_ja):
                story_ja = _ensure_reply_ja(story, "")
            notes = parsed.get("vocab_notes")
            clean_notes = []
            if isinstance(notes, list):
                for n in notes:
                    if isinstance(n, dict) and n.get("en"):
                        clean_notes.append({"en": str(n.get("en", "")).strip(),
                                            "ja": str(n.get("ja", "")).strip()})
            used = parsed.get("used_words")
            used = [str(u).strip() for u in used if str(u).strip()] if isinstance(used, list) else words
            return {"online": True, "title": str(parsed.get("title", "")).strip(),
                    "story": story, "story_ja": story_ja,
                    "used_words": used or words, "vocab_notes": clean_notes, "note": ""}
        cleaned = _plain_text(raw)
        if cleaned:
            if not _valid_story_text(cleaned, words):
                cleaned = _force_english_story(words, theme_txt, level, fmt_desc, len_desc) or cleaned
            if not _valid_story_text(cleaned, words):
                return _story_generation_failed(words, cleaned)
            return {"online": True, "title": "", "story": cleaned,
                    "story_ja": _ensure_reply_ja(cleaned, ""),
                    "used_words": words, "vocab_notes": [], "note": ""}

    return {"online": False, "title": "", "story": "", "story_ja": "",
            "used_words": words, "vocab_notes": [],
            "note": "AIオフラインのため文章を生成できません。Foundry Local を起動すると、"
                    "指定した単語やフレーズを使ったオリジナルの文章を作れます。"}


def _valid_story_text(story: str, required_terms: list[str]) -> bool:
    """True only for English-only story text that includes all required terms."""
    story = (story or "").strip()
    if not story:
        return False
    # The story field is practice English. Any Japanese character here means the
    # model ignored the contract, even if the text is not "mostly" Japanese.
    if _JP_CHARS.search(story):
        return False
    return not _story_missing_terms(story, required_terms)


def _story_generation_failed(required_terms: list[str], story: str = "") -> dict[str, Any]:
    missing = _story_missing_terms(story, required_terms)
    if missing:
        detail = " 未使用: " + ", ".join(missing[:6])
    else:
        detail = ""
    return {"online": True, "title": "", "story": "", "story_ja": "",
            "used_words": [], "vocab_notes": [],
            "note": "指定した単語やフレーズをすべて含む英語の文章を生成できませんでした。"
                    "もう一度生成してください。" + detail}


def _story_missing_terms(story: str, required_terms: list[str]) -> list[str]:
    text = _story_match_text(story)
    missing = []
    for term in required_terms:
        term_txt = _story_match_text(term)
        if term_txt and not _story_contains_term(text, term_txt):
            missing.append(term)
    return missing


def _story_match_text(value: str) -> str:
    text = _norm_text(str(value or "")).lower()
    return (text.replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))


def _story_contains_term(text: str, term: str) -> bool:
    pattern = re.escape(term).replace(r"\ ", r"\s+")
    if re.match(r"[a-z0-9']", term):
        pattern = r"(?<![a-z0-9'])" + pattern
    if re.search(r"[a-z0-9']$", term):
        pattern += r"(?![a-z0-9'])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


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
