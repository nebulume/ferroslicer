#!/bin/bash
# Rebuild the Rust slicer extension after code changes.
# Run this from the project root.
set -e
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export VIRTUAL_ENV="$PWD/venv"
venv/bin/python -m maturin develop --release -m slicer_core/Cargo.toml
echo "Rust extension built and installed."
