'''
Spiral path generation for true vase mode printing.
Creates a continuous spiral that rises incrementally with Z while spiraling around the perimeter.
Hot path accelerated via Rust slicer_core extension (100x speedup over pure Python).
'''

import math
from typing import List, Tuple
from dataclasses import dataclass
from .stl_parser import Vector3
from .geometry_analyzer import Layer
from .logger import setup_logger

logger = setup_logger("spiral_generator")

try:
    import slicer_core as _rust_sc
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False


class _FlatPos:
    """Minimal position-like object backed by pre-allocated values (no allocation)."""
    __slots__ = ('x', 'y', 'z')

    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z


class _FlatSpiralPoint:
    """Minimal SpiralPoint-compatible object backed by flat arrays — created lazily."""
    __slots__ = ('position', 'angle', 'revolution', 'layer_index', 'is_extrusion')

    def __init__(self, x: float, y: float, z: float, angle: float, revolution: float):
        self.position = _FlatPos(x, y, z)
        self.angle = angle
        self.revolution = revolution
        self.layer_index = revolution  # same as revolution for spiral
        self.is_extrusion = True


class RustSpiralPoints:
    """
    List-like wrapper around flat Rust arrays.
    Presents the same interface as List[SpiralPoint] without allocating
    600k Python objects upfront.  Objects are created on demand.
    """

    def __init__(self, xs, ys, zs, angles, revolutions):
        self._xs = xs
        self._ys = ys
        self._zs = zs
        self._angles = angles
        self._revs = revolutions

    def __len__(self) -> int:
        return len(self._xs)

    def __getitem__(self, idx: int):
        return _FlatSpiralPoint(
            self._xs[idx], self._ys[idx], self._zs[idx],
            self._angles[idx], self._revs[idx]
        )

    def __iter__(self):
        for i in range(len(self._xs)):
            yield _FlatSpiralPoint(
                self._xs[i], self._ys[i], self._zs[i],
                self._angles[i], self._revs[i]
            )

    # Slicing support used by gcode_generator (first_rev filtering)
    def iter_first_revolution(self):
        """Yield only points where revolution < 1.0."""
        for i in range(len(self._xs)):
            if self._revs[i] < 1.0:
                yield _FlatSpiralPoint(
                    self._xs[i], self._ys[i], self._zs[i],
                    self._angles[i], self._revs[i]
                )


@dataclass
class SpiralPoint:
    """A point on the spiral path with position and metadata."""
    position: Vector3  # X, Y, Z coordinates
    angle: float  # Angle around perimeter (0-360 degrees, repeats per revolution)
    revolution: float  # Which revolution (0 = bottom, 1 = one full wrap, etc.)
    layer_index: float  # Which layer this corresponds to (fractional)
    is_extrusion: bool  # Whether extrusion should be active


class SpiralGenerator:
    """Generates continuous spiral paths for vase mode printing."""

    def __init__(
        self,
        layers: List[Layer],
        layer_height: float = 0.5,
        points_per_degree: float = 1.2,  # Resolution: points per degree of angle (default ~432 points/rev)
        smoothing_window_size: int = 3,
        smoothing_move_threshold: float = 0.5,
        target_samples_per_wave: int = 16,
        auto_resample_spiral: bool = True,
    ):
        """
        Initialize spiral generator.

        Args:
            layers: List of Layer objects from geometry analyzer
            layer_height: Z rise per full revolution (typically 0.5mm)
            points_per_degree: How many interpolated points per degree (higher = smoother)
        """
        self.layers = layers
        self.layer_height = layer_height
        self.points_per_degree = points_per_degree
        # Smoothing and sampling customization
        self.smoothing_window_size = max(1, int(smoothing_window_size))
        self.smoothing_move_threshold = float(smoothing_move_threshold)
        self.target_samples_per_wave = max(4, int(target_samples_per_wave))
        self.auto_resample_spiral = bool(auto_resample_spiral)
        
        if not layers:
            raise ValueError("No layers provided to spiral generator")

    # ──────────────────────────────────────────────────────────────────────────
    # Rust fast-path
    # ──────────────────────────────────────────────────────────────────────────

    def _generate_spiral_rust(
        self,
        wave_amplitude: float = 2.0,
        waves_per_rev: float = 0.0,
        wave_pattern: str = "sine",
        layer_alternation: int = 2,
        phase_offset: float = 50,
        seam_shift: float = 0.0,
        seam_revolution_offset: float = 0.0,
        seam_transition_waves: float = 0.0,
        base_integrity_manager=None,
        wave_asymmetry: bool = False,
        wave_asymmetry_intensity: float = 100,
        wave_skew: float = 0.0,
    ) -> 'RustSpiralPoints':
        """
        Generate spiral + wave in one Rust call.
        Returns a RustSpiralPoints list-alike (no Python object allocation).
        """
        layer_zs = [layer.z for layer in self.layers]
        layer_pts_x = [[p.x for p in layer.points] for layer in self.layers]
        layer_pts_y = [[p.y for p in layer.points] for layer in self.layers]

        # Derive base integrity params
        base_height = 28.0
        base_mode = "fewer_gaps"
        base_transition = "exponential"
        if base_integrity_manager is not None:
            base_height = getattr(base_integrity_manager, 'base_height', 28.0)
            base_mode = getattr(base_integrity_manager, 'mode', None)
            if base_mode is not None:
                base_mode = base_mode.value if hasattr(base_mode, 'value') else str(base_mode)
            else:
                base_mode = "fewer_gaps"
            base_transition = getattr(base_integrity_manager, 'transition', None)
            if base_transition is not None:
                base_transition = base_transition.value if hasattr(base_transition, 'value') else str(base_transition)
            else:
                base_transition = "exponential"

        xs, ys, zs, angles, revs = _rust_sc.generate_spiral_with_waves(
            layer_zs,
            layer_pts_x,
            layer_pts_y,
            float(self.layer_height),
            float(self.points_per_degree),
            float(wave_amplitude),
            float(waves_per_rev),
            str(wave_pattern),
            int(layer_alternation),
            float(phase_offset),
            float(seam_shift),
            float(seam_revolution_offset),
            float(seam_transition_waves),
            float(base_height),
            str(base_mode),
            str(base_transition),
            int(self.smoothing_window_size),
            float(self.smoothing_move_threshold),
            int(self.target_samples_per_wave),
            bool(wave_asymmetry),
            float(wave_asymmetry_intensity),
            float(wave_skew),
        )
        total = len(xs)
        logger.info(f"Rust spiral complete: {total} points (revolutions={total / max(1, int(360 * self.points_per_degree)):.1f})")
        return RustSpiralPoints(xs, ys, zs, angles, revs)

    def generate_spiral_path(self) -> List[SpiralPoint]:
        """
        Generate complete spiral path that continuously rises while spiraling around perimeter.

        The spiral makes one complete revolution (360°) per layer_height Z rise.
        
        Returns:
            List of SpiralPoint objects representing the continuous spiral path
        """
        spiral_points = []

        if len(self.layers) < 2:
            logger.warning("Only one layer available; spiral mode may not work well")
            if self.layers:
                return self._points_from_single_layer(self.layers[0])
            return []

        # Total number of revolutions = total Z height / layer_height
        total_z = self.layers[-1].z - self.layers[0].z
        num_revolutions = total_z / self.layer_height if self.layer_height > 0 else 1.0

        # Points per revolution = 360 degrees * points_per_degree
        points_per_revolution = int(360 * self.points_per_degree)
        if points_per_revolution < 1:
            points_per_revolution = 1

        # Total points in spiral
        total_points = int(points_per_revolution * num_revolutions)

        logger.info(f"Generating spiral: {num_revolutions:.2f} revolutions, {total_points} points")

        # Progress reporting
        last_progress = -1
        progress_interval = max(1, total_points // 20)  # Report every 5%

        for point_idx in range(total_points):
            # Report progress
            if point_idx % progress_interval == 0 or point_idx == total_points - 1:
                progress = int((point_idx / total_points) * 100)
                if progress != last_progress:
                    logger.info(f"  Spiral generation progress: {progress}%")
                    last_progress = progress

            # Angle around perimeter (0-360, repeats)
            angle = (point_idx % points_per_revolution) * (360 / points_per_revolution)

            # Revolution number (which wrap around the model)
            revolution = point_idx / points_per_revolution

            # Layer index (fractional, 0 = bottom layer, N = top layer)
            # Each revolution corresponds to layer_height rise
            layer_index = revolution

            # Z rises uniformly by layer_height per revolution, starting at the model's min Z.
            min_z = self.layers[0].z
            z = min_z + revolution * self.layer_height

            # Clamp layer index to available layers
            clamped_layer_idx = min(layer_index, len(self.layers) - 1)

            # Interpolate position around perimeter at this Z height
            position = self._interpolate_position_at_angle(angle, clamped_layer_idx)
            position.z = z

            spiral_point = SpiralPoint(
                position=position,
                angle=angle,
                revolution=revolution,
                layer_index=clamped_layer_idx,
                is_extrusion=True
            )

            spiral_points.append(spiral_point)

        # Apply smoothing to remove discontinuities
        spiral_points = self._smooth_spiral_path(spiral_points, points_per_revolution)

        return spiral_points

    def _smooth_spiral_path(self, spiral_points: List[SpiralPoint], points_per_revolution: int) -> List[SpiralPoint]:
        """
        Smooth spiral path to eliminate discontinuities from geometry interpolation.
        Uses a Gaussian-like filter to blend points and reject outliers.

        Args:
            spiral_points: Original spiral points
            points_per_revolution: Points in one complete revolution

        Returns:
            Smoothed spiral points
        """
        if len(spiral_points) < 3:
            return spiral_points

        # First pass: detect and reject outlier positions (large jumps)
        cleaned_points = []
        
        for i, point in enumerate(spiral_points):
            prev_point = spiral_points[i - 1] if i > 0 else None
            next_point = spiral_points[(i + 1) % len(spiral_points)]
            
            if prev_point is not None:
                # Check if this point creates an abnormally large jump
                dist_to_prev = math.hypot(
                    point.position.x - prev_point.position.x,
                    point.position.y - prev_point.position.y
                )
                dist_to_next = math.hypot(
                    next_point.position.x - point.position.x,
                    next_point.position.y - point.position.y
                )
                
                # If both jumps around this point are large, it's likely an outlier
                # Use a threshold based on expected perimeter step size (~0.3-1.0mm per point at normal sampling)
                if dist_to_prev > 2.0 and dist_to_next > 2.0:
                    # This point is likely an outlier; use interpolation from neighbors
                    interp_x = (prev_point.position.x + next_point.position.x) / 2.0
                    interp_y = (prev_point.position.y + next_point.position.y) / 2.0
                    corrected_pos = Vector3(interp_x, interp_y, point.position.z)
                    
                    corrected_point = SpiralPoint(
                        position=corrected_pos,
                        angle=point.angle,
                        revolution=point.revolution,
                        layer_index=point.layer_index,
                        is_extrusion=point.is_extrusion
                    )
                    cleaned_points.append(corrected_point)
                else:
                    cleaned_points.append(point)
            else:
                cleaned_points.append(point)
        
        # Second pass: apply minimal smoothing with configurable window and threshold
        smoothed = []
        window_size = max(1, int(self.smoothing_window_size))
        half_window = window_size // 2

        # Construct simple symmetric weights that favor the center
        center_weight = 0.6
        side_total = 1.0 - center_weight
        side_count = window_size - 1
        if side_count > 0:
            side_weight = side_total / side_count
            weights = [side_weight] * half_window + [center_weight] + [side_weight] * half_window
        else:
            weights = [1.0]

        weight_sum = sum(weights)

        for i, point in enumerate(cleaned_points):
            neighbors_x = []
            neighbors_y = []

            for offset in range(-half_window, half_window + 1):
                neighbor_idx = (i + offset) % len(cleaned_points)
                neighbor = cleaned_points[neighbor_idx]
                neighbors_x.append(neighbor.position.x)
                neighbors_y.append(neighbor.position.y)

            # Compute weighted average
            avg_x = sum(nx * w for nx, w in zip(neighbors_x, weights)) / weight_sum
            avg_y = sum(ny * w for ny, w in zip(neighbors_y, weights)) / weight_sum

            # Only apply smoothing if move is very small (preserve wave pattern)
            dx = avg_x - point.position.x
            dy = avg_y - point.position.y
            move_dist = math.hypot(dx, dy)

            if move_dist < float(self.smoothing_move_threshold):
                new_pos = Vector3(avg_x, avg_y, point.position.z)
            else:
                new_pos = point.position

            smoothed_point = SpiralPoint(
                position=new_pos,
                angle=point.angle,
                revolution=point.revolution,
                layer_index=point.layer_index,
                is_extrusion=point.is_extrusion
            )
            smoothed.append(smoothed_point)

        return smoothed

    def _interpolate_position_at_angle(self, angle: float, layer_index: float) -> Vector3:
        """
        Interpolate position around perimeter at given angle and layer.

        Args:
            angle: Angle around perimeter (0-360 degrees)
            layer_index: Which layer (fractional for interpolation between layers)

        Returns:
            Interpolated position on the perimeter at that angle
        """
        # Get the lower and upper layer for interpolation
        lower_idx = int(math.floor(layer_index))
        upper_idx = int(math.ceil(layer_index))
        
        # Handle boundary cases
        lower_idx = max(0, min(lower_idx, len(self.layers) - 1))
        upper_idx = max(0, min(upper_idx, len(self.layers) - 1))

        lower_layer = self.layers[lower_idx]
        upper_layer = self.layers[upper_idx]

        # Interpolation factor (0 = lower layer, 1 = upper layer)
        t = layer_index - lower_idx if upper_idx != lower_idx else 0.0

        # Get positions on perimeter at this angle
        lower_pos = self._get_position_at_angle(lower_layer, angle)
        upper_pos = self._get_position_at_angle(upper_layer, angle)

        # Linear interpolation between layers
        if t == 0:
            return lower_pos
        elif t == 1:
            return upper_pos
        else:
            interp_x = lower_pos.x + t * (upper_pos.x - lower_pos.x)
            interp_y = lower_pos.y + t * (upper_pos.y - lower_pos.y)
            interp_z = lower_pos.z + t * (upper_pos.z - lower_pos.z)
            return Vector3(interp_x, interp_y, interp_z)

    def _get_position_at_angle(self, layer: Layer, angle: float) -> Vector3:
        """
        Get the position on a layer's perimeter at a specific angle from center.

        Args:
            layer: Layer object with perimeter points
            angle: Angle in degrees (0-360) from layer center

        Returns:
            Position on the perimeter at that angle
        """
        if not layer.points:
            return Vector3(0, 0, layer.z)

        # Compute polygon centroid (area-weighted) for more robust center
        cx = 0.0
        cy = 0.0
        area_acc = 0.0
        pts = layer.points
        
        for i in range(len(pts)):
            x0, y0 = pts[i].x, pts[i].y
            x1, y1 = pts[(i + 1) % len(pts)].x, pts[(i + 1) % len(pts)].y
            cross = x0 * y1 - x1 * y0
            area_acc += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        
        if abs(area_acc) > 1e-9:
            area = area_acc * 0.5
            cx = cx / (6.0 * area)
            cy = cy / (6.0 * area)
        else:
            # Fallback to simple average if degenerate
            cx = sum(p.x for p in pts) / len(pts)
            cy = sum(p.y for p in pts) / len(pts)
        
        # Compute perimeter radius (min and max distance from centroid to perimeter)
        min_radius = float('inf')
        max_radius = 0.0
        for p in pts:
            dxr = p.x - cx
            dyr = p.y - cy
            dist = math.hypot(dxr, dyr)
            min_radius = min(min_radius, dist)
            max_radius = max(max_radius, dist)
        
        if min_radius == float('inf'):
            min_radius = 0.0
        
        # Average radius for validation
        avg_radius = (min_radius + max_radius) / 2.0 if max_radius > 1e-6 else 1.0
        max_allow_t = max_radius * 1.3  # Allow up to 1.3x the max radius

        angle_rad = math.radians(angle)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)

        closest_t = None
        closest_ix = None
        closest_iy = None

        # Intersect ray from center with each segment of the polygon
        for i in range(len(layer.points)):
            p1 = layer.points[i]
            p2 = layer.points[(i + 1) % len(layer.points)]
            x1, y1 = p1.x, p1.y
            x2, y2 = p2.x, p2.y
            vx = x2 - x1
            vy = y2 - y1

            denom = dx * (-vy) - dy * (-vx)
            if abs(denom) < 1e-9:
                continue

            rx = x1 - cx
            ry = y1 - cy

            t = (rx * (-vy) - ry * (-vx)) / denom
            s = (dx * ry - dy * rx) / denom

            # Require intersection in front of the ray, on the segment, and within reasonable radius
            if t >= 1e-6 and 0.0 <= s <= 1.0:
                if max_allow_t is not None and t > max_allow_t:
                    # Intersection too far away — ignore
                    continue
                ix = cx + t * dx
                iy = cy + t * dy
                if closest_t is None or t < closest_t:
                    closest_t = t
                    closest_ix = ix
                    closest_iy = iy

        # Validate that the intersection is reasonable (not in the opposite direction or too far)
        if closest_ix is not None:
            dist_from_center = math.hypot(closest_ix - cx, closest_iy - cy)
            # Sanity check: intersection should be within 20% of average radius
            if dist_from_center >= min_radius * 0.8 and dist_from_center <= max_radius * 1.2:
                return Vector3(closest_ix, closest_iy, layer.z)

        # Fallback: Find closest point on perimeter in the ray direction
        # This handles edge cases where the ray doesn't intersect properly
        best_point = None
        best_distance = float('inf')
        
        for i in range(len(layer.points)):
            p1 = layer.points[i]
            p2 = layer.points[(i + 1) % len(layer.points)]
            x1, y1 = p1.x, p1.y
            x2, y2 = p2.x, p2.y
            
            # Find closest point on segment that lies in ray direction
            vx = x2 - x1
            vy = y2 - y1
            rx = cx - x1
            ry = cy - y1
            
            # Project center onto segment
            seg_len_sq = vx * vx + vy * vy
            if seg_len_sq > 1e-9:
                t_seg = (rx * vx + ry * vy) / seg_len_sq
                t_seg = max(0.0, min(1.0, t_seg))
            else:
                t_seg = 0.0
            
            # Point on segment
            px = x1 + t_seg * vx
            py = y1 + t_seg * vy
            
            # Check if this point is in the direction of the ray
            rdx = px - cx
            rdy = py - cy
            dot_product = rdx * dx + rdy * dy
            
            if dot_product >= -1e-6:  # Point is in forward or lateral direction of ray
                dist = rdx * rdx + rdy * rdy
                if dist < best_distance:
                    best_distance = dist
                    best_point = Vector3(px, py, layer.z)
        
        if best_point is not None:
            return best_point
        
        # Last resort: return closest perimeter point to ray direction
        return Vector3(pts[0].x, pts[0].y, layer.z)

    def _points_from_single_layer(self, layer: Layer) -> List[SpiralPoint]:
        """Handle case with only one layer by returning its perimeter points as spiral."""
        spiral_points = []

        if not layer.points:
            return spiral_points

        points_per_revolution = int(360 * self.points_per_degree)

        for i, point in enumerate(layer.points):
            angle = (i / len(layer.points)) * 360
            spiral_point = SpiralPoint(
                position=point,
                angle=angle,
                revolution=0.0,
                layer_index=0.0,
                is_extrusion=True
            )
            spiral_points.append(spiral_point)

        return spiral_points

    def apply_wave_to_spiral(
        self,
        spiral_points: List[SpiralPoint],
        wave_amplitude: float = 2.0,
        wave_count: int = None,
        wave_spacing: float = None,
        wave_pattern: str = "sine",
        layer_alternation: int = 2,
        phase_offset: float = 50,
        wave_asymmetry: bool = False,
        wave_asymmetry_intensity: float = 100,
        base_integrity_manager = None,
        seam_shift: float = 0.0,
        seam_revolution_offset: float = 0.0,
        seam_transition_waves: float = 0.0,
    ) -> List[SpiralPoint]:
        """
        Apply wave pattern to spiral path with optional layer alternation.
        Waves are applied perpendicular to the spiral direction (in X/Y plane only).
        Inner diameter stays at original perimeter.

        Args:
            spiral_points: Original spiral path
            wave_amplitude: How far waves extend outward (mm)
            wave_count: Number of waves per revolution (takes precedence over wave_spacing)
            wave_spacing: Distance per wave wavelength (mm) - used if wave_count is None
            wave_pattern: "sine", "triangular", or "sawtooth"
            layer_alternation: Every N revolutions, flip the phase (creates diamond gaps)
            phase_offset: Phase offset percentage (0-100) applied every layer_alternation
            wave_asymmetry: Enable asymmetric waves (shallow rise, steep fall)
            wave_asymmetry_intensity: 0-100, blend between symmetric (0) and asymmetric (100)
            seam_shift: Extend alternation cycle by this many waves (shifts seam location)

        Returns:
            Modified spiral points with waves applied
        """
        if not spiral_points:
            return spiral_points

        if wave_amplitude <= 0:
            return spiral_points

        # Determine wave count per revolution (waves per 360 degrees)
        if wave_count:
            waves_per_rev = float(wave_count)
        else:
            # Convert wave_spacing to wave count using average perimeter (first layer reference)
            if self.layers and wave_spacing and wave_spacing > 0:
                avg_perimeter = self.layers[0].calculate_perimeter_length()
                waves_per_rev = (avg_perimeter / wave_spacing) if avg_perimeter > 0 else 0.0
            else:
                waves_per_rev = 0.0

        # waves_per_rev is number of waves per full 360° revolution
        logger.info(
            f"Applying wave pattern: waves_per_rev={waves_per_rev:.4f}, amplitude={wave_amplitude}mm, "
            f"layer_alternation={layer_alternation}, phase_offset={phase_offset}%, seam_shift={seam_shift}"
        )

        # Ensure spiral sampling is dense enough to represent the requested wave frequency
        # Target: at least self.target_samples_per_wave samples per wavelength to avoid aliasing
        target_samples_per_wave = int(self.target_samples_per_wave)
        orig_ppd = float(self.points_per_degree)
        required_ppd = orig_ppd
        if waves_per_rev > 0:
            required_points_per_rev = max(int(360 * orig_ppd), int(math.ceil(waves_per_rev * target_samples_per_wave)))
            required_ppd = max(orig_ppd, required_points_per_rev / 360.0)

        regenerated_spiral = None
        if self.auto_resample_spiral and required_ppd > orig_ppd * 1.01:
            # Temporarily regenerate spiral at higher density to avoid undersampling waves
            logger.info(f"Regenerating spiral at {required_ppd:.2f} points/degree to avoid aliasing")
            # Save and restore original points_per_degree
            self_points_backup = self.points_per_degree
            try:
                self.points_per_degree = required_ppd
                regenerated_spiral = self.generate_spiral_path()
            finally:
                self.points_per_degree = self_points_backup

        if regenerated_spiral is not None:
            # Use the higher-density spiral for wave application
            spiral_source = regenerated_spiral
        else:
            spiral_source = spiral_points

        modified_points = []
        
        # Pre-compute layer centers (area-weighted centroid) for consistent wave application
        layer_centers = {}
        for idx, layer in enumerate(self.layers):
            if not layer.points:
                layer_centers[idx] = (0.0, 0.0)
                continue
            
            # Compute polygon centroid (area-weighted) — same method as _get_position_at_angle
            cx = 0.0
            cy = 0.0
            area_acc = 0.0
            pts = layer.points
            
            for i in range(len(pts)):
                x0, y0 = pts[i].x, pts[i].y
                x1, y1 = pts[(i + 1) % len(pts)].x, pts[(i + 1) % len(pts)].y
                cross = x0 * y1 - x1 * y0
                area_acc += cross
                cx += (x0 + x1) * cross
                cy += (y0 + y1) * cross
            
            if abs(area_acc) > 1e-9:
                area = area_acc * 0.5
                cx = cx / (6.0 * area)
                cy = cy / (6.0 * area)
            else:
                # Fallback to simple average if degenerate
                cx = sum(p.x for p in pts) / len(pts)
                cy = sum(p.y for p in pts) / len(pts)
            
            layer_centers[idx] = (cx, cy)

        for spiral_point in spiral_source:
            if not spiral_point.is_extrusion:
                modified_points.append(spiral_point)
                continue

            # Calculate wave offset based on angular position around the model.
            # Use a floating waves-per-revolution value so phase is smooth and symmetric.
            # phase (degrees) = angle (deg) * waves_per_rev  (mod 360)
            base_phase = (spiral_point.angle * waves_per_rev) % 360.0

            # Blend wave VALUES (not phase constants) for a true crossfade:
            # sin(θ) blended with sin(θ+180°)=-sin(θ) fades amplitude to 0
            # at mid-transition then rebuilds in the opposite phase.
            if layer_alternation > 0 and phase_offset > 0:
                cycle_len_revs = float(layer_alternation)
                if seam_shift != 0 and waves_per_rev > 0:
                    cycle_len_revs += (seam_shift / waves_per_rev)

                adjusted_rev = spiral_point.revolution + seam_revolution_offset
                cycle = int(adjusted_rev / cycle_len_revs)
                cur_shift = (cycle % 2) * (phase_offset / 100.0) * 360.0
                cur_wave = self._calculate_wave_value(
                    (base_phase + cur_shift) % 360.0, wave_pattern)

                if seam_transition_waves > 0 and waves_per_rev > 0:
                    cycle_progress = (adjusted_rev / cycle_len_revs) % 1.0
                    transition_frac = seam_transition_waves / (waves_per_rev * cycle_len_revs)
                    if transition_frac > 0 and cycle_progress > 1.0 - transition_frac:
                        nxt_shift = ((cycle + 1) % 2) * (phase_offset / 100.0) * 360.0
                        nxt_wave = self._calculate_wave_value(
                            (base_phase + nxt_shift) % 360.0, wave_pattern)
                        t = (cycle_progress - (1.0 - transition_frac)) / transition_frac
                        t_smooth = 0.5 - 0.5 * math.cos(math.pi * t)
                        wave_raw = cur_wave * (1.0 - t_smooth) + nxt_wave * t_smooth
                    else:
                        wave_raw = cur_wave
                else:
                    wave_raw = cur_wave
            else:
                wave_raw = self._calculate_wave_value(base_phase % 360.0, wave_pattern)
            
            # Apply base integrity amplitude factor (reduces waves at base)
            amplitude_factor = 1.0
            if base_integrity_manager is not None:
                amplitude_factor = base_integrity_manager.get_amplitude_factor(spiral_point.position.z)

            # ALWAYS apply waves outward-only to preserve inner diameter
            # Convert wave from [-1, 1] to [0, 1] range (0 = at perimeter, 1 = full amplitude out)
            # Asymmetry controls wave SHAPE (symmetric vs steep), not direction
            if wave_asymmetry:
                # Asymmetric waves: steep rise, shallow fall (or vice versa)
                # Map intensity: 0 = symmetric [0,1], 100 = fully asymmetric with sharp transitions
                asymmetry_blend = wave_asymmetry_intensity / 100.0
                wave_symmetric = (wave_raw + 1.0) * 0.5  # [-1,1] -> [0,1]
                # For asymmetric effect, steepen one side of the wave
                if wave_raw < 0:
                    # Trough side: make it steeper
                    wave_asymmetric = (wave_raw + 1.0) * 0.5 * (2.0 - asymmetry_blend)
                else:
                    # Peak side: make it more gradual  
                    wave_asymmetric = 0.5 + (wave_raw * 0.5) / (2.0 - asymmetry_blend)
                wave_norm = wave_symmetric * (1.0 - asymmetry_blend) + wave_asymmetric * asymmetry_blend
                wave_norm = max(0.0, min(1.0, wave_norm))
            else:
                # Symmetric sine wave, but still outward-only
                # Map [-1, 1] to [0, 1] where 0 = at original perimeter, 1 = full amplitude outward
                wave_norm = (wave_raw + 1.0) * 0.5

            # Apply amplitude: wave_norm is [0,1], so offset is [0, amplitude]
            # This ensures waves ONLY go outward, never inward
            offset_magnitude = wave_norm * wave_amplitude * amplitude_factor

            # Interpolate layer center based on fractional layer_index
            lower_idx = int(math.floor(spiral_point.layer_index))
            upper_idx = int(math.ceil(spiral_point.layer_index))
            lower_idx = max(0, min(lower_idx, len(self.layers) - 1))
            upper_idx = max(0, min(upper_idx, len(self.layers) - 1))
            
            t = spiral_point.layer_index - lower_idx if upper_idx != lower_idx else 0.0
            
            lower_cx, lower_cy = layer_centers[lower_idx]
            upper_cx, upper_cy = layer_centers[upper_idx]
            
            # Interpolate center position
            center_x = lower_cx + t * (upper_cx - lower_cx)
            center_y = lower_cy + t * (upper_cy - lower_cy)

            # Radial direction (from center outward)
            radial_x = spiral_point.position.x - center_x
            radial_y = spiral_point.position.y - center_y
            radial_mag = math.sqrt(radial_x**2 + radial_y**2)

            if radial_mag > 0:
                # Normalize radial direction
                radial_x /= radial_mag
                radial_y /= radial_mag

                # Apply offset in radial direction
                new_x = spiral_point.position.x + radial_x * offset_magnitude
                new_y = spiral_point.position.y + radial_y * offset_magnitude
            else:
                new_x = spiral_point.position.x
                new_y = spiral_point.position.y

            # Create modified point (Z unchanged)
            modified_point = SpiralPoint(
                position=Vector3(new_x, new_y, spiral_point.position.z),
                angle=spiral_point.angle,
                revolution=spiral_point.revolution,
                layer_index=spiral_point.layer_index,
                is_extrusion=spiral_point.is_extrusion
            )

            modified_points.append(modified_point)

        return modified_points

    def _calculate_wave_value(self, phase_degrees: float, pattern: str) -> float:
        """
        Calculate wave value at given phase (-1 to 1).

        Args:
            phase_degrees: Phase angle in degrees
            pattern: "sine", "triangular", or "sawtooth"

        Returns:
            Wave value from -1 to 1
        """
        # Normalize phase to 0-360
        phase = phase_degrees % 360
        phase_rad = math.radians(phase)

        if pattern == "sine":
            return math.sin(phase_rad)
        elif pattern == "triangular":
            # Triangular: -1 to 1 and back in 360 degrees
            if phase < 180:
                return -1 + 2 * (phase / 180)  # -1 to 1
            else:
                return 1 - 2 * ((phase - 180) / 180)  # 1 to -1
        elif pattern == "sawtooth":
            # Sawtooth: -1 to 1 per 180 degrees
            phase_180 = phase % 180
            return -1 + 2 * (phase_180 / 180)
        else:
            return 0.0
