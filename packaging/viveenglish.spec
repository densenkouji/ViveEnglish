# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ViveEnglish.

Build (from the project root, on the target OS):
    pip install pyinstaller foundry-local-sdk-winml openai fastapi "uvicorn[standard]"
    pyinstaller packaging/viveenglish.spec

Output: dist/ViveEnglish/ViveEnglish.exe  (one-folder build — easiest to ship
the native SDK DLLs alongside). The end user just double-clicks the .exe; no
Python and no `pip` required.

This bundles the Foundry Local SDK and its native binaries (the *.node / *.dll
hardware-acceleration files), the web frontend, lesson content, and the
placeholder illustrations.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# App data files (served by FastAPI). dest paths mirror the source layout so
# app/config.py (BASE_DIR == _MEIPASS) finds them.
datas += [
    ("web", "web"),
    ("app/content", "app/content"),
]

# uvicorn needs its dynamically-imported modules declared explicitly.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["app.main"]

# Pull in the Foundry Local SDK + native libraries (DLL / .node) and onnxruntime.
for pkg in ("foundry_local_sdk", "foundry_local_sdk_winml", "onnxruntime",
            "onnxruntime_genai", "openai"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # package not installed in this build environment — skip

block_cipher = None

a = Analysis(
    ["packaging/desktop_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ViveEnglish",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,            # no console window; set True to see logs
    icon=os.path.join("packaging", "icon.ico") if os.path.exists(os.path.join("packaging", "icon.ico")) else None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[], name="ViveEnglish",
)
