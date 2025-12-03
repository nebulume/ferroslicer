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
        skirt_height: int = 1
    ):
        """
        Initialize GCode generator.

        Args:
            nozzle_diameter: Nozzle diameter in mm
            layer_height: Layer height in mm
            nozzle_temp: Nozzle temperature in °C
            bed_temp: Bed temperature in °C
            print_speed: Print speed in mm/s
            travel_speed: Travel speed in mm/s
            fan_speed: Fan speed 0-100
            filament_diameter: Filament diameter in mm
                max_volumetric_speed: Maximum volumetric speed in mm³/s
        """
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

        # Calculated values
        self.extrusion_width = nozzle_diameter * 1.2
        self.filament_radius = filament_diameter / 2.0
        self.filament_cross_section = math.pi * self.filament_radius ** 2

        self.gcode_lines: List[str] = []
        self.current_position = Vector3(0, 0, 0)
        self.current_extrusion = 0.0
        self.build_plate_center = Vector3(110, 110, 0)

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
            # Calculate total path length for spiral
            total_path_length = 0.0
            for i in range(len(spiral_points) - 1):
                p1 = spiral_points[i].position
                p2 = spiral_points[i + 1].position
                dx = p2.x - p1.x
                dy = p2.y - p1.y
                dz = p2.z - p1.z
                total_path_length += math.sqrt(dx * dx + dy * dy + dz * dz)

            # Unretract before print
            self.gcode_lines.append("; --- UNRETRACT BEFORE PRINT (SPIRAL) ---")
            self.gcode_lines.append("G11")
            self.gcode_lines.append("")

            # Move to first point as travel
            first_pos = spiral_points[0].position
            first_target = Vector3(first_pos.x + offset.x, first_pos.y + offset.y, first_pos.z + offset.z)
            self._add_move(first_target, extrusion_amount=0.0, is_travel=True)

            # Iterate spiral segments
            for idx in range(len(spiral_points) - 1):
                p_curr = spiral_points[idx].position
                p_next = spiral_points[idx + 1].position
                target = Vector3(p_next.x + offset.x, p_next.y + offset.y, p_next.z + offset.z)
                seg_dx = target.x - self.current_position.x
                seg_dy = target.y - self.current_position.y
                seg_dz = target.z - self.current_position.z
                seg_len = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy + seg_dz * seg_dz)
                extrusion = self._calculate_extrusion(seg_len)
                # Continuous spiral: always extrude (no retractions)
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
        """Add start GCode."""
        self.gcode_lines.append("; --- START GCODE ---")
        self.gcode_lines.append(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE={self.bed_temp}")
        self.gcode_lines.append(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE={self.nozzle_temp}")
        
        # Convert fan_speed (0-100%) to PWM value (0-255)
        pwm_value = int((self.fan_speed / 100.0) * 255)
        pwm_value = max(0, min(255, pwm_value))  # Clamp to 0-255
        self.gcode_lines.append(f"M106 S{pwm_value}")
        
        self.gcode_lines.append("START_PRINT")
        self.gcode_lines.append("")
        self.gcode_lines.append("; Absolute positioning for X/Y/Z, relative for E")
        self.gcode_lines.append("G90")
        self.gcode_lines.append("M83")
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
        
        # Step 1: Move to safe corner (219,219) at Z=2.0
        safe_corner = Vector3(219.0, 219.0, 2.0)
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
            first_rev = [p for p in spiral_points if p.revolution < 1.0]
            if first_rev:
                cx = sum(p.position.x for p in first_rev) / len(first_rev) + offset.x
                cy = sum(p.position.y for p in first_rev) / len(first_rev) + offset.y
                # Find the rightmost point (maximum X) on the perimeter
                max_x = max(p.position.x + offset.x for p in first_rev)
            else:
                # Fallback if no first revolution
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
        Generate skirt loop around the first revolution of the print.
        Skirt follows the wavy mesh pattern of the first layer.
        Printed at Z=layer_height before the main spiral starts.
        
        Args:
            spiral_points: All spiral points
            offset: Centering offset
        """
        if not self.skirt_enabled:
            return
        
        self.gcode_lines.append("; --- SKIRT FOR ADHESION ---")
        self.gcode_lines.append(f"; One loop at {self.skirt_distance}mm distance, {self.skirt_height} layer(s) tall")
        
        # Find first revolution (revolution = 0)
        first_rev_points = [p for p in spiral_points if p.revolution < 1.0]
        
        if not first_rev_points:
            logger.warning("No first revolution points found for skirt, skipping")
            return
        
        # Sort by angle to ensure continuous path
        first_rev_points = sorted(first_rev_points, key=lambda p: p.angle)
        
        # Calculate centroid of first revolution
        cx = sum(p.position.x for p in first_rev_points) / len(first_rev_points)
        cy = sum(p.position.y for p in first_rev_points) / len(first_rev_points)
        
        # Generate skirt points by offsetting first revolution points outward
        skirt_points = []
        for point in first_rev_points:
            # Radial direction from centroid
            dx = point.position.x - cx
            dy = point.position.y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            
            if dist > 0:
                # Offset outward by skirt_distance
                skirt_x = point.position.x + (dx / dist) * self.skirt_distance + offset.x
                skirt_y = point.position.y + (dy / dist) * self.skirt_distance + offset.y
                skirt_z = self.layer_height  # Skirt at first layer height
                skirt_points.append(Vector3(skirt_x, skirt_y, skirt_z))
        
        # Add closing point to complete loop
        if skirt_points:
            skirt_points.append(skirt_points[0])
        
        # Move to skirt start (travel)
        if skirt_points:
            self.gcode_lines.append("; Move to skirt start")
            self._add_move(skirt_points[0], is_travel=True)
            
            # Unretract before skirt
            self.gcode_lines.append("; Unretract before skirt")
            self.gcode_lines.append("G11")
            
            # Print skirt loop
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
            
            # Retract after skirt
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
                # Calculate extrusion
                extrusion = self._calculate_extrusion(segment_length)

                # Move with extrusion
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
            # Apply volumetric speed limit
            actual_speed = self._limit_speed_to_volumetric(speed, extrusion_amount)
            speed = min(speed, actual_speed)

        self.current_extrusion += extrusion_amount

        gcode = f"G1 X{target.x:.3f} Y{target.y:.3f} Z{target.z:.3f}"

        if extrusion_amount > 0:
            gcode += f" E{extrusion_amount:.5f}"

        gcode += f" F{speed * 60:.0f}"

        self.gcode_lines.append(gcode)
        self.current_position = target

    def _add_end_gcode(self) -> None:
        """Add end GCode with retract, Z raise, and safe park position."""
        self.gcode_lines.append("")
        self.gcode_lines.append("; --- END GCODE ---")
        self.gcode_lines.append("; Retract filament to prevent oozing")
        self.gcode_lines.append("G10")
        
        # Raise Z by 10mm to clear the print
        z_raise = 10.0
        new_z = self.current_position.z + z_raise
        self.gcode_lines.append(f"; Raise Z by {z_raise}mm to clear print")
        self.gcode_lines.append(f"G1 Z{new_z:.3f} F{self.travel_speed * 60:.0f}")
        self.current_position.z = new_z
        
        # Move to safe corner (219, 219)
        self.gcode_lines.append("; Move to safe corner")
        self.gcode_lines.append(f"G1 X219.000 Y219.000 F{self.travel_speed * 60:.0f}")
        self.current_position.x = 219.0
        self.current_position.y = 219.0
        
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

        # Plate center is at (110, 110) for 220x220 bed
        plate_center_x = 110.0
        plate_center_y = 110.0

        # Calculate offset to center model on plate
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
