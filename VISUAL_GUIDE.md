# MeshVase Slicer - Visual Guide

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshVase Slicer Workflow                      │
└─────────────────────────────────────────────────────────────────┘

Start
  │
  ├─→ [Interactive CLI] ──→ Select STL File ──→ Input Parameters ──┐
  │                                                                  │
  └─→ [Command-Line] ──────────────────────────────────────────────┤
                                                                    ↓
                         Load Config (config.json)
                                   │
                                   ↓
                         Parse STL File (ASCII)
                                   │
                         ┌─────────┴─────────┐
                         │                   │
                    [Valid?]            [Invalid]
                         │                   │
                        Yes                  No ──→ Error + Exit
                         │
                         ↓
                   Validate Model
                         │
                 ┌───────┴───────┐
                 │               │
            [Warnings]       [OK] │
                 │               │
               Show              │
               Ask?              │
                 │               │
            Continue  ────────→  │
                                 ↓
                         Slice into Layers
                                 │
                                 ↓
                         Extract Perimeters
                                 │
                                 ↓
                         Analyze Curvature
                                 │
                                 ↓
                    Generate Wave Patterns
                    ┌────────────────────────┐
                    │ For Each Layer:        │
                    │ • Apply amplitude      │
                    │ • Phase offset         │
                    │ • Base transition      │
                    │ • Curvature reduction  │
                    │ • Diameter scaling     │
                    └────────────────────────┘
                                 │
                                 ↓
                         Generate GCode
                    ┌────────────────────────┐
                    │ • START_PRINT macro    │
                    │ • Purge line           │
                    │ • G1 movements         │
                    │ • E extrusion values   │
                    │ • Layer comments       │
                    │ • END_PRINT macro      │
                    └────────────────────────┘
                                 │
                                 ↓
                          Save Files
                    ┌────────────────────────┐
                    │ • output/*.gcode       │
                    │ • output/*.log         │
                    └────────────────────────┘
                                 │
                                 ↓
                      Launch OrcaSlicer
                                 │
                                 ↓
                         Print Command
                                 │
                                 ↓
                               DONE!
```

## Parameter Effects Visualization

### Wave Amplitude

```
Amplitude = 1.0mm        Amplitude = 2.0mm       Amplitude = 3.0mm

    ∿∿∿∿∿∿                 ∿∿∿∿∿∿                  ∿∿∿∿∿∿
   ╱    ╲                 ╱      ╲                ╱        ╲
  │      │               │        │              │          │
  └──────┘               └────────┘              └──────────┘

Gentle waves          Normal waves            Dramatic waves
  └─ Smooth            └─ Default            └─ Bold, visible
```

### Wave Spacing

```
Spacing = 2.0mm       Spacing = 4.0mm        Spacing = 6.0mm

   ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿         ∿∿∿∿∿∿∿∿              ∿∿∿∿∿∿
  ╱  ╲ ╱  ╲ ╱  ╲         ╱      ╲ ╱              ╱      ╲
  └──┘ └──┘ └──┘         └──────┘ └              └──────┘

Many small waves   Normal waves       Fewer large waves
  └─ Dense           └─ Default       └─ Sparse
```

### Layer Alternation & Phase Offset

```
No Alternation:              Layer Alternation = 2:        Phase Offset = 50%:
(solid, no gaps)

Layer 1: ∿∿∿∿∿∿∿            Layer 1: ∿∿∿∿∿∿∿             Layer 1: ∿∿∿∿∿∿∿
         ╱  ╲ ╱  ╲                   ╱  ╲ ╱  ╲                    ╱  ╲ ╱  ╲
Layer 2: ∿∿∿∿∿∿∿            Layer 2: ∿∿∿∿∿∿∿             Layer 2:  ∿∿∿∿∿∿
         ╱  ╲ ╱  ╲                   ╱  ╲ ╱  ╲  (offset)         ╱  ╲ ╱  ╲
Layer 3: ∿∿∿∿∿∿∿            Layer 3: ∿∿∿∿∿∿∿             Layer 3: ∿∿∿∿∿∿∿
         ╱  ╲ ╱  ╲                   ╱  ╲ ╱  ╲                    ╱  ╲ ╱  ╲
Layer 4: ∿∿∿∿∿∿∿            Layer 4: ∿∿∿∿∿∿∿             Layer 4:  ∿∿∿∿∿∿
         ╱  ╲ ╱  ╲                   ╱  ╲ ╱  ╲  (offset)         ╱  ╲ ╱  ╲

Solid wall           Diamond pattern         Diamond pattern
```

### Base Transition Modes

```
Fewer Gaps (Exponential)    Tighter Waves       Solid Then Mesh

Height
  │     ┌─ Full waves         ┌─ Tight waves      ┌─ Full mesh
  │    ╱ ─────────            │                    │
  │   │                        │ Tight freq        │ Transition
  │  ╱│                        │ Lower amp         │
  │ ╱ │                       ╱│                  ╱│
  │╱  └─ Ramping              │                  │ │
  │     (quadratic)           │ Ramping          │ ├─ Solid
  └─────────────────────      └─────────────────┴─└─ Base

Smooth gradient        More waves at base   Clean transition
  └─ Best visual        └─ Structural       └─ Practical
```

## File Structure Overview

```
meshy-gen3/
│
├── project/                          Python package
│   ├── __init__.py
│   ├── core/                         Core modules
│   │   ├── __init__.py
│   │   ├── __main__.py              ← ENTRY POINT (python3 -m project.core)
│   │   │
│   │   ├── config.py                Configuration management
│   │   ├── logger.py                Logging setup
│   │   ├── utils.py                 Utility functions
│   │   ├── exceptions.py            Custom exceptions
│   │   │
│   │   ├── stl_parser.py            📥 STL file reading
│   │   │   └─ STLParser.parse()
│   │   │   └─ STLModel.check_manifold()
│   │   │
│   │   ├── geometry_analyzer.py     📐 Geometry analysis
│   │   │   └─ GeometryAnalyzer.analyze_model()
│   │   │   └─ CurvatureAnalyzer.analyze_perimeter_curvature()
│   │   │
│   │   ├── wave_generator.py        🌊 Wave patterns
│   │   │   └─ WaveGenerator.generate_wave_points()
│   │   │   └─ LayerAlternationController.get_phase_for_layer()
│   │   │
│   │   ├── base_integrity.py        🏗️  Base transitions
│   │   │   └─ BaseIntegrityManager.get_amplitude_factor()
│   │   │
│   │   ├── adaptive_behavior.py     🎯 Adaptive waves
│   │   │   └─ CurvatureAdaptation.analyze_curvature_regions()
│   │   │   └─ DiameterScaling.calculate_wave_count()
│   │   │
│   │   ├── gcode_generator.py       🖨️  GCode output
│   │   │   └─ GCodeGenerator.generate_gcode()
│   │   │
│   │   ├── slicer.py                🎬 Orchestrator
│   │   │   └─ MeshVaseSlicer.slice_stl()
│   │   │
│   │   └── preview.py               👁️  OrcaSlicer integration
│   │       └─ PreviewSystem.launch_preview()
│   │
│   └── __pycache__/                 Python cache
│
├── config.json                      ⚙️  Configuration file
├── test_model.stl                   🧪 Test STL file
│
├── output/                          📁 Generated files
│   ├── *.gcode                      ← GCode files (ready to print)
│   └── *.log                        ← Generation logs
│
├── QUICK_START.md                   ⚡ Quick reference
├── USAGE.md                         📚 Full documentation
├── SETUP_COMPLETE.md                ℹ️  Implementation details
├── README.md                        📖 Project overview
├── requirements.txt                 📦 Dependencies (empty)
│
└── This tree structure!             🌳
```

## Data Flow

```
┌──────────┐
│ STL File │
└────┬─────┘
     │ STLParser.parse()
     ↓
┌──────────────┐
│ STLModel     │
│ - triangles  │
│ - bounds     │
│ - manifold✓  │
└────┬─────────┘
     │ GeometryAnalyzer.analyze_model()
     ↓
┌──────────────┐
│ Layers       │
│ - z height   │
│ - perimeter  │
│ - curvature  │
└────┬─────────┘
     │ WaveGenerator.generate_wave_points()
     ↓
┌──────────────┐
│ WavePoints   │
│ - original   │
│ - modified   │
│ - amplitude  │
└────┬─────────┘
     │ GCodeGenerator.generate_gcode()
     ↓
┌──────────────┐
│ GCode        │
│ - G1 commands│
│ - E extrusion│
│ - comments   │
└────┬─────────┘
     │
     ├─→ Save: output/*.gcode
     ├─→ Save: output/*.log
     └─→ Launch: OrcaSlicer
```

## Command Examples with Visual Results

### Example 1: Default Mesh
```bash
$ python3 -m project.core --input lamp.stl
```
```
Result (Layer cross-section):

Perimeter outline with:
├─ Wave amplitude: 2.0mm (extends 2mm outward)
├─ Wave spacing: 4.0mm (4 complete waves per circumference)
├─ Layer alternation: 2 (every 2 layers flip)
└─ Phase offset: 50% (creates diamonds)

Top view:    ∿∿∿∿∿∿∿    ← Layer 1
             ∿∿∿∿∿∿∿    ← Layer 2 (same phase)
              ∿∿∿∿∿     ← Layer 3 (50% offset = gaps!)
             ∿∿∿∿∿∿∿    ← Layer 4 (same as Layer 2)
              ∿∿∿∿∿     ← Layer 5 (same as Layer 3)
```

### Example 2: Dramatic Mesh
```bash
$ python3 -m project.core --input lamp.stl \
  --wave-amplitude 2.5 --wave-spacing 5.0
```
```
Result (Larger, more visible gaps):

     ∿∿∿∿∿∿∿∿∿∿∿∿
    ╱          ╲      ← 2.5mm amplitude
   │            │     ← 5.0mm spacing
    ╲          ╱
     ∿∿∿∿∿∿∿∿∿∿∿∿

More dramatic visual effect,
larger gaps in pattern
```

### Example 3: Tight Base
```bash
$ python3 -m project.core --input lamp.stl \
  --base-height 40 --wave-amplitude 2.0
```
```
Result (Stronger base):

Height
  │     ┌─ Full mesh wave (2.0mm)
  │    ╱│
  │   ╱ │
  │  ╱  │ Transition
  │ ╱   │ (exponential)
  │╱    │
  ├─────┴─ Full amplitude at 40mm
  │
  │ ∿∿∿  Ramping from 0 to 100%
  │ ╱╲   (quadratic curve)
  │╱  ╲
  │    └─ Solid base (no waves)
  └─ 0mm to 40mm height

More solid base for
structural strength
```

---

**Visual guide ready! Time to slice!** 🎨✂️
