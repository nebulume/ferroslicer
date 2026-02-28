# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_gui.py'],
    pathex=['/Users/haze/meshy-gen3'],
    binaries=[('venv/lib/python3.13/site-packages/slicer_core/slicer_core.cpython-313-darwin.so', 'slicer_core')],
    datas=[('config.json', '.'), ('test_model.stl', '.'), ('gui/resources', 'gui/resources'), ('packaging/entitlements.plist', '.')],
    hiddenimports=['project', 'project.core', 'project.core.adaptive_behavior', 'project.core.api_client', 'project.core.base_integrity', 'project.core.config', 'project.core.database', 'project.core.exceptions', 'project.core.gcode_generator', 'project.core.geometry', 'project.core.geometry_analyzer', 'project.core.geometry_test', 'project.core.logger', 'project.core.preview', 'project.core.slicer', 'project.core.spiral_generator', 'project.core.stl_parser', 'project.core.utils', 'project.core.validator', 'project.core.wave_generator', 'gui', 'gui.app', 'gui.main_window', 'gui.dialogs.app_settings', 'gui.dialogs.print_history', 'gui.dialogs.gcode_library', 'gui.dialogs.setup_wizard', 'gui.dialogs.test_layer_dialog', 'gui.widgets.settings_panel', 'gui.widgets.stl_viewer', 'gui.widgets.toolpath_viewer', 'gui.widgets.file_browser', 'gui.workers.slicer_worker', 'gui.workers.preview_worker', 'klipper.moonraker', 'db.print_db', 'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets', 'PyQt6.QtSvg', 'PyQt6.QtSvgWidgets', 'OpenGL.GL', 'OpenGL.arrays.numpymodule', 'numpy', 'requests', 'sqlite3', 'slicer_core'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FerroSlicer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='packaging/entitlements.plist',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FerroSlicer',
)
app = BUNDLE(
    coll,
    name='FerroSlicer.app',
    icon='packaging/icon.icns',
    bundle_identifier='com.ferroslicer.app',
    info_plist={
        # macOS Local Network privacy — required for Klipper/Moonraker connectivity
        'NSLocalNetworkUsageDescription':
            'FerroSlicer needs local network access to connect to your Klipper printer (Moonraker API).',
        # macOS Application Firewall — allow outgoing connections without repeated prompts
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
        'CFBundleShortVersionString': '0.4.0',
        'CFBundleVersion': '0.4.0',
        'NSHumanReadableCopyright': '© 2025 FerroSlicer',
    },
)
