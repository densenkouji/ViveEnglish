"""Central configuration for ViveEnglish.

All values can be overridden with environment variables so the same code runs
on a developer laptop, in a classroom, or on a teacher's PC without edits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- Paths -----------------------------------------------------------------
# When packaged with PyInstaller, bundled files live under sys._MEIPASS and the
# install dir is read-only, so the SQLite DB must go to a writable user folder.
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = BASE_DIR / "app"
CONTENT_DIR = APP_DIR / "content"
WEB_DIR = BASE_DIR / "web"

if FROZEN:
    _default_data = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ViveEnglish"
else:
    _default_data = BASE_DIR / "data"
DATA_DIR = Path(os.getenv("VIVE_DATA_DIR", str(_default_data)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "viveenglish.db"

# --- AI provider -----------------------------------------------------------
# Chat/translation/grading use an OpenAI-compatible chat-completions API.
# Foundry Local remains the default because it can also manage local models and
# speech-to-text, but learners can point the app at Ollama or another compatible
# endpoint from the settings screen.
AI_PROVIDER = os.getenv("VIVE_AI_PROVIDER", "foundry").strip().lower()
AI_BASE_URL = os.getenv("VIVE_AI_BASE_URL", "").strip()
AI_API_KEY = os.getenv("VIVE_AI_API_KEY", "notneeded")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()

# --- Foundry Local ---------------------------------------------------------
# Foundry Local exposes an OpenAI-compatible endpoint on localhost.
# If FOUNDRY_BASE_URL is set we use it directly; otherwise we try to discover
# the endpoint via the foundry-local-sdk; otherwise the app runs in OFFLINE
# mode (pre-authored content still works, AI features return a clear notice).
FOUNDRY_BASE_URL = os.getenv("FOUNDRY_BASE_URL", "").strip()
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "notneeded")

# Model aliases. Foundry Local resolves an alias to the best variant for the
# current hardware, so we keep them configurable.
CHAT_MODEL = os.getenv("VIVE_CHAT_MODEL", "qwen2.5-1.5b")
# Optional model dedicated to Japanese translation / correction. Leave empty to
# reuse CHAT_MODEL. Set this to a larger Japanese-capable chat model when small
# models copy English instead of translating.
TRANSLATE_MODEL = os.getenv("VIVE_TRANSLATE_MODEL", "").strip()
# Speech-to-text uses a Whisper model via the SDK's file-based audio client.
TRANSCRIBE_MODEL = os.getenv("VIVE_TRANSCRIBE_MODEL", "whisper-base")

# Default port Foundry Local listens on when started manually.
FOUNDRY_FALLBACK_URL = os.getenv("FOUNDRY_FALLBACK_URL", "http://localhost:5273/v1")

# --- Managed Foundry Local lifecycle --------------------------------------
# When enabled (and the foundry-local-sdk is installed), ViveEnglish picks a
# FREE port at startup and starts Foundry Local bound to that exact port, so we
# never depend on a guessed/dynamic port. Set VIVE_MANAGE_FOUNDRY=0 to disable
# (e.g. to attach to an externally managed service via FOUNDRY_BASE_URL).
MANAGE_FOUNDRY = os.getenv("VIVE_MANAGE_FOUNDRY", "1") not in ("0", "false", "False")

# Force a specific port instead of auto-selecting a free one (optional).
FOUNDRY_PORT = os.getenv("VIVE_FOUNDRY_PORT", "").strip()

# Host to bind the managed service to.
FOUNDRY_HOST = os.getenv("VIVE_FOUNDRY_HOST", "127.0.0.1").strip()

# Auto-load the chat model on startup if it is already cached (no surprise
# multi-GB downloads — uncached models are left for the user to `foundry model run`).
AUTOLOAD_MODEL = os.getenv("VIVE_AUTOLOAD_MODEL", "1") not in ("0", "false", "False")

# Request timeout (seconds) for AI calls.
AI_TIMEOUT = float(os.getenv("VIVE_AI_TIMEOUT", "60"))

APP_NAME = "ViveEnglish"
APP_VERSION = "1.0.0"
