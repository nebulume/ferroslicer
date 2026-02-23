#!/usr/bin/env bash
# FerroSlicer — Linux AppImage build script
#
# Prerequisites:
#   pip install pyinstaller
#   wget https://github.com/linuxdeploy/linuxdeploy/releases/latest/download/linuxdeploy-x86_64.AppImage
#   wget https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
#   chmod +x linuxdeploy-x86_64.AppImage appimagetool-x86_64.AppImage
#
# Run from the repo root:
#   bash packaging/build_appimage.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$REPO_ROOT/dist/FerroSlicer"
APPDIR="$REPO_ROOT/dist/FerroSlicer.AppDir"
VERSION="0.4.1"

echo "── Building PyInstaller bundle ──────────────────────────────────────────"
cd "$REPO_ROOT"
pyinstaller packaging/ferroslicer.spec --clean

echo "── Assembling AppDir ────────────────────────────────────────────────────"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy PyInstaller output
cp -r "$DIST/." "$APPDIR/usr/bin/"

# Desktop entry
cat > "$APPDIR/usr/share/applications/ferroslicer.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=FerroSlicer
Comment=Mesh-wave slicer for 3D printing sinusoidal surface patterns
Exec=FerroSlicer
Icon=ferroslicer
Categories=Graphics;Engineering;
Terminal=false
EOF

# Icon (copy PNG if exists)
if [ -f "$REPO_ROOT/packaging/icon.png" ]; then
    cp "$REPO_ROOT/packaging/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/ferroslicer.png"
    cp "$REPO_ROOT/packaging/icon.png" "$APPDIR/ferroslicer.png"
fi

# AppRun symlink
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=$(dirname "$SELF")
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/FerroSlicer" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "── Packing AppImage ─────────────────────────────────────────────────────"
ARCH=x86_64 ./appimagetool-x86_64.AppImage "$APPDIR" \
    "FerroSlicer-${VERSION}-x86_64.AppImage"

echo "── Done: FerroSlicer-${VERSION}-x86_64.AppImage ─────────────────────────"
