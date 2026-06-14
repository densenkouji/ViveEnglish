"""Entry point for the packaged (PyInstaller) ViveEnglish desktop build.

Unlike run.py (which passes an import string to uvicorn for --reload support),
this imports the app object directly, which is what a frozen executable needs.
"""
from __future__ import annotations

import os
import socket
import threading
import webbrowser

import uvicorn

from app.main import app


def _free_port(preferred: int = 8000) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
        return preferred
    except OSError:
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(("127.0.0.1", 0))
        port = s2.getsockname()[1]
        s2.close()
        return port
    finally:
        s.close()


def main() -> None:
    port = int(os.getenv("PORT", str(_free_port(8000))))
    if os.getenv("VIVE_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
