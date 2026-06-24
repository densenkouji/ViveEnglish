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

# --- OpenAI (ChatGPT) ------------------------------------------------------
# OpenAI's hosted API (api.openai.com). The secret API key is NEVER stored by
# the app: the settings screen records the NAME of an environment variable, and
# the key value is read from that variable at run time via os.getenv().
# VIVE_OPENAI_API_KEY_ENV only sets which variable name to read by default.
OPENAI_BASE_URL = os.getenv("VIVE_OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_API_KEY_ENV = os.getenv("VIVE_OPENAI_API_KEY_ENV", "OPENAI_API_KEY").strip()
OPENAI_CHAT_MODEL = os.getenv("VIVE_OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()

# --- Azure OpenAI ----------------------------------------------------------
# Azure-hosted OpenAI models via the AzureOpenAI client. Same key policy as
# above (store the env-var NAME, read the secret at run time). The "chat model"
# for Azure is the *deployment* name configured in your Azure resource.
AZURE_OPENAI_ENDPOINT = os.getenv("VIVE_AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_VERSION = os.getenv("VIVE_AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
AZURE_OPENAI_API_KEY_ENV = os.getenv("VIVE_AZURE_OPENAI_API_KEY_ENV", "AZURE_OPENAI_API_KEY").strip()
AZURE_OPENAI_DEPLOYMENT = os.getenv("VIVE_AZURE_OPENAI_DEPLOYMENT", "").strip()

# --- Foundry Local ---------------------------------------------------------
# Foundry Local exposes an OpenAI-compatible endpoint on localhost.
# If FOUNDRY_BASE_URL is set we use it directly; otherwise we try to discover
# the endpoint via the foundry-local-sdk; otherwise the app runs in OFFLINE
# mode (pre-authored content still works, AI features return a clear notice).
FOUNDRY_BASE_URL = os.getenv("FOUNDRY_BASE_URL", "").strip()
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "notneeded")

# Model aliases / ids. Use the CPU text model by default so first launch can
# download and run on a broad range of Foundry Local installs.
CHAT_MODEL = os.getenv("VIVE_CHAT_MODEL", "qwen3.5-2b-text-generic-cpu")
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

# Auto-prepare the chat model on startup. When enabled, ViveEnglish starts
# Foundry Local, downloads the configured chat model if it is missing, and loads
# it in the background so AI features become available after first launch.
AUTOLOAD_MODEL = os.getenv("VIVE_AUTOLOAD_MODEL", "1") not in ("0", "false", "False")

# Request timeout (seconds) for AI calls.
AI_TIMEOUT = float(os.getenv("VIVE_AI_TIMEOUT", "60"))

# --- Reading analysis (high-capability LLM gate) ---------------------------
# Paragraph-by-paragraph reading analysis needs a capable model: small local
# models (e.g. a 2B Foundry Local CPU model) mislabel structure or stall on the
# JSON contract, so they are restricted to the rule-based simple analysis.
# Hosted providers (ChatGPT/Azure/OpenAI-compatible) are always allowed.
# Local providers (Foundry/Ollama) are allowed only when the model's estimated
# parameter size is at least this many billions of parameters.
READING_MIN_LOCAL_PARAMS_B = float(os.getenv("VIVE_READING_MIN_PARAMS_B", "7"))
# Force-allow reading AI analysis regardless of provider/model size. Use when
# you know your local model is capable but its name carries no size hint.
READING_FORCE_AI = os.getenv("VIVE_READING_FORCE_AI", "0") not in ("0", "false", "False")
# Force-disable reading AI analysis (always use the simple rule-based analysis).
READING_DISABLE_AI = os.getenv("VIVE_READING_DISABLE_AI", "0") not in ("0", "false", "False")
# Log each paragraph that falls back to the simple analysis (and why) to
# DATA_DIR/reading_debug.log. Use this to diagnose why AI analysis "broke" for
# some paragraphs (no response / invalid JSON / failed validation). Off by default.
READING_DEBUG = os.getenv("VIVE_READING_DEBUG", "0") not in ("0", "false", "False")

APP_NAME = "ViveEnglish"
APP_VERSION = "1.0.0"
