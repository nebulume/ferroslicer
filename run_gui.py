#!/usr/bin/env python3
"""
MeshyGen — Mesh Vase Slicer with GUI.

Usage:
    python run_gui.py

Or make executable:
    chmod +x run_gui.py && ./run_gui.py
"""

import sys
import os
from pathlib import Path

# Ensure project root is on the path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Activate venv if running from a bundled context
venv_lib = ROOT / "venv" / "lib"
if venv_lib.exists():
    import site
    for p in venv_lib.iterdir():
        sp = p / "site-packages"
        if sp.exists():
            site.addsitedir(str(sp))
            break

# Import and run
from gui.app import main

if __name__ == "__main__":
    main()
