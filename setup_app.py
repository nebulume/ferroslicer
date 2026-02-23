"""
py2app setup for building MeshyGen.app

Usage:
    pip install py2app
    python setup_app.py py2app

The resulting .app is in dist/MeshyGen.app
"""

from setuptools import setup

APP           = ["run_gui.py"]
APP_NAME      = "MeshyGen"
DATA_FILES    = [
    "config.json",
    ("project", ["project/__init__.py"]),
    ("project/core", [
        "project/core/__init__.py",
        "project/core/slicer.py",
        "project/core/stl_parser.py",
        "project/core/geometry_analyzer.py",
        "project/core/wave_generator.py",
        "project/core/spiral_generator.py",
        "project/core/gcode_generator.py",
        "project/core/base_integrity.py",
        "project/core/adaptive_behavior.py",
        "project/core/config.py",
        "project/core/logger.py",
        "project/core/utils.py",
        "project/core/exceptions.py",
        "project/core/preview.py",
        "project/core/validator.py",
    ]),
    ("gui", [
        "gui/__init__.py",
        "gui/app.py",
        "gui/main_window.py",
    ]),
    ("gui/widgets", [
        "gui/widgets/__init__.py",
        "gui/widgets/stl_viewer.py",
        "gui/widgets/path_preview.py",
        "gui/widgets/settings_panel.py",
    ]),
    ("gui/workers", [
        "gui/workers/__init__.py",
        "gui/workers/slicer_worker.py",
        "gui/workers/preview_worker.py",
    ]),
    ("gui/dialogs", [
        "gui/dialogs/__init__.py",
        "gui/dialogs/app_settings.py",
        "gui/dialogs/print_history.py",
    ]),
    ("klipper", ["klipper/__init__.py", "klipper/moonraker.py"]),
    ("db", ["db/__init__.py", "db/print_db.py"]),
]
OPTIONS = {
    "argv_emulation": False,
    "packages": [
        "PyQt6", "numpy", "requests",
        "project", "gui", "klipper", "db",
    ],
    "includes": ["slicer_core"],
    "iconfile": None,
    "plist": {
        "CFBundleName":        APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleVersion":     "0.4.0",
        "CFBundleIdentifier":  "com.meshygen.slicer",
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    name=APP_NAME,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
