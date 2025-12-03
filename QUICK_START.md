# MeshVase Slicer - Quick Reference

## Run It

### Interactive (recommended)
```bash
python3 -m project.core
```
- Selects file
- Sets parameters
- Generates GCode
- Opens in OrcaSlicer

### Command-Line
```bash
python3 -m project.core --input lamp.stl --wave-amplitude 2.0
```

### Help
```bash
python3 -m project.core --help
```

## Settings

### Must-Know Parameters

| Parameter | Range | Default | Effect |
|-----------|-------|---------|--------|
| `--wave-amplitude` | 0.5-5.0 | 2.0 | How much waves bulge out (mm) |
| `--wave-spacing` | 2.0-8.0 | 4.0 | Distance between wave peaks (mm) |
| `--layer-alternation` | 1-10 | 2 | Layers before phase flip (creates gaps) |
| `--phase-offset` | 0-100 | 50 | % shift between layers (50=diamonds) |
| `--layer-height` | 0.3-0.8 | 0.5 | Layer thickness (mm) |
| `--nozzle` | 0.4-2.0 | 1.0 | Nozzle diameter (mm) |

### Base Settings

| Parameter | Options | Effect |
|-----------|---------|--------|
| `--base-height` | 5-50 | Height where base transition applies (mm) |
| `--base-mode` | fewer_gaps / tighter_waves / solid_then_mesh | Base pattern |
| `--base-transition` | linear / exponential / step | How smoothly base transitions |

### Wave Patterns

| Pattern | Effect |
|---------|--------|
| sine (default) | Smooth, round bulges |
| triangular | More pronounced peaks |
| sawtooth | Sharp, aggressive waves |

## Examples

### Gentle Mesh (less aggressive)
```bash
python3 -m project.core --input lamp.stl \
  --wave-amplitude 1.0 \
  --layer-alternation 3 \
  --phase-offset 40
```

### Dramatic Mesh (more mesh, bigger gaps)
```bash
python3 -m project.core --input lamp.stl \
  --wave-amplitude 3.0 \
  --layer-alternation 2 \
  --wave-pattern triangular
```

### Detailed Mesh (tighter waves)
```bash
python3 -m project.core --input lamp.stl \
  --wave-spacing 2.5 \
  --wave-smoothness 8
```

### Strong Base (solid bottom)
```bash
python3 -m project.core --input lamp.stl \
  --base-height 40 \
  --base-mode fewer_gaps
```

## Output Files

```
output/
├── lamp_mesh_2.0a_4.0s_2alt_20251202_143022.gcode  ← This is what you print
└── lamp_mesh_20251202_143022.log                     ← Statistics & config
```

## Typical Values

### For 90mm diameter lamp bases (like yours)
```json
{
  "wave_amplitude": 2.0,
  "wave_spacing": 4.0,
  "layer_alternation": 2,
  "phase_offset": 50,
  "base_height": 28
}
```
Creates nice diamond mesh pattern with solid 28mm base.

### For very thin/delicate models
```json
{
  "wave_amplitude": 1.0,
  "wave_spacing": 3.5,
  "layer_alternation": 3,
  "base_height": 20
}
```
Smaller waves for more delicate appearance.

### For large/robust models
```json
{
  "wave_amplitude": 2.5,
  "wave_spacing": 4.5,
  "layer_alternation": 1,
  "phase_offset": 75,
  "base_height": 35
}
```
Larger features, bigger gaps, stronger base.

## Troubleshooting

**"Model not suitable for vase mode"**
- OK to ignore - it still prints fine
- Adjust `--base-height` if base is too weak

**Waves look wrong in preview**
- Check `--wave-amplitude` (too small = invisible)
- Check `--layer-alternation` (too high = no alternation)
- Increase `--wave-smoothness` (max 10)

**Model exceeds build volume**
- Too tall: edit base_height or use different model
- Too wide: scale model in CAD

**Want to repeat a slice**
- Command is printed after each slice
- Copy and paste to re-run with same settings

## All Command-Line Flags

```
Printer:
  --nozzle FLOAT                 Nozzle diameter (mm)
  --nozzle-temp INT              Nozzle temp (°C)
  --bed-temp INT                 Bed temp (°C)

Print:
  --layer-height FLOAT           Layer height (mm)
  --print-speed FLOAT            Print speed (mm/s)
  --travel-speed FLOAT           Travel speed (mm/s)

Mesh:
  --wave-amplitude FLOAT         Wave peak distance (mm)
  --wave-spacing FLOAT           Wave wavelength (mm)
  --wave-smoothness INT          Smoothness 1-10
  --wave-pattern {sine,triangular,sawtooth}
  --layer-alternation INT        Layers per alternation
  --phase-offset FLOAT           Phase % offset (0-100)

Base:
  --base-height FLOAT            Base transition height (mm)
  --base-mode {tighter_waves,fewer_gaps,solid_then_mesh}
  --base-transition {linear,exponential,step}

File:
  --input FILE                   Input STL file
  --output FILE                  Output GCode file
```

## Files

- **config.json** - Edit defaults here (apply to all future slices)
- **USAGE.md** - Full documentation
- **SETUP_COMPLETE.md** - Implementation details

## Performance

- Slicing time: 1-2 seconds for 300-layer model
- GCode size: ~3-5 MB
- Print time: ~45 minutes for typical vase

## Notes

- Always use `--input` or interactive mode
- GCode auto-opens in OrcaSlicer (if installed)
- Settings print after each slice (easy to reproduce)
- Logs save automatically with all config used
- No external dependencies needed

---

**Start slicing:** `python3 -m project.core`
