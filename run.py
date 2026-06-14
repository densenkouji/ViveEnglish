#!/usr/bin/env python3
"""ViveEnglish launcher.

Usage:
    python run.py            # start on http://localhost:8000
    PORT=9000 python run.py  # custom port

This simply boots the FastAPI app with uvicorn and opens the browser.
"""
import os
import webbrowser
import threading

import uvicorn

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")


def _open_browser():
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    if os.getenv("VIVE_NO_BROWSER") != "1":
        threading.Timer(1.5, _open_browser).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=bool(os.getenv("VIVE_RELOAD")))
