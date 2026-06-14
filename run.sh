#!/usr/bin/env bash
# ViveEnglish launcher for macOS / Linux.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[ViveEnglish] Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "[ViveEnglish] Upgrading pip (needed to find prebuilt wheels)..."
python -m pip install --upgrade pip --quiet

echo "[ViveEnglish] Installing dependencies..."
pip install -r requirements.txt

echo "[ViveEnglish] Starting server on http://localhost:8000 ..."
python run.py
