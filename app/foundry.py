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
import time
import wave
from typing import Any

from . import config, database

_client = None
_base_url: str | None = None
_model: str | None = None
_translate_model: str | None = None
_manager = None  # live foundry-local-sdk manager (when managed)
_status: dict[str, Any] = {"online": False, "provider": "foundry",
                           "provider_label": "Foundry Local",
                           "base_url": None, "model": None,
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
    st = dict(_status)
    provider = eff_ai_provider()
    st["provider"] = provider
    st["provider_label"] = _provider_label(provider)
    if provider != "foundry" and not st.get("base_url"):
        st["base_url"] = eff_ai_base_url() or None
    return st


# --- User-selected model preferences (persisted in profile.settings) --------
# The settings UI lets the learner pick which cached model to use for chat,
# translation, and speech-to-text. These override the config/env defaults.

def _prefs() -> dict[str, Any]:
    try:
        return database.get_profile().get("settings") or {}
    except Exception:
        return {}


_PROVIDER_LABELS = {
    "foundry": "Foundry Local",
    "ollama": "Ollama",
    "openai": "OpenAI互換",
}


def _provider_key(value: str | None) -> str:
    key = (value or "").strip().lower().replace("-", "_")
    if key in ("ollama",):
        return "ollama"
    if key in ("openai", "custom", "custom_openai", "openai_compatible",
               "openai_compat", "compatible"):
        return "openai"
    return "foundry"


def _provider_label(provider: str | None) -> str:
    return _PROVIDER_LABELS.get(_provider_key(provider), "Foundry Local")


def eff_ai_provider() -> str:
    return _provider_key(_prefs().get("ai_provider") or config.AI_PROVIDER)


def eff_ai_base_url() -> str:
    provider = eff_ai_provider()
    prefs = _prefs()
    saved = (prefs.get("ai_base_url") or "").strip()
    if provider == "ollama":
        return saved or config.AI_BASE_URL or config.OLLAMA_BASE_URL
    if provider == "openai":
        return saved or config.AI_BASE_URL
    return ""


def eff_ai_api_key() -> str:
    provider = eff_ai_provider()
    saved = (_prefs().get("ai_api_key") or "").strip()
    if provider == "foundry":
        return saved or config.FOUNDRY_API_KEY
    if provider == "ollama":
        return saved or config.AI_API_KEY or "ollama"
    return saved or config.AI_API_KEY


def provider_settings() -> dict[str, Any]:
    provider = eff_ai_provider()
    base = eff_ai_base_url()
    stored_key = (_prefs().get("ai_api_key") or "").strip()
    env_key = "" if config.AI_API_KEY == "notneeded" else (config.AI_API_KEY or "").strip()
    return {
        "provider": provider,
        "provider_label": _provider_label(provider),
        "base_url": base,
        "default_base_url": config.OLLAMA_BASE_URL if provider == "ollama" else config.AI_BASE_URL,
        "has_api_key": bool(stored_key or env_key),
        "status": status(),
    }


def set_ai_provider(provider: str, base_url: str | None = None,
                    api_key: str | None = None,
                    chat_model: str | None = None,
                    translate_model: str | None = None) -> dict[str, Any]:
    provider = _provider_key(provider)
    settings = _prefs()
    settings["ai_provider"] = provider
    if base_url is not None:
        base_url = base_url.strip()
        if base_url:
            settings["ai_base_url"] = _normalize(base_url)
        else:
            settings.pop("ai_base_url", None)
    if api_key is not None:
        api_key = api_key.strip()
        if api_key:
            settings["ai_api_key"] = api_key
        else:
            settings.pop("ai_api_key", None)
    # Model choices are scoped to the provider they were made for so switching
    # providers never carries a model that only exists on the other one.
    if chat_model is not None or translate_model is not None:
        store = _provider_model_store(settings, provider, create=True)
        if chat_model is not None:
            store["chat_model"] = chat_model.strip()
        if translate_model is not None:
            store["translate_model"] = translate_model.strip()
    database.update_profile(settings=settings)
    return reconnect()


# --- Per-provider model preferences ----------------------------------------
# Each provider (foundry/ollama/openai) keeps its OWN chat/translate/transcribe
# model choice under settings["models_by_provider"][provider]. Storing them
# globally used to leak a model from one provider into another (e.g. an Ollama
# tag selected while on Foundry), which then asked the new provider for a model
# it does not have.

def _provider_model_store(settings: dict[str, Any], provider: str,
                          *, create: bool = False) -> dict[str, Any]:
    by = settings.get("models_by_provider")
    if not isinstance(by, dict):
        if not create:
            return {}
        by = {}
        settings["models_by_provider"] = by
    store = by.get(provider)
    if not isinstance(store, dict):
        if not create:
            return {}
        store = {}
        by[provider] = store
    return store


def _model_pref(key: str, provider: str | None = None) -> str:
    provider = provider or eff_ai_provider()
    prefs = _prefs()
    val = (_provider_model_store(prefs, provider).get(key) or "").strip()
    if val:
        return val
    # Legacy top-level prefs predate per-provider scoping. Treat them as the
    # historical default provider (foundry) so existing setups keep working,
    # but never expose them to a different provider.
    if provider == "foundry":
        return (prefs.get(key) or "").strip()
    return ""


def eff_chat_model() -> str:
    return _model_pref("chat_model") or config.CHAT_MODEL


def eff_translate_model() -> str:
    """Translate model preference; empty falls back to the chat model."""
    return _model_pref("translate_model") or config.TRANSLATE_MODEL


def eff_transcribe_model() -> str:
    return _model_pref("transcribe_model") or config.TRANSCRIBE_MODEL


def set_model_preference(kind: str, alias: str | None) -> dict[str, Any]:
    """Persist a model choice ('chat'|'translate'|'transcribe') and reconnect."""
    key = {"chat": "chat_model", "translate": "translate_model",
           "transcribe": "transcribe_model"}.get(kind)
    if not key:
        raise ValueError(f"unknown model kind: {kind}")
    settings = _prefs()
    provider = eff_ai_provider()
    store = _provider_model_store(settings, provider, create=True)
    store[key] = (alias or "").strip()
    # Drop any legacy global value so it can no longer shadow the scoped choice.
    settings.pop(key, None)
    database.update_profile(settings=settings)
    # Reconnect so chat/translate immediately use the newly chosen model.
    if kind in ("chat", "translate"):
        reconnect()
    return status()


def _clear_foundry_preferences(model_id: str, alias: str) -> list[str]:
    """Clear saved Foundry model choices that point at a removed model."""
    settings = _prefs()
    store = _provider_model_store(settings, "foundry")
    cleared: list[str] = []
    for kind, key in (("chat", "chat_model"), ("translate", "translate_model"),
                      ("transcribe", "transcribe_model")):
        val = (store.get(key) or "").strip()
        if val and _model_ref_matches(val, model_id, alias):
            store[key] = ""
            cleared.append(kind)
        legacy = (settings.get(key) or "").strip()
        if legacy and _model_ref_matches(legacy, model_id, alias):
            settings.pop(key, None)
            if kind not in cleared:
                cleared.append(kind)
    if cleared:
        database.update_profile(settings=settings)
    return cleared


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


def _external_model_ref_matches(ref: str | None, model_id: str | None) -> bool:
    """Exact match for providers where ':' is a model tag, not a variant suffix."""
    return (ref or "").strip().lower() == (model_id or "").strip().lower()


def _pick_chat_model(ids: list[str], manager=None) -> str | None:
    """Choose a text/chat-completion model, never a vision/embedding/audio one."""
    if not ids:
        return None
    alias = eff_chat_model().lower()
    if manager is None and alias:
        preferred = [m for m in ids
                     if not _NON_CHAT.search(m)
                     and _external_model_ref_matches(alias, m)]
        if preferred:
            return preferred[0]
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
    if eff_ai_provider() != "foundry":
        for mid in ids:
            if _external_model_ref_matches(key, mid):
                return mid
        return configured
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
    provider = eff_ai_provider()
    if provider != "foundry":
        base = eff_ai_base_url()
        return _normalize(base) if base else None
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
    if eff_ai_provider() != "foundry":
        return None
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
    if not model_id:
        return False
    # The endpoint may have been discovered without us starting the SDK manager
    # (unmanaged path). Acquire/initialise it so we can still load the model.
    manager = _manager
    if manager is None:
        manager, _ = _sdk_manager(initialize=True)
    if manager is None:
        return False
    try:  # pragma: no cover - depends on local install
        m = _find_catalog_model(manager, model_id)
        if m is None:
            return False
        if getattr(m, "is_loaded", False):
            return True
        m.load()
        return True
    except Exception as exc:
        _status["note"] = f"model load failed: {exc}"
        return False


def _is_not_loaded_error(exc: Exception) -> bool:
    """Detect Foundry Local's 'Model ... is not loaded' 400 response."""
    msg = str(exc or "").lower()
    return "is not loaded" in msg or "load the model" in msg


def init() -> dict[str, Any]:
    """Probe / start Foundry Local. Safe to call repeatedly."""
    global _client, _base_url, _model, _translate_model, _status
    with _lock:
        provider = eff_ai_provider()
        try:
            from openai import OpenAI
        except Exception as exc:
            _status = {"online": False, "provider": provider,
                       "provider_label": _provider_label(provider),
                       "base_url": None, "model": None,
                       "translate_model": None,
                       "note": f"openai client unavailable: {exc}",
                       "managed": False, "port": None}
            return _status

        base = _resolve_base_url()
        if not base:
            _status.update(online=False, base_url=None, model=None,
                           provider=provider, provider_label=_provider_label(provider),
                           managed=False if provider != "foundry" else _status.get("managed", False),
                           note=(f"{_provider_label(provider)} の接続先URLが未設定です"
                                 if provider != "foundry"
                                 else "Foundry Local endpoint not available"))
            return _status

        chat_pref = eff_chat_model()
        translate_pref = eff_translate_model()
        model = chat_pref
        try:
            client = OpenAI(base_url=base, api_key=eff_ai_api_key(),
                            timeout=config.AI_TIMEOUT, max_retries=0)
            models = client.models.list()
            ids = [m.id for m in getattr(models, "data", [])]
            picked = _pick_chat_model(ids, _manager if provider == "foundry" else None)
            if picked:
                model = picked
            # Ensure the chosen chat model is actually loaded (cached-but-not-
            # loaded models are listed by /v1/models but reject completions).
            if provider == "foundry":
                _ensure_loaded(model)
            translate_model = _resolve_configured_model(ids, translate_pref) if translate_pref else model
            if provider == "foundry" and translate_model and translate_model != model:
                _ensure_loaded(translate_model)
            _client, _base_url, _model, _translate_model = client, base, model, translate_model
            note = "ready"
            if ids and _NON_CHAT.search(model or ""):
                note = ("warning: selected model may not support chat. "
                        "設定画面でテキスト/チャット対応モデルを選んでください。")
            _status.update(online=True, provider=provider,
                           provider_label=_provider_label(provider),
                           base_url=base, model=model,
                           translate_model=translate_model,
                           note=note)
        except Exception as exc:
            _client, _base_url, _model, _translate_model = None, base, model, (translate_pref or model)
            _status.update(online=False, provider=provider,
                           provider_label=_provider_label(provider),
                           base_url=base, model=model,
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
    provider = eff_ai_provider()
    should_reset = _status.get("base_url") and not _reachable(str(_status["base_url"]))
    if provider != _status.get("provider"):
        should_reset = True
    if force_managed or should_reset:
        _client = None
        _model = None
        if provider == "foundry" and config.MANAGE_FOUNDRY and not config.FOUNDRY_BASE_URL:
            _force_managed_base_url()
    return init()


def status() -> dict[str, Any]:
    st = _base_status()
    st["speech"] = speech_status()
    return st


def test_model(kind: str = "chat") -> dict[str, Any]:
    """Run a tiny completion against the active chat/translation model."""
    kind = (kind or "chat").strip().lower()
    if kind not in ("chat", "translate"):
        raise ValueError("kind must be chat or translate")

    init()
    provider = eff_ai_provider()
    model = (_translate_model if kind == "translate" else _model) or _model
    result: dict[str, Any] = {
        "ok": False,
        "online": bool(_status.get("online")),
        "kind": kind,
        "provider": provider,
        "provider_label": _provider_label(provider),
        "base_url": _base_url or _status.get("base_url"),
        "model": model,
        "elapsed_ms": None,
        "sample": "",
        "note": "",
        "status": status(),
    }
    if _client is None or not _status.get("online"):
        result["note"] = _status.get("note") or "AIに接続できません。"
        return result
    if not model:
        result["note"] = "テスト対象のモデルが選択されていません。"
        return result

    if kind == "translate":
        messages = [
            {"role": "system", "content": "Translate English into natural Japanese. Reply with one short Japanese sentence only."},
            {"role": "user", "content": "The book is on the desk."},
        ]
    else:
        messages = [
            {"role": "system", "content": "Reply briefly in English."},
            {"role": "user", "content": "Say OK if this model is working."},
        ]

    start = time.perf_counter()
    try:
        try:
            resp = _client.chat.completions.create(
                model=model, messages=messages, temperature=0, max_tokens=80)
        except Exception as exc:
            # Cached-but-not-loaded model: load it on demand and retry once.
            if provider == "foundry" and _is_not_loaded_error(exc) and _ensure_loaded(model):
                resp = _client.chat.completions.create(
                    model=model, messages=messages, temperature=0, max_tokens=80)
            else:
                raise
        content = (resp.choices[0].message.content or "").strip()
        elapsed = int((time.perf_counter() - start) * 1000)
        result.update(ok=bool(content), online=True, elapsed_ms=elapsed,
                      sample=content[:500],
                      note=("テスト応答を受信しました。"
                            if content else "応答はありましたが、本文が空でした。"))
        _status.update(online=True, note="ready")
    except Exception as exc:  # pragma: no cover - depends on local endpoint
        elapsed = int((time.perf_counter() - start) * 1000)
        note = f"テスト呼び出しに失敗しました: {exc}"
        result.update(ok=False, online=False, elapsed_ms=elapsed, note=note)
        _status.update(online=False, note=note)
    result["status"] = status()
    return result


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
    if eff_ai_provider() != "foundry":
        provider = eff_ai_provider()
        _set_setup(state="ready", progress=100, phase="external",
                   model=eff_chat_model(),
                   message=f"{_provider_label(provider)} のモデルは接続先側で管理します。")
        return setup_state()
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
    if eff_ai_provider() != "foundry":
        provider = eff_ai_provider()
        _set_setup(state="ready", progress=100, phase="external",
                   model=eff_chat_model(),
                   message=f"{_provider_label(provider)} のモデルは接続先側で管理します。")
        return
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
    provider = eff_ai_provider()
    if provider != "foundry":
        init()
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
    if provider != "foundry":
        return _list_openai_models(provider, selected, current, status_snapshot)

    mgr, err = _sdk_manager(initialize=True)
    if mgr is None:
        return {"online": False,
                "note": "Foundry Local SDK が利用できないため、モデル一覧を取得できません。",
                "detail": err or "", "provider": provider, "manageable": True,
                "selected": selected, "current": current,
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
    return {"online": True, "note": "", "provider": provider, "manageable": True,
            "selected": selected,
            "current": current, "status": status_snapshot, "models": models}


def _list_openai_models(provider: str, selected: dict[str, str],
                        current: dict[str, str],
                        status_snapshot: dict[str, Any]) -> dict[str, Any]:
    """List models from an external OpenAI-compatible endpoint such as Ollama."""
    base = eff_ai_base_url()
    common = {
        "provider": provider,
        "manageable": False,
        "selected": selected,
        "current": current,
        "status": status_snapshot,
        "models": [],
    }
    if not base:
        return {**common, "online": False,
                "note": f"{_provider_label(provider)} の接続先URLを設定してください。"}
    try:
        from openai import OpenAI
        client = OpenAI(base_url=_normalize(base), api_key=eff_ai_api_key(),
                        timeout=config.AI_TIMEOUT, max_retries=0)
        models_resp = client.models.list()
        ids = [m.id for m in getattr(models_resp, "data", []) if getattr(m, "id", "")]
    except Exception as exc:
        return {**common, "online": False,
                "note": f"{_provider_label(provider)} のモデル一覧を取得できません: {exc}"}

    models = []
    for mid in ids:
        kind = _model_kind(mid)
        selected_kinds = [
            k for k, ref in selected.items()
            if ref and _external_model_ref_matches(ref, mid)
        ]
        active_kinds = [
            k for k, ref in current.items()
            if ref and _external_model_ref_matches(ref, mid)
        ]
        models.append({
            "id": mid,
            "alias": mid,
            "task": "chat-completion",
            "kind": kind,
            "cached": True,
            "loaded": bool(active_kinds),
            "selected_kinds": selected_kinds,
            "active_kinds": active_kinds,
        })
    models.sort(key=lambda x: (x["kind"] != "chat", x["id"]))
    return {**common, "online": True, "note": "", "models": models}


def download_model_async(alias: str, *, kind: str = "chat") -> dict[str, Any]:
    """Download a catalog model in the background.

    Reuses the _setup progress channel so the existing /api/ai/setup-state
    polling and overlay show the download. Chat/translate models are loaded only
    when they already match the configured model preference.
    """
    global _setup_thread
    if eff_ai_provider() != "foundry":
        raise ValueError("現在のAIプロバイダーでは、モデルは接続先側で管理してください。")
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


def delete_model(alias: str) -> dict[str, Any]:
    """Remove a downloaded Foundry Local model from the local cache."""
    if eff_ai_provider() != "foundry":
        raise ValueError("現在のAIプロバイダーでは、モデルは接続先側で管理してください。")
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("model alias is required")
    with _setup_lock:
        if _setup["state"] in ("checking", "preparing", "downloading", "loading"):
            raise ValueError("モデルの準備中です。完了してから削除してください。")
        if _setup_thread is not None and _setup_thread.is_alive():
            raise ValueError("モデルの準備中です。完了してから削除してください。")

    mgr, err = _sdk_manager(initialize=True)
    if mgr is None:
        raise ValueError(f"ローカルAIサービスに接続できません: {err or ''}")

    model = _find_catalog_model(mgr, alias)
    if model is None:
        raise ValueError(f"モデル「{alias}」がカタログに見つかりませんでした。")

    mid = getattr(model, "id", "") or alias
    model_alias = getattr(model, "alias", "") or alias
    if not getattr(model, "is_cached", False):
        raise ValueError(f"モデル「{mid}」はダウンロードされていません。")

    was_selected_or_active = any(
        _model_ref_matches(ref, mid, model_alias)
        for ref in (eff_chat_model(), eff_translate_model(), eff_transcribe_model(),
                    _model, _translate_model)
        if ref
    )
    try:
        if getattr(model, "is_loaded", False):
            model.unload()
    except Exception:
        pass
    try:
        model.remove_from_cache()
    except Exception as exc:
        raise ValueError(f"モデル「{mid}」の削除に失敗しました: {exc}") from exc

    invalidate = getattr(mgr.catalog, "_invalidate_cache", None)
    if callable(invalidate):
        try:
            invalidate()
        except Exception:
            pass
    cleared = _clear_foundry_preferences(mid, model_alias)
    if was_selected_or_active or any(k in cleared for k in ("chat", "translate")):
        reconnect()

    data = list_models()
    data["deleted"] = {"id": mid, "alias": model_alias, "cleared_kinds": cleared}
    return data


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
    reloaded = False
    for kw in attempts:
        try:
            resp = _client.chat.completions.create(**kw)
            return resp.choices[0].message.content
        except Exception as exc:  # pragma: no cover
            last = exc
            # Load a cached-but-not-loaded Foundry model once, then retry.
            if (not reloaded and eff_ai_provider() == "foundry"
                    and _is_not_loaded_error(exc) and _ensure_loaded(kw.get("model"))):
                reloaded = True
                try:
                    resp = _client.chat.completions.create(**kw)
                    return resp.choices[0].message.content
                except Exception as exc2:
                    last = exc2
            continue
    _status["online"] = False
    _status["note"] = f"call failed: {last}"
    return None


def _chat_stream(messages: list[dict[str, str]], *, temperature: float = 0.4,
                 max_tokens: int = 512, json_mode: bool = False,
                 model: str | None = None):
    """Yield streamed text chunks from an OpenAI-compatible chat endpoint."""
    if _client is None:
        init()
    if _client is None:
        raise RuntimeError(_status.get("note") or "AI offline")
    base: dict[str, Any] = dict(model=(model or _model), messages=messages,
                                temperature=temperature, max_tokens=max_tokens,
                                stream=True)
    attempts = ([{**base, "response_format": {"type": "json_object"}}] if json_mode else []) + [base]
    last = None
    reloaded = False
    for kw in attempts:
        try:
            try:
                stream = _client.chat.completions.create(**kw)
            except Exception as exc:
                if (not reloaded and eff_ai_provider() == "foundry"
                        and _is_not_loaded_error(exc) and _ensure_loaded(kw.get("model"))):
                    reloaded = True
                    stream = _client.chat.completions.create(**kw)
                else:
                    raise
            for chunk in stream:
                try:
                    delta = chunk.choices[0].delta.content or ""
                except Exception:
                    delta = ""
                if delta:
                    yield delta
            return
        except Exception as exc:  # pragma: no cover
            last = exc
            continue
    _status["online"] = False
    _status["note"] = f"stream failed: {last}"
    raise RuntimeError(str(last))


def _stream_event(event: str, **data: Any) -> str:
    return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"


def _stream_completion(messages: list[dict[str, str]], *,
                       temperature: float = 0.4, max_tokens: int = 512,
                       json_mode: bool = False, model: str | None = None,
                       finish=None, fallback=None):
    """NDJSON events: start, delta, final. Final keeps existing API shape."""
    yield _stream_event("start", status=status())
    raw_parts: list[str] = []
    try:
        for delta in _chat_stream(messages, temperature=temperature,
                                  max_tokens=max_tokens, json_mode=json_mode,
                                  model=model):
            raw_parts.append(delta)
            yield _stream_event("delta", text=delta)
        raw = "".join(raw_parts)
        result = finish(raw) if finish else {"online": True, "text": raw}
    except Exception as exc:
        yield _stream_event("error", note=f"ストリーミングに失敗しました: {exc}")
        result = None
    if not result and fallback:
        result = fallback()
    yield _stream_event("final", result=result or {"online": False, "note": "AI出力を取得できませんでした。"})


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
            "You are a dictionary for Japanese learners of English. "
            "Give the meaning of the following English word IN JAPANESE. "
            "Return ONLY JSON in the form "
            "{\"translation\": \"concise Japanese meaning\", \"pos\": \"part of speech in Japanese\", "
            "\"note\": \"optional one-line usage note in Japanese\"}."
        )
    else:
        instruction = (
            "You are a translator for Japanese learners of English. "
            "Translate the following English text into natural Japanese. "
            "Return ONLY JSON in the form "
            "{\"translation\": \"natural Japanese translation\", "
            "\"note\": \"optional one-line note on grammar or nuance in Japanese\"}."
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


def translate_stream(text: str, mode: str = "auto", glossary: dict | None = None):
    text = (text or "").strip()
    if mode == "auto":
        mode = "word" if len(text.split()) == 1 else "sentence"
    if mode == "word":
        instruction = (
            "You are a dictionary for Japanese learners of English. "
            "Give the meaning of the following English word IN JAPANESE. "
            "Return ONLY JSON in the form "
            "{\"translation\": \"concise Japanese meaning\", \"pos\": \"part of speech in Japanese\", "
            "\"note\": \"optional one-line usage note in Japanese\"}."
        )
    else:
        instruction = (
            "You are a translator for Japanese learners of English. "
            "Translate the following English text into natural Japanese. "
            "Return ONLY JSON in the form "
            "{\"translation\": \"natural Japanese translation\", "
            "\"note\": \"optional one-line note on grammar or nuance in Japanese\"}."
        )

    def finish(raw: str) -> dict[str, Any] | None:
        parsed = _safe_json(raw)
        if parsed and parsed.get("translation"):
            if mode != "word" and _looks_untranslated(text, str(parsed.get("translation", ""))):
                return _translation_failed(text, "モデルが原文を返したため、和訳としては表示しません。")
            parsed.setdefault("note", "")
            return {"source": text, "online": True, **parsed}
        cleaned = _plain_text(raw)
        if cleaned:
            if mode != "word" and _looks_untranslated(text, cleaned):
                return _translation_failed(text, "モデルが原文を返したため、和訳としては表示しません。")
            return {"source": text, "translation": cleaned, "note": "", "online": True}
        return None

    return _stream_completion(
        [{"role": "system", "content": instruction},
         {"role": "user", "content": text}],
        temperature=0.2, max_tokens=300, json_mode=True,
        model=(_translate_model or config.TRANSLATE_MODEL or None),
        finish=finish, fallback=lambda: translate(text, mode, glossary),
    )


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


# --- Learner level → concrete difficulty guidance --------------------------
# The settings UI lets the learner pick a level (入門/初級/中級). Passing the
# bare alias ("beginner"/"elementary"/"intermediate") to the model gives weak,
# inconsistent results — small local models can't infer the intended vocabulary
# or sentence complexity from one word. These guides spell out the target so the
# difference between levels is actually reflected in chat, corrections, reading,
# and story generation.

_LEVEL_GUIDES = {
    "beginner": (
        "CEFR A1 (absolute beginner / 入門). Use only the ~500 most common words. "
        "Mostly present tense, sentences under 8 words, one idea per sentence. "
        "Avoid idioms, phrasal verbs, and subordinate clauses."
    ),
    "elementary": (
        "CEFR A2 (elementary / 初級). Use common everyday vocabulary. "
        "Simple past, future, and present continuous are fine. Sentences under "
        "12 words; at most one simple subordinate clause. Avoid rare idioms."
    ),
    "intermediate": (
        "CEFR B1 (intermediate / 中級). Use varied everyday and topic vocabulary, "
        "common phrasal verbs and connectors, and a natural mix of tenses and "
        "compound/complex sentences. Keep it clear, not academic."
    ),
}


def _level_guide(level: str) -> str:
    """Return concrete difficulty guidance for a learner level alias."""
    key = (level or "").strip().lower()
    return _LEVEL_GUIDES.get(key, _LEVEL_GUIDES["beginner"])


def tutor_reply(history: list[dict[str, str]], scenario: str = "",
                level: str = "beginner", name: str = "Vivi",
                gender: str = "female") -> dict[str, Any]:
    """Conversation partner that replies in English and gently corrects."""
    g = "male" if str(gender).lower().startswith("m") else "female"
    sys = (
        f"You are '{name}', a warm, encouraging {g} English conversation partner for a "
        f"Japanese {level} learner. Stay strictly in the role-play scenario: {scenario or 'free talk'}. "
        f"Target English level — {_level_guide(level)} "
        "Rules: (1) Reply in short, natural English that matches the target level above. "
        "(2) Always provide reply_ja, a natural Japanese translation of your English reply. "
        "(3) Check the learner's latest English carefully for spelling, word choice, grammar, "
        "missing words, unnatural phrasing, or Japanese-English literal wording. If there is any "
        "issue, put a concise Japanese explanation in correction, including the better phrase. "
        "If it is fully natural, set correction to an empty string. "
        "(4) Always include one short, practical Japanese tip in tip. "
        "(5) Always end the English reply with a simple follow-up question to keep the conversation going. "
        "Return ONLY JSON: {\"reply\":\"English reply\", "
        "\"reply_ja\":\"Japanese translation of the reply\", "
        "\"correction\":\"correction or improvement point in Japanese (empty string if none)\", "
        "\"tip\":\"short learning tip in Japanese\"}."
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


def tutor_reply_stream(history: list[dict[str, str]], scenario: str = "",
                       level: str = "beginner", name: str = "Vivi",
                       gender: str = "female"):
    g = "male" if str(gender).lower().startswith("m") else "female"
    sys = (
        f"You are '{name}', a warm, encouraging {g} English conversation partner for a "
        f"Japanese {level} learner. Stay strictly in the role-play scenario: {scenario or 'free talk'}. "
        f"Target English level — {_level_guide(level)} "
        "Rules: (1) Reply in short, natural English that matches the target level above. "
        "(2) Always provide reply_ja, a natural Japanese translation of your English reply. "
        "(3) Check the learner's latest English carefully for spelling, word choice, grammar, "
        "missing words, unnatural phrasing, or Japanese-English literal wording. If there is any "
        "issue, put a concise Japanese explanation in correction, including the better phrase. "
        "If it is fully natural, set correction to an empty string. "
        "(4) Always include one short, practical Japanese tip in tip. "
        "(5) Always end the English reply with a simple follow-up question to keep the conversation going. "
        "Return ONLY JSON: {\"reply\":\"English reply\", "
        "\"reply_ja\":\"Japanese translation of the reply\", "
        "\"correction\":\"correction or improvement point in Japanese (empty string if none)\", "
        "\"tip\":\"short learning tip in Japanese\"}."
    )
    messages = [{"role": "system", "content": sys}] + history
    learner_text = _last_user_text(history)

    def finish(raw: str) -> dict[str, Any] | None:
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
        cleaned = _plain_text(raw)
        if cleaned:
            review = _review_learner_text(learner_text, level)
            return {"online": True, "reply": cleaned, "reply_ja": _ensure_reply_ja(cleaned, ""),
                    "correction": review.get("correction", ""), "tip": review.get("tip", "")}
        return None

    return _stream_completion(
        messages, temperature=0.6, max_tokens=400, json_mode=True,
        finish=finish,
        fallback=lambda: tutor_reply(history, scenario, level, name=name, gender=gender),
    )


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
        f"The learner's target level — {_level_guide(level)} "
        "Find spelling mistakes, wrong word choice, grammar mistakes, missing articles/prepositions, or unnatural phrasing. "
        "If there is any issue, give one concise Japanese correction with a better English phrase suited to that level. "
        "If the sentence is natural and correct, set correction to an empty string. "
        "Return ONLY JSON: {\"correction\":\"correction explanation in Japanese. Better: ...\", "
        "\"tip\":\"short tip in Japanese\"}."
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
        f"Target English level — {_level_guide(level)} "
        "Create 3 short, natural English replies the learner can choose from. "
        "Keep them easy to say aloud and relevant to the latest assistant message. "
        "Return ONLY JSON: {\"suggestions\":[{\"en\":\"English sentence\","
        "\"ja\":\"natural Japanese translation\",\"note\":\"short note in Japanese on when to use it\"}]}."
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


def chat_suggestions_stream(history: list[dict[str, str]], scenario: str = "",
                            level: str = "beginner"):
    sys = (
        "You help a Japanese English learner continue a role-play conversation. "
        f"Scenario: {scenario or 'free talk'}. Learner level: {level}. "
        f"Target English level — {_level_guide(level)} "
        "Create 3 short, natural English replies the learner can choose from. "
        "Keep them easy to say aloud and relevant to the latest assistant message. "
        "Return ONLY JSON: {\"suggestions\":[{\"en\":\"English sentence\","
        "\"ja\":\"natural Japanese translation\",\"note\":\"short note in Japanese on when to use it\"}]}."
    )

    def finish(raw: str) -> dict[str, Any] | None:
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
        return None

    return _stream_completion(
        [{"role": "system", "content": sys}] + history[-10:],
        temperature=0.5, max_tokens=420, json_mode=True,
        finish=finish,
        fallback=lambda: chat_suggestions(history, scenario, level),
    )


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
        f"Target English level — {_level_guide(level)} "
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


def generate_reading_passage_stream(topic: str = "", level: str = "beginner",
                                    length: str = "medium"):
    topic_txt = re.sub(r"\s+", " ", str(topic or "")).strip() or "technology and daily life"
    plan = _READING_LENGTHS.get(length, _READING_LENGTHS["medium"])
    para_count = int(plan["paragraphs"])
    angle = random.choice(_READING_ANGLES)
    seed = random.randint(1000, 9999)
    sys = (
        "You are an English reading-material writer for Japanese learners. "
        f"Write EXACTLY {para_count} paragraphs ({plan['words']}) for a {level} learner "
        f"about \"{topic_txt}\" as {angle['genre']}. "
        f"Target English level — {_level_guide(level)} "
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
    user = (
        f"Topic: {topic_txt}\nLevel: {level}\nLength: {length}\n"
        f"Genre: {angle['genre']}\nAngle: {angle['angle']}\n"
        f"Structure: {angle['structure']}\nSeed: {seed}"
    )

    def finish(raw: str) -> dict[str, Any] | None:
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
        return None

    return _stream_completion(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.85, max_tokens=1200, json_mode=True,
        finish=finish,
        fallback=lambda: generate_reading_passage(topic, level=level, length=length),
    )


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


# --- Reading analysis (sentence-by-sentence) -------------------------------
# Small local models break down when asked to emit one giant JSON object for a
# whole passage: the output is truncated or malformed once the text is long.
# So we split the passage into paragraphs/sentences ourselves and ask the model
# to analyse ONE sentence per call, returning a tiny JSON object. Any sentence
# the model fails on falls back to the rule-based analyser individually, so a
# single bad sentence never wrecks the whole passage.

def _reading_sentence_sys(level: str) -> str:
    return (
        "You are an expert English reading coach for Japanese learners. "
        f"Analyse ONE English sentence for a {level} learner. "
        f"Pitch the explanation to this level — {_level_guide(level)} "
        "Do NOT translate it. Return ONLY a compact JSON object for this single sentence. "
        "Use concrete values, never placeholder descriptions. "
        "Forbidden values include: 'exact original sentence', 'exact words from the sentence', "
        "'SV/SVC/SVO/SVOO/SVOC + Japanese explanation', '要点/対比/理由・原因/結果/結論/具体例/詳細など', "
        "'接続語/S 主語/V 動詞/O 目的語/C 補語/修飾句', '日本語ラベル', and '...'. "
        "JSON contract: {text, pattern, focus, chunks, signals}. "
        "Each chunk has kind, label, and text. kind must be one of connector, subject, verb, object, complement, modifier. "
        "Each signal has key, label, and match. "
        "Example for 'However, the change does not help everyone in the same way.': "
        "{\"text\":\"However, the change does not help everyone in the same way.\","
        "\"pattern\":\"SVO（主語＋動詞＋目的語）\","
        "\"focus\":\"対比\","
        "\"chunks\":["
        "{\"kind\":\"connector\",\"label\":\"接続語\",\"text\":\"However\"},"
        "{\"kind\":\"subject\",\"label\":\"S 主語\",\"text\":\"the change\"},"
        "{\"kind\":\"verb\",\"label\":\"V 動詞\",\"text\":\"does not help\"},"
        "{\"kind\":\"object\",\"label\":\"O 目的語\",\"text\":\"everyone\"},"
        "{\"kind\":\"modifier\",\"label\":\"修飾句\",\"text\":\"in the same way\"}],"
        "\"signals\":[{\"key\":\"contrast\",\"label\":\"対比\",\"match\":\"However\"}]}. "
        "Rules: copy the sentence exactly into text, in English. "
        "Chunk text and signal match must be copied exactly from the sentence, without adding words. "
        "Separate sentence-opening connectors such as However/Therefore/For example from the subject. "
        "Treat verb phrases such as 'does not help', 'can be seen', and 'has been changing' as one V chunk. "
        "Put prepositional/adverbial phrases such as 'in the same way' in modifier chunks."
    )


def _reading_paragraph_units(text: str) -> list[list[str]]:
    """Split a passage into paragraphs, each a list of sentence strings."""
    units = []
    for para_text in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        sentences = _reading_sentence_split(para_text)
        if sentences:
            units.append(sentences)
    if not units:
        sentences = _reading_sentence_split(text)
        if sentences:
            units.append(sentences)
    return units


def _reading_note(any_usable: bool, any_fallback: bool) -> str:
    if any_usable and any_fallback:
        return "一部の文はAI解析が崩れたため簡易解析で補いました。"
    if any_usable:
        return "AI解析を表示しています。"
    # AI was reachable but produced nothing usable for any sentence.
    return "AI解析の形式が崩れたため、簡易解析を表示しています。"


def _reading_role(pi: int, total: int) -> str:
    if total <= 1:
        return "導入・話題提示"
    if pi == 0:
        return "導入・話題提示"
    if pi == total - 1:
        return "結論・まとめ"
    return "展開・補足"


def _analyze_reading_sentence(sentence: str, level: str, pi: int, si: int) -> tuple[dict[str, Any], bool, bool]:
    """Analyse a single sentence via the model, falling back to rules.

    Returns ``(sentence_analysis, usable, responded)`` where ``usable`` is True
    only when the model produced a valid result for this sentence, and
    ``responded`` is True whenever the model replied at all (i.e. the AI is
    reachable, even if its output was unusable). Keeping the two apart lets the
    caller tell "could not connect" from "connected but output was malformed".
    """
    raw = _chat(
        [{"role": "system", "content": _reading_sentence_sys(level)},
         {"role": "user", "content": sentence}],
        temperature=0.1, max_tokens=600, json_mode=True,
        model=(_translate_model or config.TRANSLATE_MODEL or None),
    )
    responded = bool(raw)
    if raw:
        parsed = _safe_json(raw)
        if parsed:
            item = _clean_reading_sentence(parsed, sentence)
            if item:
                return item, True, True
    return _fallback_sentence_analysis(sentence, pi, si), False, responded


def analyze_reading_text(text: str, level: str = "beginner") -> dict[str, Any]:
    """Analyse a passage into paragraph roles, sentence patterns, and chunks.

    Each sentence is analysed in its own small model call so that long passages
    do not overwhelm small local models.
    """
    text = str(text or "").strip()
    if not text:
        return {"online": False, "analysis": None, "note": "解析する英文を入力してください。"}

    units = _reading_paragraph_units(text)
    if not units:
        return {"online": False, "analysis": None, "note": "解析する英文を入力してください。"}

    paragraphs = []
    all_signals = []
    any_usable = False
    any_fallback = False
    any_responded = False
    total = len(units)
    for pi, sentences in enumerate(units):
        items = []
        for si, sentence in enumerate(sentences):
            item, usable, responded = _analyze_reading_sentence(sentence, level, pi, si)
            any_usable = any_usable or usable
            any_fallback = any_fallback or (not usable)
            any_responded = any_responded or responded
            all_signals.extend(item["signals"])
            items.append(item)
        paragraphs.append({
            "index": pi,
            "role": _reading_role(pi, total),
            "reason": "段落の位置から役割を推定しました。",
            "sentences": items,
        })

    # The AI was never reachable for any sentence -> truly offline.
    if not any_responded:
        fallback = _fallback_reading_analysis(
            text, "AI解析に接続できなかったため、簡易解析を表示しています。")
        return {"online": False, "analysis": fallback, "note": fallback["note"]}

    note = _reading_note(any_usable, any_fallback)
    analysis = {"paragraphs": paragraphs, "signals": all_signals, "note": note}
    return {"online": True, "analysis": analysis, "note": note}


def analyze_reading_text_stream(text: str, level: str = "beginner"):
    """Stream a sentence-by-sentence reading analysis as NDJSON events.

    Emits a ``delta`` per analysed sentence (so the UI shows progress) and a
    single ``final`` event with the assembled analysis, matching the existing
    stream contract.
    """
    text = str(text or "").strip()
    if not text:
        return iter([_stream_event("final", result={"online": False, "analysis": None,
                                                    "note": "解析する英文を入力してください。"})])

    def gen():
        yield _stream_event("start", status=status())
        units = _reading_paragraph_units(text)
        total_sentences = sum(len(s) for s in units)
        paragraphs = []
        all_signals = []
        any_usable = False
        any_fallback = False
        any_responded = False
        total = len(units)
        done = 0
        for pi, sentences in enumerate(units):
            items = []
            for si, sentence in enumerate(sentences):
                try:
                    item, usable, responded = _analyze_reading_sentence(sentence, level, pi, si)
                except Exception as exc:
                    yield _stream_event("error", note=f"文の解析に失敗しました: {exc}")
                    item, usable, responded = _fallback_sentence_analysis(sentence, pi, si), False, False
                any_usable = any_usable or usable
                any_fallback = any_fallback or (not usable)
                any_responded = any_responded or responded
                all_signals.extend(item["signals"])
                items.append(item)
                done += 1
                yield _stream_event("delta",
                                    text=f"[{done}/{total_sentences}] {item['text']}\n")
            paragraphs.append({
                "index": pi,
                "role": _reading_role(pi, total),
                "reason": "段落の位置から役割を推定しました。",
                "sentences": items,
            })

        # The AI was never reachable for any sentence -> truly offline.
        if not any_responded:
            fallback = _fallback_reading_analysis(
                text, "AI解析に接続できなかったため、簡易解析を表示しています。")
            yield _stream_event("final", result={"online": False, "analysis": fallback,
                                                  "note": fallback["note"]})
            return
        note = _reading_note(any_usable, any_fallback)
        analysis = {"paragraphs": paragraphs, "signals": all_signals, "note": note}
        yield _stream_event("final", result={"online": True, "analysis": analysis, "note": note})

    return gen()


def _clean_reading_sentence(sent: Any, source: str) -> dict[str, Any] | None:
    """Validate and normalise one AI-produced sentence object.

    ``source`` is the English text the sentence (and its chunks) must be copied
    from — the whole passage for batch parsing, or the single sentence itself
    when each sentence is analysed on its own.
    """
    if not isinstance(sent, dict):
        return None
    stext = _norm_text(str(sent.get("text", "")))
    if (not stext or _bad_reading_value(stext) or _JP_CHARS.search(stext)
            or not _copied_from(stext, source)):
        return None
    chunks = []
    for ch in sent.get("chunks") or []:
        if not isinstance(ch, dict):
            continue
        ctext = _norm_text(str(ch.get("text", "")))
        if (not ctext or _bad_reading_value(ctext) or _JP_CHARS.search(ctext)
                or not _copied_from(ctext, stext)):
            continue
        kind = str(ch.get("kind", "")).strip().lower()
        if kind not in {"connector", "subject", "verb", "object", "complement", "modifier"}:
            kind = "modifier"
        chunks.append({
            "kind": kind,
            "label": str(ch.get("label", "")).strip() or _chunk_label(kind),
            "text": ctext,
        })
    pattern = str(sent.get("pattern", "")).strip()
    if _bad_reading_value(pattern):
        pattern = "文型不明"
    focus = str(sent.get("focus", "")).strip()
    if _bad_reading_value(focus):
        focus = "詳細"
    signals = _clean_reading_signals(sent.get("signals") or [], context=stext)
    return {
        "text": stext,
        "pattern": pattern or "文型不明",
        "focus": focus or "詳細",
        "chunks": chunks,
        "signals": signals,
    }


def _clean_reading_signals(items: list[Any], context: str = "") -> list[dict[str, str]]:
    clean = []
    allowed = {"contrast", "reason", "cause", "result", "conclusion",
               "example", "addition", "sequence", "reference"}
    for item in items:
        if not isinstance(item, dict):
            continue
        match = _norm_text(str(item.get("match", "")))
        if (not match or _bad_reading_value(match) or _JP_CHARS.search(match)
                or (context and not _copied_from(match, context))):
            continue
        key = str(item.get("key", "")).strip().lower()
        if key not in allowed:
            key = "reference"
        clean.append({
            "key": key,
            "label": str(item.get("label", "")).strip() or key,
            "match": match,
        })
    return clean


_READING_PLACEHOLDERS = {
    "exact original sentence",
    "exact words from the sentence",
    "exact signal word or phrase",
    "sv/svc/svo/svoo/svoc + japanese explanation",
    "要点/対比/理由・原因/結果/結論/具体例/詳細など",
    "接続語/s 主語/v 動詞/o 目的語/c 補語/修飾句",
    "日本語ラベル",
    "...",
}


def _bad_reading_value(value: str) -> bool:
    text = _norm_text(value).lower()
    if not text:
        return True
    return text in _READING_PLACEHOLDERS or "exact " in text or " + japanese explanation" in text


def _copied_from(needle: str, haystack: str) -> bool:
    n = _norm_text(needle).lower().strip(".,;:!?")
    h = _norm_text(haystack).lower()
    return bool(n) and n in h


def _chunk_label(kind: str) -> str:
    return {
        "connector": "接続語",
        "subject": "S 主語",
        "verb": "V 動詞",
        "object": "O 目的語",
        "complement": "C 補語",
        "modifier": "修飾句",
    }.get(kind, "修飾句")


def _fallback_reading_analysis(text: str, note: str) -> dict[str, Any]:
    paragraphs = []
    all_signals = []
    for pi, para_text in enumerate([p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]):
        sentences = []
        for si, sentence in enumerate(_reading_sentence_split(para_text)):
            item = _fallback_sentence_analysis(sentence, pi, si)
            sentences.append(item)
            all_signals.extend(item["signals"])
        if sentences:
            role = "導入・話題提示" if pi == 0 else "展開・補足"
            paragraphs.append({
                "index": pi,
                "role": role,
                "reason": "AI出力が崩れたため、語順と接続語から推定しました。",
                "sentences": sentences,
            })
    if paragraphs:
        paragraphs[-1]["role"] = "結論・まとめ" if len(paragraphs) > 1 else paragraphs[-1]["role"]
    return {"paragraphs": paragraphs, "signals": all_signals, "note": note}


def _reading_sentence_split(text: str) -> list[str]:
    return [s.strip() for s in re.findall(r"[^.!?]+(?:[.!?]+|$)", text) if s.strip()]


_FB_CONNECTORS = {
    "however": ("contrast", "対比"),
    "therefore": ("result", "結果"),
    "so": ("result", "結果"),
    "because": ("reason", "理由"),
    "although": ("contrast", "対比"),
    "also": ("addition", "追加"),
    "moreover": ("addition", "追加"),
    "finally": ("conclusion", "結論"),
}
_FB_VERBS = {
    "feel", "feels", "felt", "see", "sees", "saw", "seen", "help", "helps",
    "make", "makes", "made", "think", "thinks", "look", "looks", "read",
    "reads", "become", "becomes", "became", "give", "gives", "find", "finds",
    "show", "shows", "need", "needs", "use", "uses", "learn", "learns",
}
_FB_AUX = {"do", "does", "did", "can", "could", "will", "would", "should",
           "may", "might", "must", "is", "are", "was", "were", "am", "be",
           "has", "have", "had"}
_FB_NEG = {"not", "never"}
_FB_PREP = {"in", "on", "at", "by", "for", "from", "with", "without", "into",
            "over", "under", "between", "through", "during", "before", "after",
            "of", "to", "about", "around", "when", "where", "while", "because", "if"}
_FB_LINKING = {"feel", "feels", "felt", "is", "are", "was", "were", "am",
               "be", "become", "becomes", "became", "seem", "seems"}


def _fallback_sentence_analysis(sentence: str, pi: int, si: int) -> dict[str, Any]:
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+", sentence)
    lower = [w.lower() for w in words]
    signals = []
    chunks = []
    start = 0
    if lower and lower[0] in _FB_CONNECTORS:
        key, label = _FB_CONNECTORS[lower[0]]
        chunks.append({"kind": "connector", "label": "接続語", "text": words[0]})
        signals.append({"key": key, "label": label, "match": words[0]})
        start = 1
    verb_start, verb_end = _fallback_find_verb(lower, start)
    if verb_start < 0:
        return {
            "text": sentence, "pattern": "文型不明", "focus": "詳細",
            "chunks": chunks or [{"kind": "modifier", "label": "文", "text": " ".join(words)}],
            "signals": signals,
        }
    subject = " ".join(words[start:verb_start]).strip()
    verb = " ".join(words[verb_start:verb_end + 1]).strip()
    rest_words = words[verb_end + 1:]
    rest_lower = lower[verb_end + 1:]
    if subject:
        chunks.append({"kind": "subject", "label": "S 主語", "text": subject})
    if verb:
        chunks.append({"kind": "verb", "label": "V 動詞", "text": verb})
    prep_at = next((i for i, w in enumerate(rest_lower) if w in _FB_PREP), -1)
    main_rest = rest_words[:prep_at] if prep_at >= 0 else rest_words
    prep_rest = rest_words[prep_at:] if prep_at >= 0 else []
    main_kind = "complement" if lower[verb_end] in _FB_LINKING else "object"
    if main_rest:
        chunks.append({"kind": main_kind, "label": _chunk_label(main_kind), "text": " ".join(main_rest)})
    if prep_rest:
        chunks.append({"kind": "modifier", "label": "修飾句", "text": " ".join(prep_rest)})
    pattern = "SVC（主語＋動詞＋補語）" if main_kind == "complement" and main_rest else (
        "SVO（主語＋動詞＋目的語）" if main_rest else "SV（主語＋動詞）"
    )
    focus = signals[0]["label"] if signals else ("要点" if si == 0 else "詳細")
    return {"text": sentence, "pattern": pattern, "focus": focus, "chunks": chunks, "signals": signals}


def _fallback_find_verb(lower: list[str], start: int) -> tuple[int, int]:
    for i in range(start, len(lower)):
        w = lower[i]
        if w in _FB_AUX:
            end = i
            j = i + 1
            if j < len(lower) and lower[j] in _FB_NEG:
                end = j
                j += 1
            if j < len(lower) and (lower[j] in _FB_VERBS or re.search(r"(ed|ing)$", lower[j])):
                end = j
            return i, end
        if w in _FB_VERBS or re.search(r"(ed|ing)$", w):
            return i, i
    return -1, -1


def check_speech(target: str, said: str, level: str = "beginner") -> dict[str, Any]:
    """Compare what the learner said (from transcription) to the target line."""
    target = (target or "").strip()
    said = (said or "").strip()
    score = _word_overlap_score(target, said)

    sys = (
        "You are a friendly English pronunciation/accuracy coach for a Japanese "
        f"{level} learner. Target English level — {_level_guide(level)} "
        "Compare the TARGET sentence and what the learner SAID "
        "(auto-transcribed, so ignore punctuation/case). Return ONLY JSON: "
        "{\"score\":0-100, \"good\":\"praise point in Japanese\", "
        "\"improve\":\"specific point to improve in Japanese\", "
        "\"missed_words\":[\"words that were missed or said incorrectly\"]}."
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
            f"English learner (target level — {_level_guide(level)}), "
            f"on the theme \"{theme}\". You MUST include every "
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
        "omit required terms. Keep the English natural and matched to the target "
        f"level ({_level_guide(level)}), and make the theme clearly recognisable. "
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


def generate_story_stream(words: list[str], theme: str = "", level: str = "beginner",
                          fmt: str = "story", length: str = "short"):
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
        return iter([_stream_event("final", result={"online": _status["online"], "title": "", "story": "",
                                                    "story_ja": "", "used_words": [], "vocab_notes": [],
                                                    "note": "単語やフレーズを1つ以上選んでください。"})])

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
        "omit required terms. Keep the English natural and matched to the target "
        f"level ({_level_guide(level)}), and make the theme clearly recognisable. "
        "CRITICAL: the \"story\" field MUST be written in ENGLISH only — never in "
        "Japanese, and not mixed Japanese/English. The \"story_ja\" field is the "
        "Japanese translation of that English passage. Do not swap them. "
        "Return ONLY JSON: {\"title\":\"a short English title\", "
        "\"story\":\"the passage IN ENGLISH\", "
        "\"story_ja\":\"その英文の全文の自然な和訳\", "
        "\"used_words\":[\"words you actually used\"], "
        "\"vocab_notes\":[{\"en\":\"word\",\"ja\":\"この文脈での意味\"}]}."
    )

    def finish(raw: str) -> dict[str, Any] | None:
        parsed = _safe_json(raw)
        if parsed and parsed.get("story"):
            story = str(parsed.get("story", "")).strip()
            story_ja = str(parsed.get("story_ja", "")).strip()
            if _is_mostly_japanese(story) and story_ja and not _is_mostly_japanese(story_ja):
                story, story_ja = story_ja, story
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
        return None

    return _stream_completion(
        [{"role": "system", "content": sys},
         {"role": "user", "content": f"Required terms:\n{required_lines}\nTheme: {theme_txt}"}],
        temperature=0.4, max_tokens=1000, json_mode=True,
        finish=finish,
        fallback=lambda: generate_story(words, theme=theme, level=level, fmt=fmt, length=length),
    )


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
