#!/usr/bin/env bash
# ViveEnglish launcher for macOS / Linux.
set -e
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
  if ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
    echo "[ViveEnglish] Existing virtual environment is broken; recreating..."
    rm -rf .venv
  fi
fi

if [ ! -d .venv ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[ViveEnglish] python3 was not found. Install Python, then run this file again."
    exit 1
  fi
  echo "[ViveEnglish] Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "[ViveEnglish] Upgrading pip (needed to find prebuilt wheels)..."
python -m pip install --upgrade pip --quiet

echo "[ViveEnglish] Installing dependencies..."
pip install -r requirements.txt

if ! python -c "import foundry_local_sdk" >/dev/null 2>&1; then
  echo "[ViveEnglish] Installing Foundry Local SDK for AI speech features..."
  python -m pip install foundry-local-sdk || true
fi

if ! python -c "import foundry_local_sdk" >/dev/null 2>&1; then
  echo "[ViveEnglish] Foundry Local SDK is unavailable; recording transcription will ask for typed input."
fi

echo "[ViveEnglish] Starting server on http://localhost:8000 ..."
python run.py
