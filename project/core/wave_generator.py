'''
Wave pattern generation for meshification.
NumPy-vectorized for ~20x speedup on wave computation.
'''

import math
from typing import List, Tuple
from dataclasses import dataclass
from .stl_parser import Vector3
from .logger import setup_logger

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import slicer_core as _rust
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False

logger = setup_logger("wave_generator")


@dataclass
class WavePoint:
    """Point on wave-modified perimeter."""
    original: Vector3
    modified: Vector3
    center: Vector3
    amplitude_factor: float  # 0.0 to 1.0


class WaveGenerator:
    """Generates sinusoidal/triangular/sawtooth wave patterns on perimeters."""

    def __init__(
        self,
        amplitude: float = 2.0,
        spacing: float = 4.0,
        smoothness: int = 10,
        pattern_type: str = "sine",
        start_phase: float = 0.0
    ):
        """
        Initialize wave generator.

        Args:
            amplitude: Distance from perimeter to peak (mm)
            spacing: Wavelength / peak-to-peak distance (mm)
            smoothness: 1-10, controls wave sharpness
            pattern_type: 'sine', 'triangular', or 'sawtooth'
            start_phase: Starting phase in degrees (0-360)
        """
        self.amplitude = amplitude
        self.spacing = spacing
        self.smoothness = max(1, min(10, smoothness))
        self.pattern_type = pattern_type.lower()
        self.start_phase = start_phase

        if self.pattern_type not in ["sine", "triangular", "sawtooth"]:
            self.pattern_type = "sine"

        logger.info(
            f"Wave generator: {self.pattern_type} pattern, "
            f"amplitude={amplitude}mm, spacing={spacing}mm"
        )

    def generate_wave_points(
        self,
        perimeter_points: List[Vector3],
        amplitude_factor: float = 1.0,
        phase_offset: float = 0.0,
    ) -> List[WavePoint]:
        """
        Generate wave-modified perimeter points.

        Args:
            perimeter_points: Original perimeter points in order
            amplitude_factor: Multiply amplitude by this (for base transitions)
            phase_offset: Phase shift in percent (0-100)

        Returns:
            List of WavePoint objects
        """
        if len(perimeter_points) < 3:
            return [WavePoint(p, p, Vector3(0, 0, 0), amplitude_factor) for p in perimeter_points]

        if _HAS_RUST:
            return self._generate_wave_points_rust(perimeter_points, amplitude_factor, phase_offset)
        if _HAS_NUMPY:
            return self._generate_wave_points_numpy(perimeter_points, amplitude_factor, phase_offset)

        # --- Pure Python fallback ---
        # Calculate center (center of mass)
        center_x = sum(p.x for p in perimeter_points) / len(perimeter_points)
        center_y = sum(p.y for p in perimeter_points) / len(perimeter_points)
        center_z = perimeter_points[0].z if perimeter_points else 0
        center = Vector3(center_x, center_y, center_z)

        # Calculate perimeter arc length and point positions
        arc_lengths = [0.0]
        total_length = 0.0

        for i in range(len(perimeter_points)):
            p1 = perimeter_points[i]
            p2 = perimeter_points[(i + 1) % len(perimeter_points)]
            dx = p2.x - p1.x
            dy = p2.y - p1.y
            segment_length = math.sqrt(dx**2 + dy**2)
            total_length += segment_length
            arc_lengths.append(total_length)

        # Generate wave points
        wave_points = []

        for i, original_point in enumerate(perimeter_points):
            # Distance along perimeter
            arc_dist = arc_lengths[i]

            # Wave phase based on arc distance
            wave_phase = (arc_dist / self.spacing) * 360.0
            wave_phase = (wave_phase + self.start_phase + phase_offset) % 360.0

            # Calculate raw wave value in range [-1, 1]
            wave_raw = self._calculate_wave_value(wave_phase)
            # Apply smoothness adjustment if any
            wave_adj = self.adjust_for_smoothness(wave_raw)
            # Map to outward-only offset: [-1,1] -> [0,1]
            wave_offset = (wave_adj + 1.0) * 0.5

            # Apply amplitude (outward-only)
            applied_amplitude = self.amplitude * amplitude_factor * wave_offset

            # Direction from center to point (outward normal)
            dx = original_point.x - center.x
            dy = original_point.y - center.y
            distance_from_center = math.sqrt(dx**2 + dy**2)

            if distance_from_center > 0.001:
                # Normalize outward direction
                out_dir_x = dx / distance_from_center
                out_dir_y = dy / distance_from_center

                # Apply wave offset in outward direction
                modified = Vector3(
                    original_point.x + out_dir_x * applied_amplitude,
                    original_point.y + out_dir_y * applied_amplitude,
                    original_point.z
                )
            else:
                modified = original_point

            wave_points.append(WavePoint(
                original=original_point,
                modified=modified,
                center=center,
                amplitude_factor=amplitude_factor
            ))

        return wave_points

    def _generate_wave_points_rust(
        self,
        perimeter_points: List[Vector3],
        amplitude_factor: float,
        phase_offset: float,
    ) -> List[WavePoint]:
        """Rust-backed wave generation — fastest path."""
        n = len(perimeter_points)
        pts_x = [p.x for p in perimeter_points]
        pts_y = [p.y for p in perimeter_points]

        mod_x, mod_y = _rust.generate_waves(
            pts_x, pts_y,
            self.amplitude, amplitude_factor,
            self.spacing, self.pattern_type,
            self.start_phase, phase_offset,
            self.smoothness,
        )

        center_x = sum(pts_x) / n
        center_y = sum(pts_y) / n
        center_z = float(perimeter_points[0].z)
        center = Vector3(center_x, center_y, center_z)

        result = []
        for i in range(n):
            orig = perimeter_points[i]
            mod = Vector3(float(mod_x[i]), float(mod_y[i]), orig.z)
            result.append(WavePoint(
                original=orig,
                modified=mod,
                center=center,
                amplitude_factor=amplitude_factor,
            ))
        return result

    def _generate_wave_points_numpy(
        self,
        perimeter_points: List[Vector3],
        amplitude_factor: float,
        phase_offset: float,
    ) -> List[WavePoint]:
        """NumPy-vectorized wave generation — ~20x faster than pure Python loop."""
        n = len(perimeter_points)
        pts = np.empty((n, 3), dtype=np.float64)
        for i, p in enumerate(perimeter_points):
            pts[i] = (p.x, p.y, p.z)

        center_x = pts[:, 0].mean()
        center_y = pts[:, 1].mean()
        center_z = float(pts[0, 2])
        center = Vector3(center_x, center_y, center_z)

        # Arc lengths along perimeter
        rolled = np.roll(pts[:, :2], -1, axis=0)
        segs = np.linalg.norm(rolled - pts[:, :2], axis=1)
        segs[-1] = np.linalg.norm(pts[0, :2] - pts[-1, :2])  # close loop
        arc = np.concatenate([[0.0], np.cumsum(segs[:-1])])

        # Wave phase for each point
        wave_phase_deg = (arc / self.spacing) * 360.0
        wave_phase_deg = (wave_phase_deg + self.start_phase + phase_offset) % 360.0
        phase_rad = np.radians(wave_phase_deg)

        # Wave value [-1, 1]
        if self.pattern_type == "sine":
            wave_raw = np.sin(phase_rad)
        elif self.pattern_type == "triangular":
            norm = wave_phase_deg / 360.0
            wave_raw = np.where(
                norm < 0.25, norm * 4.0,
                np.where(norm < 0.75, 2.0 - norm * 4.0, norm * 4.0 - 4.0)
            )
        elif self.pattern_type == "sawtooth":
            norm = wave_phase_deg / 360.0
            wave_raw = norm * 2.0 - 1.0
        else:
            wave_raw = np.sin(phase_rad)

        # Smoothness: sign-preserving power function
        if self.smoothness < 10:
            exp = 2.0 - (self.smoothness - 1) * (2.0 - 0.2) / 9.0
            wave_adj = np.sign(wave_raw) * (np.abs(wave_raw) ** exp)
        else:
            wave_adj = wave_raw

        # Map [-1,1] → [0,1], apply amplitude
        wave_offset = (wave_adj + 1.0) * 0.5
        applied_amp = self.amplitude * amplitude_factor * wave_offset

        # Outward direction from center
        dx = pts[:, 0] - center_x
        dy = pts[:, 1] - center_y
        dist = np.sqrt(dx**2 + dy**2)
        valid = dist > 0.001
        out_x = np.where(valid, dx / dist, 0.0)
        out_y = np.where(valid, dy / dist, 0.0)

        mod_x = pts[:, 0] + out_x * applied_amp
        mod_y = pts[:, 1] + out_y * applied_amp

        result = []
        for i in range(n):
            orig = perimeter_points[i]
            mod = Vector3(float(mod_x[i]), float(mod_y[i]), orig.z)
            result.append(WavePoint(
                original=orig,
                modified=mod,
                center=center,
                amplitude_factor=amplitude_factor,
            ))
        return result

    def _calculate_wave_value(self, phase_degrees: float) -> float:
        """
        Calculate wave value at phase (0-1 range representing -1 to 1 for sine).
        Returns value in range [-1, 1].
        """
        phase_rad = math.radians(phase_degrees)

        if self.pattern_type == "sine":
            return math.sin(phase_rad)

        elif self.pattern_type == "triangular":
            # Triangular wave: goes from -1 to 1 to -1 over 360 degrees
            # Normalize to 0-1 range of period
            normalized = (phase_degrees % 360.0) / 360.0

            if normalized < 0.25:
                return normalized * 4.0  # 0 to 1
            elif normalized < 0.75:
                return 2.0 - normalized * 4.0  # 1 to -1
            else:
                return normalized * 4.0 - 4.0  # -1 to 0

        elif self.pattern_type == "sawtooth":
            # Sawtooth wave: ramps from -1 to 1
            normalized = (phase_degrees % 360.0) / 360.0
            return normalized * 2.0 - 1.0

        return math.sin(phase_rad)

    def adjust_for_smoothness(self, wave_value: float) -> float:
        """
        Adjust wave value based on smoothness setting.
        Higher smoothness (10) = pure wave, lower (1) = more aggressive.
        """
        if self.smoothness == 10:
            return wave_value

        # Apply power function to make waves sharper at lower smoothness
        # smoothness 1-9 maps to exponent 2-0.2
        exponent = 2.0 - (self.smoothness - 1) * (2.0 - 0.2) / 9.0

        # Preserve sign
        if wave_value >= 0:
            return wave_value ** exponent
        else:
            return -((-wave_value) ** exponent)


class LayerAlternationController:
    """Manages layer alternation and phase offset for mesh gap creation."""

    def __init__(
        self,
        alternation_period: int = 2,
        phase_offset: float = 50.0
    ):
        """
        Initialize alternation controller.

        Args:
            alternation_period: Number of layers before alternation
            phase_offset: Phase shift percentage between alternations
        """
        self.alternation_period = max(1, alternation_period)
        self.phase_offset = max(0, min(100, phase_offset))
        self.current_phase = 0

    def get_phase_for_layer(self, layer_index: int) -> float:
        """
        Get phase offset for specific layer.

        Args:
            layer_index: 0-based layer index

        Returns:
            Phase offset in degrees (0-360)
        """
        # Determine which alternation cycle we're in
        cycle = (layer_index // self.alternation_period) % 2

        # Alternation phase in degrees (0-360 range)
        phase = (cycle * self.phase_offset) % 360.0

        return phase

    def get_amplitude_factor_for_layer(self, layer_index: int) -> float:
        """
        Get amplitude factor (always 1.0 unless modified by base transitions).
        Returns 1.0 for standard layers.
        """
        return 1.0
