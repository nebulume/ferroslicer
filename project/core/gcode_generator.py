'''
GCode generation for Klipper-flavored output.
'''

import math
from typing import List, Tuple
from datetime import datetime
from .stl_parser import Vector3
from .geometry_analyzer import Layer
from .wave_generator import WavePoint
from .logger import setup_logger

logger = setup_logger("gcode_generator")


class GCodeGenerator:
    """Generates Klipper-flavored GCode output."""

    def __init__(
        self,
        nozzle_diameter: float = 1.0,
        layer_height: float = 0.5,
        nozzle_temp: float = 260,
        bed_temp: float = 65,
        print_speed: float = 35,
        travel_speed: float = 40,
        fan_speed: float = 25,
        filament_diameter: float = 1.75,
        purge_gap: float = 20.0,
        purge_length: float = 50.0,
        purge_side: str = "left",
        max_volumetric_speed: float = 12.0,
        skirt_enabled: bool = True,
        skirt_distance: float = 0.0,
        skirt_height: int = 1,
        start_gcode_override: str = "",
        end_gcode_override: str = "",
        # ── Printer geometry ──────────────────────────────────────────────────
        bed_x: float = 220.0,
        bed_y: float = 220.0,
        max_z: float = 280.0,
        origin: str = "front_left",     # "front_left" | "center"
        kinematics: str = "cartesian",  # "cartesian" | "corexy" | "delta"
        # ── Motion ───────────────────────────────────────────────────────────
        print_accel: int = 500,
        travel_accel: int = 1500,
        z_hop: float = 0.0,
        # ── First layer ───────────────────────────────────────────────────────
        first_layer_speed_pct: int = 50,
    ):
        self.nozzle_diameter = nozzle_diameter
        self.layer_height = layer_height
        self.nozzle_temp = nozzle_temp
        self.bed_temp = bed_temp
        self.print_speed = print_speed
        self.travel_speed = travel_speed
        self.fan_speed = fan_speed
        self.filament_diameter = filament_diameter
        self.purge_gap = purge_gap
        self.purge_length = purge_length
        self.purge_side = purge_side
        self.max_volumetric_speed = max_volumetric_speed
        self.skirt_enabled = skirt_enabled
        self.skirt_distance = skirt_distance
        self.skirt_height = skirt_height
        self.start_gcode_override = start_gcode_override
        self.end_gcode_override = end_gcode_override
        self.bed_x = bed_x
        self.bed_y = bed_y
        self.max_z = max_z
        self.origin = origin
        self.kinematics = kinematics
        self.print_accel = print_accel
        self.travel_accel = travel_accel
        self.z_hop = z_hop
        self.first_layer_speed_pct = first_layer_speed_pct

        # Calculated values
        self.extrusion_width = nozzle_diameter * 1.2
        self.filament_radius = filament_diameter / 2.0
        self.filament_cross_section = math.pi * self.filament_radius ** 2

        self.gcode_lines: List[str] = []
        self.current_position = Vector3(0, 0, 0)
        self.current_extrusion = 0.0
        self._at_z_hop = False   # track whether we've already lifted for z-hop

        # Build-plate center depends on origin convention
        if origin == "center":
            self.build_plate_center = Vector3(0, 0, 0)
        else:  # front_left (default for Cartesian/CoreXY)
            self.build_plate_center = Vector3(bed_x / 2.0, bed_y / 2.0, 0)

    def generate_gcode(
        self,
        wave_points_by_layer: List[List[WavePoint]],
        model_name: str,
        model_bounds: Tuple[Vector3, Vector3] = None,
        base_layer_points: List[Vector3] = None,
        spiral_points: list = None
    ) -> str:
        """
        Generate complete GCode from wave-modified perimeters.

        Args:
            wave_points_by_layer: List of layers, each containing WavePoint list
            model_name: Name of model being sliced
            model_bounds: Tuple of (min_point, max_point) for centering

        Returns:
            Complete GCode as string
        """
        self.gcode_lines = []
        self.current_position = Vector3(0, 0, 0)
        self.current_extrusion = 0.0

        # Calculate offset to center model on build plate
        offset = self._calculate_centering_offset(wave_points_by_layer, model_bounds)

        # Add headers
        self._add_header(model_name)

        # Add start GCode
        self._add_start_gcode()

        # New purge sequence after START_PRINT
        self._add_new_purge_sequence(wave_points_by_layer, offset, base_layer_points, spiral_points)

        # Add skirt if enabled (before main print starts)
        if self.skirt_enabled and spiral_points:
            self._add_skirt(spiral_points, offset)

        # If spiral points provided, generate continuous spiral GCode
        if spiral_points:
            self.gcode_lines.append("; --- UNRETRACT BEFORE PRINT (SPIRAL) ---")
            self.gcode_lines.append("G11")
            self.gcode_lines.append("")

            # Fast path for RustSpiralPoints: work on raw flat arrays to avoid
            # allocating 600k Python objects in the inner loop.
            from .spiral_generator import RustSpiralPoints as _RSP
            if isinstance(spiral_points, _RSP):
                xs = spiral_points._xs
                ys = spiral_points._ys
                zs = spiral_points._zs
                ox = offset.x
                oy = offset.y
                oz = offset.z
                lh = self.layer_height
                ew = self.extrusion_width
                fcs = self.filament_cross_section
                spd = self.print_speed
                spd_f = int(spd * 60)
                tv_f = int(self.travel_speed * 60)

                # Travel to first point
                first_target = Vector3(xs[0] + ox, ys[0] + oy, zs[0] + oz)
                self._add_move(first_target, extrusion_amount=0.0, is_travel=True)

                # Pre-compute extrusion factor (constant per segment)
                e_factor = (lh * ew) / fcs

                cx = float(self.current_position.x)
                cy = float(self.current_position.y)
                cz = float(self.current_position.z)
                total_e = self.current_extrusion

                n = len(xs)
                lines = self.gcode_lines
                for i in range(1, n):
                    tx = xs[i] + ox
                    ty = ys[i] + oy
                    tz = zs[i] + oz
                    seg_dx = tx - cx
                    seg_dy = ty - cy
                    seg_dz = tz - cz
                    seg_len = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy + seg_dz * seg_dz)
                    if seg_len > 0.001:
                        extrusion = seg_len * e_factor
                        total_e += extrusion
                        lines.append(
                            f"G1 X{tx:.3f} Y{ty:.3f} Z{tz:.3f} E{extrusion:.5f} F{spd_f}"
                        )
                    cx, cy, cz = tx, ty, tz

                self.current_position = Vector3(cx, cy, cz)
                self.current_extrusion = total_e

            else:
                # Legacy path for regular SpiralPoint lists
                first_pos = spiral_points[0].position
                first_target = Vector3(first_pos.x + offset.x, first_pos.y + offset.y, first_pos.z + offset.z)
                self._add_move(first_target, extrusion_amount=0.0, is_travel=True)

                for idx in range(len(spiral_points) - 1):
                    p_next = spiral_points[idx + 1].position
                    target = Vector3(p_next.x + offset.x, p_next.y + offset.y, p_next.z + offset.z)
                    seg_dx = target.x - self.current_position.x
                    seg_dy = target.y - self.current_position.y
                    seg_dz = target.z - self.current_position.z
                    seg_len = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy + seg_dz * seg_dz)
                    extrusion = self._calculate_extrusion(seg_len)
                    self._add_move(target, extrusion_amount=extrusion)

        else:
            # Calculate total path length for extrusion
            total_path_length = self._calculate_total_path_length(wave_points_by_layer)

            # Unretracting before print starts
            self.gcode_lines.append("; --- UNRETRACT BEFORE PRINT ---")
            self.gcode_lines.append("G11")
            self.gcode_lines.append("")

            # Process each layer with offset
            for layer_idx, wave_points in enumerate(wave_points_by_layer):
                self._process_layer(layer_idx, wave_points, offset)

        # Add end GCode
        self._add_end_gcode()

        return "\n".join(self.gcode_lines)

    def _add_header(self, model_name: str) -> None:
        """Add file header comments."""
        self.gcode_lines.append("; MeshVase Slicer - Klipper GCode Output")
        self.gcode_lines.append(f"; Model: {model_name}")
        self.gcode_lines.append(f"; Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.gcode_lines.append(f"; Nozzle: {self.nozzle_diameter}mm, Layer: {self.layer_height}mm")
        self.gcode_lines.append(f"; Nozzle temp: {self.nozzle_temp}°C, Bed: {self.bed_temp}°C")
        self.gcode_lines.append("")

    def _add_start_gcode(self) -> None:
        """Add start GCode — uses custom override if provided."""
        self.gcode_lines.append("; --- START GCODE ---")
        if self.start_gcode_override.strip():
            # User-provided start gcode (supports {bed_temp} / {nozzle_temp} placeholders)
            pwm_value = max(0, min(255, int((self.fan_speed / 100.0) * 255)))
            rendered = (
                self.start_gcode_override
                .replace("{bed_temp}", str(int(self.bed_temp)))
                .replace("{nozzle_temp}", str(int(self.nozzle_temp)))
                .replace("{fan_speed}", str(pwm_value))
            )
            for line in rendered.splitlines():
                self.gcode_lines.append(line)
        else:
            # Default Klipper macro start sequence
            self.gcode_lines.append(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE={self.bed_temp}")
            self.gcode_lines.append(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE={self.nozzle_temp}")
            pwm_value = max(0, min(255, int((self.fan_speed / 100.0) * 255)))
            self.gcode_lines.append(f"M106 S{pwm_value}")
            self.gcode_lines.append("START_PRINT")
        self.gcode_lines.append("")
        self.gcode_lines.append("; Absolute positioning for X/Y/Z, relative for E")
        self.gcode_lines.append("G90")
        self.gcode_lines.append("M83")
        self.gcode_lines.append("")
        if self.print_accel > 0 or self.travel_accel > 0:
            self.gcode_lines.append(f"; Acceleration: print={self.print_accel} mm/s², travel={self.travel_accel} mm/s²")
            self.gcode_lines.append(f"M204 P{self.print_accel} T{self.travel_accel}")
            self.gcode_lines.append("")

    def _add_new_purge_sequence(
        self,
        wave_points_by_layer: List[List[WavePoint]],
        offset: Vector3,
        base_layer_points: List[Vector3] = None,
        spiral_points: list = None
    ) -> None:
        """
        New purge sequence after START_PRINT:
        1. Move to (219,219) safe corner
        2. Retract to prevent oozing
        3. Move to purge line start (20mm from print start)
        4. Purge: first 20mm extruding, next 20mm travel-only (oozing)
        5. Retract again before moving to print
        
        Total purge time target: <= 8 seconds
        """
        self.gcode_lines.append("; --- NEW PURGE SEQUENCE ---")
        self.gcode_lines.append("; After START_PRINT: retract, move to purge, extrude+ooze, retract")
        
        # Step 1: Move to safe corner at Z=2.0 (slightly inside bed limits)
        sc_x = self.build_plate_center.x + self.bed_x * 0.45 if self.origin != "center" else self.bed_x * 0.45
        sc_y = self.build_plate_center.y + self.bed_y * 0.45 if self.origin != "center" else self.bed_y * 0.45
        safe_corner = Vector3(sc_x, sc_y, 2.0)
        self.gcode_lines.append("; Move to safe corner after START_PRINT")
        self._add_move(safe_corner, is_travel=True)
        
        # Step 2: Retract to prevent oozing during travel to purge
        self.gcode_lines.append("; Retract to prevent oozing")
        self.gcode_lines.append("G10")
        self.gcode_lines.append("")
        
        # Step 3: Calculate purge line position (20mm OUTSIDE the print on the RIGHT side)
        # Find the center of the first layer/revolution to place purge outside
        if spiral_points:
            # Get first revolution points to find centroid
            # Fast path: RustSpiralPoints exposes raw arrays
            from .spiral_generator import RustSpiralPoints as _RSP
            if isinstance(spiral_points, _RSP):
                revs = spiral_points._revs
                cut = next((i for i, r in enumerate(revs) if r >= 1.0), len(revs))
                if cut > 0:
                    xs_r = spiral_points._xs[:cut]
                    ys_r = spiral_points._ys[:cut]
                    cx = sum(xs_r) / cut + offset.x
                    cy = sum(ys_r) / cut + offset.y
                    max_x = max(xs_r) + offset.x
                else:
                    cx = spiral_points._xs[0] + offset.x
                    cy = spiral_points._ys[0] + offset.y
                    max_x = cx
            else:
                first_rev = [p for p in spiral_points if p.revolution < 1.0]
                if first_rev:
                    cx = sum(p.position.x for p in first_rev) / len(first_rev) + offset.x
                    cy = sum(p.position.y for p in first_rev) / len(first_rev) + offset.y
                    max_x = max(p.position.x + offset.x for p in first_rev)
                else:
                    first_print_pos = spiral_points[0].position
                    cx = first_print_pos.x + offset.x
                    cy = first_print_pos.y + offset.y
                    max_x = cx
        else:
            # Fallback to first layer point
            first_layer = wave_points_by_layer[0]
            cx = sum(p.modified.x for p in first_layer) / len(first_layer) + offset.x
            cy = sum(p.modified.y for p in first_layer) / len(first_layer) + offset.y
            max_x = max(p.modified.x + offset.x for p in first_layer)
        
        # Purge line: 40mm long, positioned 20mm OUTSIDE (to the right of) the rightmost perimeter point
        purge_distance = 20.0
        purge_length = 40.0
        purge_x = max_x + purge_distance  # 20mm to the right of the rightmost perimeter point
        purge_start = Vector3(purge_x, cy - purge_length/2, 0.5)
        purge_mid = Vector3(purge_x, cy, 0.5)
        purge_end = Vector3(purge_x, cy + purge_length/2, 0.5)
        
        # Move to purge start
        self.gcode_lines.append(f"; Move to purge line start ({purge_distance}mm from print)")
        self._add_move(purge_start, is_travel=True)
        
        # Step 4: Purge sequence
        # First 20mm: extruding at 40mm/s (~0.5 seconds)
        # Calculate extrusion for 20mm
        purge_speed = 40.0  # mm/s (consistent purge speed)
        first_segment_length = 20.0
        extrusion_first = self._calculate_extrusion(first_segment_length)
        
        self.gcode_lines.append("; Unretract and purge first 20mm (extruding)")
        self.gcode_lines.append("G11")  # Unretract
        self.gcode_lines.append(f"G1 X{purge_mid.x:.3f} Y{purge_mid.y:.3f} Z{purge_mid.z:.3f} E{extrusion_first:.5f} F{purge_speed * 60:.0f}")
        self.current_position = purge_mid
        self.current_extrusion += extrusion_first
        
        # Next 20mm: travel only (pressure oozing, no extrusion command)
        ooze_speed = self.travel_speed  # Use travel speed for ooze segment (~40mm/s)
        self.gcode_lines.append("; Next 20mm: travel only (oozing from pressure)")
        self.gcode_lines.append(f"G1 X{purge_end.x:.3f} Y{purge_end.y:.3f} Z{purge_end.z:.3f} F{ooze_speed * 60:.0f}  ; No E command - let pressure ooze")
        self.current_position = purge_end
        
        # Step 5: Retract after purge to prevent stringing to print start
        self.gcode_lines.append("; Retract after purge to prevent stringing")
        self.gcode_lines.append("G10")
        self.gcode_lines.append("")

    def _add_skirt(self, spiral_points: list, offset: Vector3) -> None:
        """
        Generate skirt loop - a single circular base loop for bed adhesion.
        Skirt is printed FIRST at Z=layer_height as a complete circle.
        Then spiral starts and rises from the center.
        
        Skirt radius = spiral perimeter radius + nozzle_diameter/2 + skirt_distance
        This ensures the skirt and spiral touch side-by-side (parallel circles).
        
        Args:
            spiral_points: All spiral points
            offset: Centering offset
        """
        if not self.skirt_enabled:
            return
        
        self.gcode_lines.append("; --- SKIRT FOR ADHESION (BASE LOOP) ---")
        self.gcode_lines.append(f"; Single circular loop parallel to spiral start, {self.skirt_height} layer(s) tall")
        
        # Find first revolution to determine the spiral perimeter
        from .spiral_generator import RustSpiralPoints as _RSP
        if isinstance(spiral_points, _RSP):
            revs = spiral_points._revs
            cut = next((i for i, r in enumerate(revs) if r >= 1.0), len(revs))
            if cut == 0:
                logger.warning("No first revolution points found for skirt, skipping")
                return
            frxs = spiral_points._xs[:cut]
            frys = spiral_points._ys[:cut]
            cx = sum(frxs) / cut + offset.x
            cy = sum(frys) / cut + offset.y
            spiral_radius = sum(
                math.sqrt((frxs[i] + offset.x - cx)**2 + (frys[i] + offset.y - cy)**2)
                for i in range(cut)
            ) / cut
        else:
            first_rev_points = [p for p in spiral_points if p.revolution < 1.0]
            if not first_rev_points:
                logger.warning("No first revolution points found for skirt, skipping")
                return
            cx = sum(p.position.x for p in first_rev_points) / len(first_rev_points) + offset.x
            cy = sum(p.position.y for p in first_rev_points) / len(first_rev_points) + offset.y
            spiral_radius = sum(
                math.sqrt((p.position.x + offset.x - cx)**2 + (p.position.y + offset.y - cy)**2)
                for p in first_rev_points
            ) / len(first_rev_points)
        
        # Skirt radius = spiral radius + nozzle_width/2 + skirt_distance
        # nozzle_width = nozzle_diameter (assumes line width = nozzle diameter for 0.5mm layer)
        nozzle_width = self.nozzle_diameter
        skirt_radius = spiral_radius + (nozzle_width / 2.0) + self.skirt_distance
        
        # Generate skirt as a high-resolution circular loop
        num_points = 360  # One point per degree for smooth circle
        skirt_points = []
        
        for i in range(num_points):
            angle = (i / num_points) * 2 * math.pi
            skirt_x = cx + skirt_radius * math.cos(angle)
            skirt_y = cy + skirt_radius * math.sin(angle)
            skirt_z = self.layer_height  # Skirt at base layer height only
            skirt_points.append(Vector3(skirt_x, skirt_y, skirt_z))
        
        # Close the loop
        if skirt_points:
            skirt_points.append(skirt_points[0])
        
        # Print skirt loop
        if skirt_points:
            self.gcode_lines.append("; Move to skirt start")
            self._add_move(skirt_points[0], is_travel=True)
            
            self.gcode_lines.append("; Unretract before skirt")
            self.gcode_lines.append("G11")
            
            # Print skirt loop with extrusion
            for i in range(1, len(skirt_points)):
                prev_point = skirt_points[i-1]
                curr_point = skirt_points[i]
                seg_len = math.sqrt(
                    (curr_point.x - prev_point.x)**2 + 
                    (curr_point.y - prev_point.y)**2 + 
                    (curr_point.z - prev_point.z)**2
                )
                extrusion = self._calculate_extrusion(seg_len)
                self._add_move(curr_point, extrusion_amount=extrusion)
            
            self.gcode_lines.append("; Retract after skirt")
            self.gcode_lines.append("G10")
            self.gcode_lines.append("")

    def _add_purge_line(
        self,
        wave_points_by_layer: List[List[WavePoint]],
        offset: Vector3,
        base_layer_points: List[Vector3] = None
    ) -> None:
        """DEPRECATED: Old purge line method. Use _add_new_purge_sequence instead."""
        # Show configured purge length/gap in the comment
        gap = getattr(self, 'purge_gap', 20.0)
        length = getattr(self, 'purge_length', 50.0)
        self.gcode_lines.append("; --- PURGE LINE ---")
        self.gcode_lines.append(f"; {length:.0f}mm purge line ({gap:.0f}mm away from print base) to ensure good extrusion start")

        # Use configured purge parameters
        gap = getattr(self, 'purge_gap', 20.0)
        length = getattr(self, 'purge_length', 50.0)
        side = getattr(self, 'purge_side', 'left').lower()

        # Decide orientation and position based on side
        if base_layer_points:
            base_min_x = min(p.x for p in base_layer_points) + offset.x
            base_max_x = max(p.x for p in base_layer_points) + offset.x
            base_min_y = min(p.y for p in base_layer_points) + offset.y
            base_max_y = max(p.y for p in base_layer_points) + offset.y
        else:
            # Fallback to wave-modified extents
            base_min_x = float('inf')
            base_max_x = float('-inf')
            base_min_y = float('inf')
            base_max_y = float('-inf')
            for layer_points in wave_points_by_layer:
                for point in layer_points:
                    xw = point.modified.x + offset.x
                    yw = point.modified.y + offset.y
                    base_min_x = min(base_min_x, xw)
                    base_max_x = max(base_max_x, xw)
                    base_min_y = min(base_min_y, yw)
                    base_max_y = max(base_max_y, yw)

        cx = self.build_plate_center.x
        cy = self.build_plate_center.y

        if side == 'left':
            purge_x = base_min_x - gap
            y_start = cy - (length / 2.0)
            y_end = cy + (length / 2.0)
            purge_start = Vector3(purge_x, max(y_start, 0.0), 0.5)
            purge_end = Vector3(purge_x, min(y_end, 220.0), 0.5)
        elif side == 'right':
            purge_x = base_max_x + gap
            y_start = cy - (length / 2.0)
            y_end = cy + (length / 2.0)
            purge_start = Vector3(purge_x, max(y_start, 0.0), 0.5)
            purge_end = Vector3(purge_x, min(y_end, 220.0), 0.5)
        elif side == 'front':
            purge_y = base_max_y + gap
            x_start = cx - (length / 2.0)
            x_end = cx + (length / 2.0)
            purge_start = Vector3(max(x_start, 0.0), purge_y, 0.5)
            purge_end = Vector3(min(x_end, 220.0), purge_y, 0.5)
        elif side == 'back':
            purge_y = base_min_y - gap
            x_start = cx - (length / 2.0)
            x_end = cx + (length / 2.0)
            purge_start = Vector3(max(x_start, 0.0), purge_y, 0.5)
            purge_end = Vector3(min(x_end, 220.0), purge_y, 0.5)
        else:
            # default to left behavior
            purge_x = base_min_x - gap
            y_start = cy - (length / 2.0)
            y_end = cy + (length / 2.0)
            purge_start = Vector3(purge_x, max(y_start, 0.0), 0.5)
            purge_end = Vector3(purge_x, min(y_end, 220.0), 0.5)

        # Start from back-right corner of build plate (safe position)
        safe_start = Vector3(219.0, 219.0, 2.0)
        self._add_move(safe_start, is_travel=True)

        # Move to start of purge (travel, no extrusion)
        self._add_move(purge_start, is_travel=True)

        # Extrude purge line (length configured)
        extrusion = self._calculate_extrusion(length)
        self._add_move(purge_end, extrusion_amount=extrusion)

        # Retract after purge line to stop extrusion before print starts
        self.gcode_lines.append("; Retract to stop extrusion")
        self.gcode_lines.append("G10")

        self.gcode_lines.append("")

    def _process_layer(self, layer_idx: int, wave_points: List[WavePoint], offset: Vector3 = None) -> None:
        """
        Process single layer of wave points.

        Args:
            layer_idx: Layer index
            wave_points: WavePoint objects for this layer
            offset: Position offset to center on build plate
        """
        if not wave_points:
            return

        if offset is None:
            offset = Vector3(0, 0, 0)

        # Get Z height from first point
        z = wave_points[0].modified.z

        # Add layer comment
        num_waves = len(wave_points)
        diameter = math.sqrt(
            (max(p.modified.x for p in wave_points) - min(p.modified.x for p in wave_points)) ** 2 +
            (max(p.modified.y for p in wave_points) - min(p.modified.y for p in wave_points)) ** 2
        )

        self.gcode_lines.append(
            f"; Layer {layer_idx}, Z={z:.3f}mm, Diameter={diameter:.1f}mm, "
            f"Points={num_waves}, "
            f"Amplitude factor={wave_points[0].amplitude_factor:.2f}"
        )

        # Process each point
        for point_idx, wave_point in enumerate(wave_points):
            # Apply offset to position
            target_position = Vector3(
                wave_point.modified.x + offset.x,
                wave_point.modified.y + offset.y,
                wave_point.modified.z + offset.z
            )

            # Calculate path segment length
            segment_vector = target_position - self.current_position
            segment_length = segment_vector.magnitude()

            if segment_length > 0.001:
                extrusion = self._calculate_extrusion(segment_length)
                # Slow down first layer for adhesion
                if layer_idx == 0:
                    orig = self.print_speed
                    self.print_speed = orig * self.first_layer_speed_pct / 100.0
                    self._add_move(target_position, extrusion_amount=extrusion)
                    self.print_speed = orig
                else:
                    self._add_move(target_position, extrusion_amount=extrusion)

        self.gcode_lines.append("")

    def _add_move(
        self,
        target: Vector3,
        extrusion_amount: float = 0.0,
        is_travel: bool = False
    ) -> None:
        """
        Add G1 movement command.

        Args:
            target: Target position
            extrusion_amount: Extrusion amount in mm (0 = no extrusion)
            is_travel: If True, use travel speed
        """
        # Calculate speed respecting volumetric limit
        if is_travel or extrusion_amount == 0:
            speed = self.travel_speed
        else:
            speed = self.print_speed
            actual_speed = self._limit_speed_to_volumetric(speed, extrusion_amount)
            speed = min(speed, actual_speed)

        # Z-hop: lift before travel, lower before extrusion
        if is_travel and self.z_hop > 0 and not self._at_z_hop:
            hop_z = self.current_position.z + self.z_hop
            self.gcode_lines.append(f"G1 Z{hop_z:.3f} F{self.travel_speed * 60:.0f}")
            self._at_z_hop = True
        elif not is_travel and self._at_z_hop:
            self.gcode_lines.append(f"G1 Z{target.z:.3f} F{self.travel_speed * 60:.0f}")
            self._at_z_hop = False

        self.current_extrusion += extrusion_amount

        gcode = f"G1 X{target.x:.3f} Y{target.y:.3f} Z{target.z:.3f}"

        if extrusion_amount > 0:
            gcode += f" E{extrusion_amount:.5f}"

        gcode += f" F{speed * 60:.0f}"

        self.gcode_lines.append(gcode)
        self.current_position = target

    def _add_end_gcode(self) -> None:
        """Add end GCode — uses custom override if provided."""
        self.gcode_lines.append("")
        self.gcode_lines.append("; --- END GCODE ---")
        if self.end_gcode_override.strip():
            for line in self.end_gcode_override.splitlines():
                self.gcode_lines.append(line)
        else:
            self.gcode_lines.append("; Retract filament to prevent oozing")
            self.gcode_lines.append("G10")
            z_raise = 10.0
            new_z = self.current_position.z + z_raise
            self.gcode_lines.append(f"; Raise Z by {z_raise}mm to clear print")
            self.gcode_lines.append(f"G1 Z{new_z:.3f} F{self.travel_speed * 60:.0f}")
            self.current_position.z = new_z
            self.gcode_lines.append("; Move to safe corner")
            ec_x = self.build_plate_center.x + self.bed_x * 0.45 if self.origin != "center" else self.bed_x * 0.45
            ec_y = self.build_plate_center.y + self.bed_y * 0.45 if self.origin != "center" else self.bed_y * 0.45
            self.gcode_lines.append(f"G1 X{ec_x:.3f} Y{ec_y:.3f} F{self.travel_speed * 60:.0f}")
            self.current_position.x = ec_x
            self.current_position.y = ec_y
            self.gcode_lines.append("")
            self.gcode_lines.append("END_PRINT")

    def _calculate_centering_offset(
        self,
        wave_points_by_layer: List[List[WavePoint]],
        model_bounds: Tuple[Vector3, Vector3] = None
    ) -> Vector3:
        """
        Calculate offset to center model on 220x220 build plate (center at 110, 110).

        Args:
            wave_points_by_layer: List of layers with wave points
            model_bounds: Tuple of (min_point, max_point) from STL parser

        Returns:
            Vector3 offset to apply to all coordinates
        """
        # Use model_bounds if provided (from STL parser)
        if model_bounds is not None:
            min_point, max_point = model_bounds
            model_center_x = (min_point.x + max_point.x) / 2.0
            model_center_y = (min_point.y + max_point.y) / 2.0
        else:
            # Fallback: calculate from wave points
            min_x = float('inf')
            max_x = float('-inf')
            min_y = float('inf')
            max_y = float('-inf')

            for layer_points in wave_points_by_layer:
                for point in layer_points:
                    min_x = min(min_x, point.modified.x)
                    max_x = max(max_x, point.modified.x)
                    min_y = min(min_y, point.modified.y)
                    max_y = max(max_y, point.modified.y)

            model_center_x = (min_x + max_x) / 2.0 if min_x != float('inf') else 0.0
            model_center_y = (min_y + max_y) / 2.0 if min_y != float('inf') else 0.0

        plate_center_x = self.build_plate_center.x
        plate_center_y = self.build_plate_center.y

        offset_x = plate_center_x - model_center_x
        offset_y = plate_center_y - model_center_y

        return Vector3(offset_x, offset_y, 0.0)

    def _calculate_extrusion(self, path_length: float) -> float:
        """
        Calculate extrusion amount for path segment.

        Formula: E = (segment_length * layer_height * extrusion_width) / filament_cross_section
        """
        if path_length <= 0:
            return 0.0

        extrusion = (path_length * self.layer_height * self.extrusion_width) / self.filament_cross_section

        return extrusion

    def _calculate_total_path_length(self, wave_points_by_layer: List[List[WavePoint]]) -> float:
        """Calculate total path length for estimation."""
        total = 0.0

        for layer_points in wave_points_by_layer:
            for i in range(len(layer_points)):
                p1 = layer_points[i].modified
                p2 = layer_points[(i + 1) % len(layer_points)].modified

                dx = p2.x - p1.x
                dy = p2.y - p1.y
                total += math.sqrt(dx**2 + dy**2)
        return total

    def _limit_speed_to_volumetric(self, speed_mms: float, extrusion_mm: float) -> float:
        """
        Calculate maximum speed constrained by volumetric limit.

        Args:
            speed_mms: Requested speed in mm/s
            extrusion_mm: Extrusion distance in mm (relative E)

        Returns:
            Maximum safe speed in mm/s that respects volumetric limit
        """
        if extrusion_mm <= 0 or self.max_volumetric_speed <= 0:
            return speed_mms

        # Nozzle cross-sectional area: width × height
        nozzle_area = self.nozzle_diameter * self.layer_height

        if nozzle_area <= 0:
            return speed_mms

        # Max extrusion speed (mm of filament per second) from volumetric limit
        max_extrusion_speed = self.max_volumetric_speed / nozzle_area

        # Limit speed based on extrusion amount per mm of travel
        # Assuming extrusion_mm is relative to travel distance
        max_speed = self.max_volumetric_speed / (self.nozzle_diameter * extrusion_mm) if extrusion_mm > 0 else speed_mms

        return max(1.0, min(speed_mms, max_speed))  # Ensure 1-100 mm/s range
        

    def estimate_print_time(self, total_path_length: float) -> float:
        """
        Estimate print time in hours.

        Args:
            total_path_length: Total path length in mm

        Returns:
            Estimated time in hours
        """
        # Average speed considering travel and extrusion moves
        avg_speed = (self.print_speed + self.travel_speed) / 2

        # Total time in minutes
        total_minutes = total_path_length / avg_speed / 60.0

        # Convert to hours
        return total_minutes / 60.0
