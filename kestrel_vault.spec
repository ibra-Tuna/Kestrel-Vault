# PyInstaller spec for Kestrel Vault — single-file build.
#
#   Build:  pyinstaller --noconfirm --clean kestrel_vault.spec
#   Output: dist/KestrelVault.exe
#
# Icon notes
# ----------
# app.ico is used twice:
#   * as the icon of the .exe itself (EXE(icon=...))
#   * bundled into the package at kestrel_vault/app.ico so the runtime
#     window icon loads in both dev and frozen modes.

import os
from PyInstaller.utils.hooks import collect_data_files

ICON_FILE = "app.ico"
HAVE_ICON = os.path.exists(ICON_FILE)

datas = [
    ("kestrel_vault/style.qss.tpl", "kestrel_vault"),   # stylesheet template
]
datas += collect_data_files("diceware")                 # EFF word lists
datas += collect_data_files("zxcvbn")                   # frequency lists

if HAVE_ICON:
    # (source, dest_dir) — file keeps its name, lands at kestrel_vault/app.ico.
    datas.append((ICON_FILE, "kestrel_vault"))

a = Analysis(
    ["run.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=[
        "argon2._ffi",       # argon2-cffi loads its C extension lazily
        "PySide6.QtSvg",     # QSvgRenderer for icons.py
    ],
    excludes=[
        "tkinter",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtPdf",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml",
        "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KestrelVault",
    icon=ICON_FILE if HAVE_ICON else None,
    console=False,
    upx=False,
    disable_windowed_traceback=False,
)