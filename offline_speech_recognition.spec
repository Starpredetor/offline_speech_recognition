# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_dir = Path.cwd()
models_dir = project_dir / "models"
datas = []

if models_dir.exists():
    datas.append((str(models_dir), "models"))


a = Analysis(
    ["main.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "sounddevice",
        "vosk",
        "argostranslate.package",
        "argostranslate.translate",
        "langdetect",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OfflineSpeechRecognition",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OfflineSpeechRecognition",
)
