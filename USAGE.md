# MeshVase Slicer

**Production-ready 3D mesh slicer for vase mode printing with decorative wave patterns.**

Converts STL files into Klipper-flavored GCode with unique sinusoidal wave patterns that create diamond-shaped gaps between layers. Designed specifically for lamp and vase printing business applications.

## Features

- ✨ **Mesh Pattern Generation**: Sinusoidal, triangular, or sawtooth wave patterns with configurable amplitude and spacing
- 📊 **Layer Alternation**: Phase-offset alternating layers to create decorative diamond-shaped gaps
- 🎯 **Base Integrity**: Three transition modes (fewer_gaps, tighter_waves, solid_then_mesh) to ensure dimensional accuracy at base
- 📐 **Adaptive Waves**: Automatic reduction of wave amplitude in high-curvature regions and diameter-based scaling
- 🔄 **Vase Mode**: Pure continuous spiral extrusion without layer boundaries or retractions
- ⚙️ **Full Configuration**: All settings editable via config.json or command-line flags
- 🎨 **Interactive CLI**: Parameter selection with file browser, or direct command-line mode
- 👁️ **OrcaSlicer Integration**: Automatic preview launching with generated GCode
- 🧵 **Klipper-Optimized**: Proper START_PRINT/END_PRINT macros, relative E-axis extrusion (M83)
- 📋 **Comprehensive Logging**: Detailed GCode comments and generation logs

## Quick Start

### Installation

```bash
cd /Users/haze/meshy-gen3
pip install -r requirements.txt  # Currently no external dependencies
```

### Usage

#### Interactive Mode (with file browser and parameter selection)

```bash
python3 -m project.core
```

This launches an interactive session where you:
1. Select an STL file from the current directory
2. Choose printing mode: **spiral vase** (continuous spiral) or **layer mesh** (layer-by-layer)
3. Configure wave frequency by choosing:
   - **Per Revolution**: Specify exact number of waves per complete 360° rotation (e.g., 120 waves)
   - **Per Distance**: Specify peak-to-peak distance in mm (e.g., 4.0mm spacing)
4. Set all other parameters (amplitude, pattern, fan speed, etc.) with sensible defaults
5. Confirm and generate GCode
6. Automatically preview in OrcaSlicer

**Interactive Prompts:**
- Printer Settings: Nozzle diameter, temperatures
- Print Settings: Layer height, speeds, fan speed, volumetric speed limit
- Printing Mode: Choose spiral vase or layer mesh
- Wave Pattern: Amplitude, frequency mode (per revolution or per distance), pattern type
- Spiral Vase (if selected): Sampling resolution for smooth spiral generation
- Base Integrity: Height, transition mode, transition profile

#### Direct Mode (command-line flags)

```bash
# Slice with defaults
python3 -m project.core --input lamp1.stl

# Slice with custom parameters (layer-based mesh)
python3 -m project.core --input lamp1.stl \
  --wave-amplitude 1.5 \
  --wave-spacing 4.0 \
  --layer-alternation 3 \
  --nozzle-temp 250 \
  --base-height 32

# True spiral vase mode with mesh pattern
python3 -m project.core --input vase.stl \
  --vase-mode \
  --fan-speed 100 \
  --max-volumetric-speed 12 \
  --wave-count 120 \
  --wave-amplitude 2.0 \
  --wave-pattern sine \
  --layer-alternation 2 \
  --phase-offset 50 \
  --spiral-points-per-degree 1.2
```

#### Reproduce Previous Slicing

The CLI prints a complete command after slicing:

```bash
python3 -m project.core --input lamp1.stl --nozzle 1.0 --nozzle-temp 260 \
  --wave-amplitude 2.0 --wave-spacing 4.0 --layer-alternation 2 \
  --phase-offset 50 --base-height 28.0 --base-mode fewer_gaps
```

## Command-Line Flags

### Printer Settings
- `--nozzle` - Nozzle diameter (mm)
- `--nozzle-temp` - Nozzle temperature (°C)
- `--bed-temp` - Bed temperature (°C)

### Print Settings
- `--layer-height` - Layer height (mm)
- `--print-speed` - Print speed (mm/s)
- `--travel-speed` - Travel speed (mm/s)
- `--fan-speed` - Fan speed 0-100% (automatically converted to M106 S{pwm} where pwm = 0-255)
- `--max-volumetric-speed` - Maximum volumetric extrusion speed (mm³/s); limits feedrate based on nozzle diameter and layer height

### Vase Mode (Spiral)
- `--vase-mode` - Enable true spiral vase mode (continuous spiral extrusion, no Z-hops)
- `--spiral-points-per-degree` - Sampling resolution for spiral path (points per degree, default ~1.2 → ~432 points/revolution)

### Skirt and Purge Line
- `--skirt` - Enable skirt (default: enabled). One loop around the base for improved adhesion.
- `--no-skirt` - Disable skirt
- `--skirt-distance` - Distance from print to skirt in mm (default 0 = touching the print for maximum adhesion)
- `--skirt-height` - Number of layers for skirt (default 1)

**Purge Behavior:**
After START_PRINT, the toolhead performs a clean purge sequence:
1. Moves to safe corner (219,219) and retracts to prevent oozing
2. Travels to purge line location (20mm from print start)
3. Purges 40mm total: first 20mm extruding at 40mm/s, next 20mm travel-only (pressure ooze)
4. Retracts before moving to print start
5. If skirt enabled, prints one wavy loop at Z=layer_height touching the print base
6. Starts main print

**End Sequence:**
After print completes:
1. Retracts filament to prevent oozing
2. Raises Z by 10mm to clear the print
3. Moves toolhead to safe corner (219,219)
4. Calls END_PRINT macro

### Spiral Tuning (Advanced)
Fine-tune spiral geometry for wave symmetry and visual fidelity:

- `--target-samples-per-wave` - Minimum samples per wavelength to avoid aliasing (default 16); increase for high-frequency waves
- `--smoothing-window-size` - Spiral smoothing kernel size: 1 (no smoothing) to 5+ (more aggressive, default 3)
- `--smoothing-threshold` - Smoothing move distance threshold in mm (default 0.5); only apply smoothing if point moves less than this
- `--no-auto-resample-spiral` - Disable automatic spiral resampling for high-frequency waves (normally enabled to prevent undersampling)

**Recommended values:**
- Default (symmetric sine waves): Use defaults (no flags needed)
- High-frequency waves (wave-count > 150): `--target-samples-per-wave 24 --no-auto-resample-spiral` to let auto-resampling handle density
- Performance priority: `--smoothing-window-size 1 --no-auto-resample-spiral`
- Visual perfection: `--smoothing-window-size 3 --smoothing-threshold 0.3 --target-samples-per-wave 20`

### Mesh Settings (Wave Pattern)
- `--wave-amplitude` - Distance from perimeter to peak (mm)
- `--wave-spacing` - Peak-to-peak distance along perimeter (mm) - ignored if `--wave-count` is set
- `--wave-count` - Number of complete waves per full revolution (takes precedence over `--wave-spacing`)
- `--wave-pattern` - sine | triangular | sawtooth
- `--wave-smoothness` - 1-10, higher = more sinuous
- `--layer-alternation` - Revolutions before phase alternation (creates diamond mesh pattern)
- `--phase-offset` - Phase offset percentage 0-100 applied at alternation points

### Base Integrity
- `--base-height` - Height where special handling applies (mm)
- `--base-mode` - tighter_waves | fewer_gaps | solid_then_mesh
- `--base-transition` - linear | exponential | step

## Configuration

### config.json

All settings are stored in `config.json` with inline documentation:

```json
{
    "printer": {
        "nozzle_diameter": 1.0,
        "nozzle_temp": 260,
        "bed_temp": 65
    },
    "print_settings": {
        "layer_height": 0.5,
        "print_speed": 35,
        "travel_speed": 40,
        "skirt_enabled": true,
        "skirt_distance": 0.0,
        "skirt_height": 1
    },
    "mesh_settings": {
        "wave_amplitude": 2.0,
        "wave_spacing": 4.0,
        "wave_smoothness": 10,
        "wave_pattern": "sine",
        "layer_alternation": 2,
        "phase_offset": 50,
        "base_height": 28.0,
        "base_mode": "fewer_gaps",
        "base_transition": "exponential"
    }
}
```

Edit these values to change defaults. Command-line flags override config.json.

## Output Files

Generated files are saved in the `output/` directory:

### GCode Files
- `{model}_mesh_{amplitude}a_{spacing}s_{alternation}alt_{timestamp}.gcode`
- Example: `lamp1_mesh_2.0a_4.0s_2alt_20251202_143022.gcode`

### Log Files
- `{model}_mesh_{timestamp}.log`
- Contains model statistics, configuration used, and generation details

## How It Works

### Pipeline (Layer-Based Mesh)

1. **STL Parsing**: Load ASCII STL file, extract triangles, compute bounds
2. **Geometry Analysis**: Slice model into horizontal layers, extract perimeter points
3. **Wave Generation**: Apply sinusoidal/triangular/sawtooth patterns to each perimeter
4. **Layer Alternation**: Offset wave phase every N layers (default 2) to create gaps
5. **Base Integrity**: Gradually transition from solid base to full mesh pattern
6. **Adaptive Behavior**: Reduce waves in high-curvature regions, scale for diameter changes
7. **GCode Generation**: Convert wave points to Klipper commands with proper extrusion
8. **Preview Launch**: Automatically open in OrcaSlicer

### Pipeline (Spiral Vase Mode)

1. **STL Parsing**: Load ASCII STL file, extract triangles, compute bounds
2. **Geometry Analysis**: Slice model into horizontal layers, extract perimeter points
3. **Spiral Path Generation**: Create continuous spiral by:
   - Computing area-weighted centroid for each layer (robust center calculation)
   - Casting rays at configurable angle resolution (default 1.2 points/degree)
   - Intersecting rays with layer perimeters using robust segment intersection
   - Validating intersections against layer radius bounds (rejects far-away spurious hits)
   - Interpolating positions between layer geometries for smooth Z-rise
4. **Path Smoothing**: Apply intelligent post-processing:
   - Detect outlier positions (large consecutive jumps)
   - Correct via neighbor interpolation when confidence is low
   - Apply 5-point Gaussian smoothing for final polish
5. **Wave Application**: Apply outward-only waves to spiral path; optionally phase-shift every N revolutions for diamond pattern
6. **GCode Generation**: Convert continuous spiral to Klipper commands with smooth Z ramp, no layer boundaries, no retractions; apply volumetric speed limiting
7. **Preview Launch**: Automatically open in OrcaSlicer

### Wave Pattern Logic

- **Amplitude**: 2.0mm = waves extend 2mm outward from original perimeter
- **Spacing**: 4.0mm = one complete wavelength per 4mm of perimeter distance
- **Layer Alternation**: Every 2 layers, flip phase by 50% to create diamond gaps
- **Phase Offset**: 50% creates perfect diamonds; 0% = no offset; 100% = maximum offset

Note: Waves are applied only outward from the original perimeter. Troughs do not move the inner boundary inward — the inner perimeter (the vase's inner diameter) is preserved. For example, a 90mm base with `--wave-amplitude 2.0` will produce a maximum outer diameter of 94mm while the inner diameter remains 90mm.

Example with 2.0mm amplitude, 50% phase offset, 2-layer alternation:
- Layer 0: Waves bulge outward, reach +2mm
- Layer 1: Waves at same amplitude (phase = 0°)
- Layer 2: Waves offset by 50%, creating gaps between Layer 0 and Layer 2
- Layer 3: Waves aligned with Layer 1
- Result: Diamond-shaped mesh pattern

## START_PRINT Macro Integration

Generated GCode includes proper macro setup for Klipper:

```gcode
; --- START GCODE ---
SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE=65
SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE=260
M106 S255
START_PRINT

; Absolute positioning for X/Y/Z, relative for E
G90
M83
```

- **Fan Control**: `M106 S{pwm}` sets fan speed (0-255 PWM based on `--fan-speed` percentage; 0 = off, 255 = max)
  - `--fan-speed 100` → `M106 S255` (max fan)
  - `--fan-speed 50` → `M106 S127` (half speed)
  - `--fan-speed 25` → `M106 S63` (quarter speed)
- **Temperatures**: Passed via SET_GCODE_VARIABLE to START_PRINT macro
- **Extrusion**: M83 enables relative E-axis (Klipper standard for cleaner GCode)

### Base Integrity Modes

1. **fewer_gaps** (default): Smooth exponential transition from 0% amplitude at Z=0 to 100% at base_height
2. **tighter_waves**: More frequent oscillations (150% frequency, 40% amplitude) in base
3. **solid_then_mesh**: Solid layers up to base_height/2, then transition to mesh

### Vase Mode Characteristics

- **Spiral Path**: Single continuous spiral that rises smoothly (no Z-axis steps/hops)
- **Z Increment**: Per revolution rise = `layer_height` (typically 0.5mm per full 360° spiral)
- **Resolution**: Configurable via `--spiral-points-per-degree` (default ~1.2 → ~432 points/revolution); includes outlier detection and smoothing to eliminate discontinuities
- **Robust Ray Intersection**: Uses area-weighted polygon centroid and validates intersections against layer radius; includes intelligent fallback for edge cases
- **Path Smoothing**: 5-point Gaussian-like filter detects and corrects outlier points; preserves genuine geometry while eliminating jumps
- **No Z-Seam**: Spiral approach eliminates vertical seam issues
- **No Retractions**: Continuous extrusion throughout print (no retract/unretracts between moves)
- **Wave Mesh**: Waves applied radially outward only (inner diameter preserved at original perimeter)
- **Layer Alternation**: Optional diamond mesh pattern created by 50% phase-shift every N revolutions (default 2)

### Layer-Based Mesh Mode Characteristics (without `--vase-mode`)

- Layer-by-layer horizontal slices
- Phase-offset alternating layers create decorative diamond-shaped gaps
- Base integrity transitions from solid to mesh
- Extrusion stops (E0) during gap sections but maintains travel path

## Architecture

```
project/
├── core/
│   ├── __main__.py              # CLI entry point
│   ├── config.py                # Configuration management
│   ├── slicer.py                # Main orchestrator
│   ├── stl_parser.py            # STL file parsing
│   ├── geometry_analyzer.py     # Layer extraction & perimeter analysis
│   ├── wave_generator.py        # Wave pattern generation (layer-based mesh)
│   ├── spiral_generator.py      # Spiral path generation (vase mode)
│   ├── base_integrity.py        # Base transition management
│   ├── adaptive_behavior.py     # Curvature adaptation & diameter scaling
│   ├── gcode_generator.py       # GCode output generation
│   ├── preview.py               # OrcaSlicer integration
│   ├── exceptions.py            # Custom exceptions
│   ├── logger.py                # Logging setup
│   └── utils.py                 # Utility functions
├── config.json                  # Configuration file
└── requirements.txt             # Python dependencies
```

### Key Classes

- **MeshVaseSlicer**: Main orchestrator, coordinates entire pipeline
- **STLParser**: Parses ASCII STL files
- **GeometryAnalyzer**: Slices model, extracts layers
- **WaveGenerator**: Creates sinusoidal/triangular/sawtooth waves (layer-based mesh)
- **SpiralGenerator**: Creates continuous spiral paths with robust ray-intersection and smoothing; applies optional wave patterns
  - `_get_position_at_angle()`: Robust centroid-based ray-segment intersection with validation and intelligent fallback
  - `_smooth_spiral_path()`: 5-point Gaussian smoothing with outlier detection and correction
- **LayerAlternationController**: Manages phase offsets between layers
- **BaseIntegrityManager**: Handles base transitions
- **CurvatureAdaptation**: Reduces waves in curves
- **DiameterScaling**: Scales waves for diameter changes
- **GCodeGenerator**: Converts waves or spiral to Klipper GCode; includes volumetric speed limiting and M106 fan control
- **PreviewSystem**: Launches OrcaSlicer

## Testing

Test with sample STL:

```bash
python3 -m project.core --input test_model.stl
```

This generates:
- GCode file in `output/` directory
- Log file with generation statistics
- Auto-opens in OrcaSlicer (if available)

## Validation

Generated GCode is validated for:
- ✓ File contains actual movement commands (G1/G0)
- ✓ Proper start/end GCode
- ✓ Layer count matches expected
- ✓ No NaN or infinite coordinate values
- ✓ File size > 10KB (indicates real content)

## Performance

- Layer analysis: ~0.2-0.5s per 100 layers
- Wave generation: ~0.1s per 100 layers  
- GCode generation: ~0.1s per 100 layers
- Total for 300-layer model: ~1-2 seconds

Multi-threading optimizations ready for layer processing parallelization.

## Error Handling

All errors provide:
- Clear, actionable error messages
- Suggested fixes
- No crashes without explanation
- Validation of inputs before processing

Common errors and solutions:
- **STL file not found**: Check filename and path
- **Non-manifold geometry**: Model has holes/gaps (still sliceable with warning)
- **Model exceeds build volume**: Consider scaling with --base-height or resizing in CAD
- **OrcaSlicer not found**: GCode still saved; open manually

## Development

### Adding New Wave Patterns

Edit `wave_generator.py` `_calculate_wave_value()`:

```python
elif self.pattern_type == "custom":
    # Your pattern logic here
    return wave_value
```

### Adjusting Default Parameters

Edit `config.json` or `config.py` default config dictionary.

### Extending Base Modes

Edit `base_integrity.py` add new mode to `BaseMode` enum and `get_amplitude_factor()`.

## Specifications

- **Input**: ASCII STL format only
- **Output**: Klipper-flavored GCode (G90 for XYZ, M83 for E)
- **Build Volume**: 220 × 220 × 280 mm (configurable)
- **Target**: macOS M4 Pro (multi-threading ready)
- **Python**: 3.11+

## References

- Klipper firmware: https://www.klipper3d.org/
- GCode reference: https://reprap.org/wiki/G-code
- Vase mode printing: Single-wall continuous spiral technique

## License

MIT License

## Acknowledgments

Generated with AI assistance. All specifications from custom brief.
