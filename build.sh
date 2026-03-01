#!/usr/bin/env bash
# FerroSlicer build script — Rust + PyInstaller + codesign
# Run from the project root: ./build.sh

set -e
cd "$(dirname "$0")"

CERT_NAME="FerroSlicer Dev"
APP_BUNDLE="dist/FerroSlicer.app"
BUNDLE_ID="com.ferroslicer.app"

# ── 1. Rebuild Rust slicer core ──────────────────────────────────────────────
echo "▶ Building Rust slicer core…"
PATH="$HOME/.cargo/bin:$PATH" VIRTUAL_ENV="$PWD/venv" \
  venv/bin/python -m maturin develop --release -m slicer_core/Cargo.toml

# ── 2. PyInstaller ───────────────────────────────────────────────────────────
echo "▶ Running PyInstaller…"
rm -rf "build" "dist"
venv/bin/python -m PyInstaller FerroSlicer.spec

# ── 3. Code signing with stable identity ─────────────────────────────────────
# macOS tracks Local Network & Firewall permissions by code signature.
# Signing with a consistent self-signed cert means permissions survive rebuilds.

# Check if our dev cert already exists
if security find-identity -v -p codesigning 2>/dev/null | grep -q "\"$CERT_NAME\""; then
    echo "▶ Signing with '$CERT_NAME' (stable identity — permissions will persist)…"
    codesign --deep --force \
        --entitlements packaging/entitlements.plist \
        --sign "$CERT_NAME" \
        "$APP_BUNDLE"
else
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  One-time setup: create a self-signed code-signing certificate  ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  1. Open 'Keychain Access'                                       ║"
    echo "║  2. Menu → Certificate Assistant → Create a Certificate…        ║"
    echo "║  3. Name: FerroSlicer Dev                                        ║"
    echo "║  4. Identity Type: Self Signed Root                              ║"
    echo "║  5. Certificate Type: Code Signing                               ║"
    echo "║  6. Click Continue → Create → Done                               ║"
    echo "║  Then re-run ./build.sh                                          ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "▶ Falling back to ad-hoc signing (permissions reset each build)…"
    codesign --deep --force \
        --entitlements packaging/entitlements.plist \
        --sign - \
        "$APP_BUNDLE"
fi

# ── 4. Refresh dock/Spotlight icon cache ─────────────────────────────────────
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
  -f "$APP_BUNDLE" 2>/dev/null || true

echo ""
echo "✓ Build complete: $APP_BUNDLE"
echo ""
echo "  First launch: macOS will ask 'FerroSlicer would like to find and"
echo "  connect to devices on your local network' — click Allow."
echo "  With the stable cert, this permission will persist for future builds."
