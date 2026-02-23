# FerroSlicer — Packaging Notes

## What you need

| Tool | Install |
|------|---------|
| PyInstaller | `pip install pyinstaller` |
| Rust + Maturin | `rustup` + `pip install maturin` |
| macOS icon | `icon.icns` (place in this directory) |
| Linux icon | `icon.png` (place in this directory) |

---

## macOS `.app`

### 1. Build the Rust extension (if not already built)
```bash
./build_rust.sh
```

### 2. Run PyInstaller
```bash
pyinstaller packaging/ferroslicer.spec --clean
```

Output: `dist/FerroSlicer.app`

### 3. Test the bundle
```bash
open dist/FerroSlicer.app
```

### 4. Create a DMG (optional)
```bash
brew install create-dmg
create-dmg \
  --volname "FerroSlicer" \
  --window-size 600 400 \
  --icon-size 128 \
  --icon "FerroSlicer.app" 150 200 \
  --hide-extension "FerroSlicer.app" \
  --app-drop-link 450 200 \
  "FerroSlicer-0.4.1.dmg" \
  "dist/"
```

### Known macOS issues
- **Gatekeeper**: The app will be quarantined on first open. Users right-click → Open.
  To sign properly, you need an Apple Developer ID certificate (`codesign`).
- **OpenGL deprecation**: macOS has deprecated OpenGL in favour of Metal.
  PyQt6 still routes through OpenGL compatibility layer, which works fine through macOS 15.
- **Qt platform plugins**: PyInstaller hooks usually handle this. If the app fails with
  "could not find platform cocoa", add `platforms/libqcocoa.dylib` to binaries manually.

---

## Linux `.AppImage`

### 1. Build on a reasonably old base OS
Use Ubuntu 22.04 for maximum compatibility. Newer glibc symbols will not run on older distros.

### 2. Install AppImage tools
```bash
wget https://github.com/linuxdeploy/linuxdeploy/releases/latest/download/linuxdeploy-x86_64.AppImage
wget https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
chmod +x linuxdeploy-x86_64.AppImage appimagetool-x86_64.AppImage
```

### 3. Run the build script
```bash
bash packaging/build_appimage.sh
```

Output: `FerroSlicer-0.4.1-x86_64.AppImage`

### Known Linux issues
- **libGL**: If the AppImage fails with `libGL not found`, the target machine is missing
  mesa: `apt install libgl1`. PyInstaller bundles the library but some distros override.
- **Qt xcb platform**: Wayland and X11 both work but the `xcb` plugin needs `libxcb-*` installed.
  If running on a headless machine, use `QT_QPA_PLATFORM=offscreen` for testing.
- **Rust `.so` name**: The Rust extension compiles to `slicer_core.cpython-3XX-linux-gnu.so`.
  The spec file globs for this — make sure you built it on the same Python version you're packaging.

---

## Icon generation

No icon is bundled in the repo (add your own). Required formats:

- **macOS**: `icon.icns` — a multi-resolution ICNS file
  - Easiest: create a 1024×1024 PNG and run `iconutil` or use `sips`
  ```bash
  mkdir icon.iconset
  sips -z 1024 1024 icon.png --out icon.iconset/icon_512x512@2x.png
  sips -z 512  512  icon.png --out icon.iconset/icon_512x512.png
  sips -z 256  256  icon.png --out icon.iconset/icon_256x256.png
  sips -z 128  128  icon.png --out icon.iconset/icon_128x128.png
  iconutil -c icns icon.iconset
  mv icon.icns packaging/
  ```

- **Linux**: `icon.png` — 256×256 PNG

### Logo prompt (for Midjourney / DALL-E / Stable Diffusion)

```
A minimalist logo icon for a software tool called "FerroSlicer".
Iron letters "FS" in a bold geometric sans-serif typeface,
surface covered in deep rust and oxidation, patchy brown and orange iron oxide,
dark gunmetal background, industrial foundry aesthetic,
subtle horizontal wave/layer lines etched into the metal surface,
professional app icon, high contrast, clean silhouette.
--ar 1:1 --style raw --q 2
```

Alternative (text-free):
```
A single stylized wave cross-section, layered like a 3D print slice,
the surface corroded with rust and iron patina, sinusoidal wave profile visible,
deep shadow, dark industrial background, icon design, square format.
```
