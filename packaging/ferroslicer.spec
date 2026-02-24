# FerroSlicer — PyInstaller spec
# Builds a single-folder app bundle containing Python + Rust extension.
#
# Usage (macOS):
#   pip install pyinstaller
#   pyinstaller packaging/ferroslicer.spec
#   open dist/FerroSlicer.app
#
# Usage (Linux):
#   pyinstaller packaging/ferroslicer.spec
#   # Then run packaging/build_appimage.sh

import sys
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent  # repo root

block_cipher = None

# ── Collect the Rust .so extension ───────────────────────────────────────────
import glob as _glob
rust_so = _glob.glob(str(ROOT / "slicer_core*.so")) + \
          _glob.glob(str(ROOT / "slicer_core*.pyd"))
binaries = [(p, ".") for p in rust_so]

# ── Qt plugins that OpenGL needs ──────────────────────────────────────────────
import PyQt6
qt_path = Path(PyQt6.__file__).parent
platform_plugins = []
for plat_dir in qt_path.glob("Qt6/plugins/platforms"):
    for f in plat_dir.iterdir():
        if f.is_file():
            platform_plugins.append((str(f), "PyQt6/Qt6/plugins/platforms"))

a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries + platform_plugins,
    datas=[
        (str(ROOT / "config.json"),   "."),
        (str(ROOT / "test_model.stl"), "."),
        (str(ROOT / "gui"),            "gui"),
        (str(ROOT / "project"),        "project"),
        (str(ROOT / "klipper"),        "klipper"),
        (str(ROOT / "db"),             "db"),
    ],
    hiddenimports=[
        "PyQt6.QtOpenGL",
        "PyQt6.QtOpenGLWidgets",
        "OpenGL.GL",
        "OpenGL.arrays.numpymodule",
        "numpy",
        "requests",
        "sqlite3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FerroSlicer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,      # no terminal window
    icon=str(ROOT / "packaging" / "icon.icns") if sys.platform == "darwin" else
         str(ROOT / "packaging" / "icon.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FerroSlicer",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FerroSlicer.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="com.nebulume.ferroslicer",
        info_plist={
            "CFBundleName": "FerroSlicer",
            "CFBundleDisplayName": "FerroSlicer",
            "CFBundleVersion": "0.4.1",
            "CFBundleShortVersionString": "0.4.1",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # allow dark mode
            "LSMinimumSystemVersion": "13.0",
            "NSHumanReadableCopyright": "Copyright © 2025 nebulume",
        },
    )
