# FerroSlicer

> *A mesh-wave slicer for 3D printing sinusoidal surface patterns on vase-mode and layered models.*

**FerroSlicer** converts STL files into GCode with mathematically precise wave patterns printed into the surface of every layer. The result is objects that appear woven, corrugated, or organically textured — produced from a single continuous extrusion path.

Built for Klipper printers. Powered by a Rust extension for sub-second slice times.

---

## What it does

Instead of printing flat, uniform layers, FerroSlicer displaces each layer's extrusion path outward and inward in a sinusoidal wave. Wave amplitude, frequency, phase alternation, and pattern shape are all tunable. The effect:

- **Mesh lamps** — light passes through the wave-gaps between layers
- **Textured vases** — surface appears woven or latticed
- **Spiral prints** — continuous vase-mode path with Z-gradient and seam control

---

## Screenshots

<!-- Add screenshots after first run -->

| STL Preview | GCode Viewer | Settings | Gcode Explorer |
|---|---|---|---|
| <img width="1478" height="935" alt="Screenshot 2026-02-25 at 00 14 39" src="https://github.com/user-attachments/assets/adbbc8bc-a474-4c1e-b076-8c2099c8353b" /> | <img width="1480" height="938" alt="Screenshot 2026-02-25 at 00 13 53" src="https://github.com/user-attachments/assets/60a4c5c1-a0df-4233-81cb-6c03af218452" /> | <img width="677" height="955" alt="Screenshot 2026-02-25 at 00 15 26" src="https://github.com/user-attachments/assets/533c1669-d650-41c0-b936-f3d90c89a8f0" /> | <img width="1401" height="816" alt="Screenshot 2026-02-25 at 00 26 12" src="https://github.com/user-attachments/assets/659eaf45-4b10-422e-91cd-243ae06f0c1d" /> |

---

## Features

- **Vase mode** — continuous spiral path, no layer seams (Rust-accelerated)
- **Layer mesh mode** — traditional layer-by-layer with alternating wave phases
- **Wave patterns** — sine, square, triangle, sawtooth, custom smoothness
- **Wave asymmetry** — unequal inward/outward displacement
- **Base integrity** — solid/dense base transitions so the model doesn't fall over
- **Diameter scaling** — auto-reduces amplitude on high-curvature geometry
- **Seam control** — seam position, shift, phase transition between layers
- **Printer profiles** — per-printer nozzle/bed/firmware/kinematics settings
- **Klipper upload** — one-click send-to-printer via Moonraker REST API
- **3D preview** — OpenGL toolpath viewer with per-layer navigation
- **Print history** — SQLite log of every job with settings snapshot

---

## Requirements

### Runtime
- Python 3.11+
- macOS 13+ or Linux (Ubuntu 22.04+)
- OpenGL 3.3+ capable GPU (integrated is fine)
- Klipper + Moonraker (optional; only needed for direct upload)

### Python packages
```
PyQt6 >= 6.4
numpy >= 1.24
requests >= 2.28
PyOpenGL >= 3.1
PyOpenGL_accelerate >= 3.1
```

### Build (for Rust extension)
- Rust toolchain (`rustup`)
- `maturin` (`pip install maturin`)

---

## Installation

### Option A — Run from source

```bash
git clone https://github.com/nebulume/ferroslicer.git
cd ferroslicer

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
# venv\Scripts\activate           # Windows (not supported yet)

# Install Python dependencies
pip install -r requirements.txt

# Build the Rust extension (required for vase-mode speed)
./build_rust.sh

# Launch
python run_gui.py
```

### Option B — macOS .app bundle

Download the latest `.dmg` from [Releases](https://github.com/nebulume/ferroslicer/releases), mount it, and drag **FerroSlicer.app** to your Applications folder.

### Option C — Linux .AppImage

Download the `.AppImage` from [Releases](https://github.com/nebulume/ferroslicer/releases):

```bash
chmod +x FerroSlicer-x86_64.AppImage
./FerroSlicer-x86_64.AppImage
```

---

## Building the Rust extension manually

The Rust extension (`slicer_core`) provides:
- Parallel layer slicing via Rayon
- Spiral path generation with wave application (~78ms for 360k-point paths)
- Binary STL parsing

```bash
# Requires: rustup, maturin in your venv
./build_rust.sh

# Or manually:
VIRTUAL_ENV=$PWD/venv \
PATH="$HOME/.cargo/bin:$PATH" \
venv/bin/python -m maturin develop --release -m slicer_core/Cargo.toml
```

The Python slicer falls back to pure-Python spiral generation if the Rust extension is not available. Vase mode will still work but will be significantly slower on large models.

---

## Configuration

**`config.json`** — slicer defaults loaded at startup:

| Key | Default | Description |
|-----|---------|-------------|
| `mesh_settings.wave_amplitude` | `5.0` | Wave peak-to-wall displacement (mm) |
| `mesh_settings.wave_spacing` | `4.0` | Distance between wave peaks (mm) |
| `mesh_settings.wave_pattern` | `"sine"` | Pattern: sine, square, triangle, sawtooth |
| `mesh_settings.layer_alternation` | `2` | Every N layers, phase flips 180° |
| `mesh_settings.phase_offset` | `50` | Flip offset as percentage of half-cycle |
| `mesh_settings.base_height` | `28.0` | Height of solid/dense base (mm) |
| `mesh_settings.seam_position` | `"auto"` | Seam: auto, left, right, front, back |
| `print_settings.layer_height` | `0.5` | Layer height (mm) |
| `print_settings.print_speed` | `8` | Print speed (mm/s) |
| `print_settings.vase_mode` | `false` | Enable continuous spiral path |
| `printer.nozzle_diameter` | `1.0` | Nozzle diameter (mm) |
| `printer.nozzle_temp` | `240` | Nozzle temperature (°C) |
| `printer.bed_temp` | `65` | Bed temperature (°C) |

**`data/app_settings.json`** — created on first run, stores printer profiles, Moonraker IP, custom start/end GCode. This file is excluded from version control.

---

## Klipper / Moonraker setup

In App Settings → Moonraker, enter your printer's IP and port (default port 80 if using an nginx proxy, or 7125 for direct Moonraker access). FerroSlicer uses the standard Moonraker REST API:

- `POST /server/files/upload` — uploads the GCode file
- `POST /printer/print/start` — starts the print job
- `GET /printer/objects/query` — polls printer state

---

## Project structure

```
ferroslicer/
├── project/core/          # Slicer pipeline (Python)
│   ├── stl_parser.py      # ASCII + binary STL reader
│   ├── geometry_analyzer.py  # Layer extraction, perimeter analysis
│   ├── wave_generator.py  # Wave point generation per layer
│   ├── spiral_generator.py   # Vase-mode spiral path
│   ├── gcode_generator.py # GCode assembly, skirt, purge, seam
│   └── slicer.py          # Top-level CLI pipeline
├── slicer_core/           # Rust PyO3 extension
│   └── src/lib.rs         # Rayon-parallel slice + spiral
├── gui/                   # PyQt6 GUI
│   ├── main_window.py     # Application shell
│   ├── widgets/           # STL viewer, toolpath viewer, settings panel
│   ├── dialogs/           # App settings, print history, send-to-printer
│   └── workers/           # Background slicer thread
├── klipper/               # Moonraker REST client
├── db/                    # SQLite print history
├── config.json            # Default slicer configuration
├── requirements.txt       # Python dependencies
├── build_rust.sh          # Rust extension build script
├── run_gui.py             # GUI entry point
└── test_model.stl         # Bundled test model (vase)
```

---

## Performance

Tested on a 52,000-triangle / 321-layer vase model:

| Stage | Time |
|-------|------|
| STL parse (Rust binary) | ~12ms |
| Geometry analysis (Rust parallel) | ~48ms |
| Spiral generation + wave application | ~78ms |
| GCode assembly (361k points) | ~420ms |
| **Total** | **~0.64s** |

Pure-Python spiral generation for the same model: ~4 minutes.

---

## CLI usage

FerroSlicer also has a command-line interface for headless/scripted use:

```bash
python -m project path/to/model.stl \
    --wave-amplitude 6 \
    --wave-spacing 4 \
    --layer-alternation 2 \
    --vase-mode \
    --output output/model.gcode
```

Run `python -m project --help` for full option list.

---

## Building distribution packages

### macOS `.app`

```bash
pip install pyinstaller
pyinstaller packaging/ferroslicer.spec
# Output: dist/FerroSlicer.app
```

### Linux `.AppImage`

```bash
pip install pyinstaller
pyinstaller packaging/ferroslicer.spec
# Then use linuxdeploy + AppImage tools (see packaging/build_appimage.sh)
```

See [`packaging/`](packaging/) for full build scripts and notes.

---

## License

Copyright © 2025 nebulume

Licensed under the **Apache License 2.0** with the **Commons Clause** restriction.

In plain terms:
- You may use, modify, and distribute this software for any **non-commercial** purpose.
- You may not sell this software or a product primarily derived from it without a separate commercial agreement with the original author.
- If you fork this project and derive revenue from it, you must contact the author to arrange fair compensation.

See [`LICENSE`](LICENSE) for the full legal text.

For commercial licensing enquiries: open an issue or contact via GitHub.

---

## Contributing

Pull requests are welcome for bug fixes and non-competing features. For significant new features, open an issue first to discuss direction.

---

*FerroSlicer — ferro, from Latin ferrum (iron). Every layer, oxidized into form.*
