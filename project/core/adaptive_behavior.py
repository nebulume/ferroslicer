'''
Adaptive wave behavior based on geometry curvature and diameter changes.
'''

import math
from typing import List, Tuple
from .geometry_analyzer import CurvatureAnalyzer
from .stl_parser import Vector3
from .logger import setup_logger

logger = setup_logger("adaptive_behavior")


class CurvatureAdaptation:
    """Manages wave adaptation based on perimeter curvature."""

    def __init__(
        self,
        angle_threshold: float = 30.0,
        distance_threshold: float = 10.0,
        amplitude_reduction: float = 60.0,
        frequency_reduction: float = 40.0,
        transition_smoothness: str = "medium"
    ):
        """
        Initialize curvature adaptation.

        Args:
            angle_threshold: Degrees of angle change to trigger reduction
            distance_threshold: Distance in mm over which angle is measured
            amplitude_reduction: Percent reduction (0-100)
            frequency_reduction: Percent reduction (0-100)
            transition_smoothness: 'instant', 'fast', 'medium', 'slow'
        """
        self.angle_threshold = angle_threshold
        self.distance_threshold = distance_threshold
        self.amplitude_reduction = max(0, min(100, amplitude_reduction))
        self.frequency_reduction = max(0, min(100, frequency_reduction))

        # Map smoothness to segment count
        smoothness_map = {
            "instant": 1,
            "fast": 3,
            "medium": 5,
            "slow": 10
        }
        self.transition_segments = smoothness_map.get(transition_smoothness, 5)

        logger.info(
            f"Curvature adaptation: threshold={angle_threshold}°, "
            f"amplitude_reduction={amplitude_reduction}%, "
            f"frequency_reduction={frequency_reduction}%"
        )

    def analyze_curvature_regions(
        self,
        perimeter_points: List[Vector3]
    ) -> List[Tuple[int, float]]:
        """
        Identify high-curvature regions and calculate reduction factor for each point.

        Args:
            perimeter_points: Ordered list of perimeter points

        Returns:
            List of (point_index, reduction_factor) tuples
        """
        if len(perimeter_points) < 3:
            return [(i, 1.0) for i in range(len(perimeter_points))]

        # Calculate curvatures
        curvatures = CurvatureAnalyzer.analyze_perimeter_curvature(
            perimeter_points,
            window_size=3
        )

        # Find high-curvature regions
        high_regions = CurvatureAnalyzer.identify_high_curvature_regions(
            curvatures,
            threshold=self.angle_threshold
        )

        # Create reduction factor map
        reduction_factors = [1.0] * len(perimeter_points)

        for region_start, region_end in high_regions:
            # Apply smooth transition around region
            for i in range(len(perimeter_points)):
                distance_to_region = self._distance_to_region(
                    i, region_start, region_end, len(perimeter_points)
                )

                if distance_to_region < self.transition_segments:
                    # Smoothly transition based on distance
                    transition_factor = distance_to_region / self.transition_segments
                    reduction = 1.0 - (self.amplitude_reduction / 100.0) * (1.0 - transition_factor)
                    reduction_factors[i] = min(reduction_factors[i], reduction)

        return list(enumerate([1.0 - (self.amplitude_reduction / 100.0) * (1.0 - f)
                               for f in reduction_factors]))

    @staticmethod
    def _distance_to_region(
        point_idx: int,
        region_start: int,
        region_end: int,
        total_points: int
    ) -> float:
        """
        Calculate distance from point to region (circular distance).
        Returns number of segments distance.
        """
        # Normalized distance within region
        if region_start <= point_idx <= region_end:
            return 0.0

        # Distance going forward
        dist_forward = (point_idx - region_end) % total_points
        # Distance going backward
        dist_backward = (region_start - point_idx) % total_points

        return min(dist_forward, dist_backward)

    def get_amplitude_factor(self, curvature_reduction: float) -> float:
        """
        Get amplitude multiplication factor based on curvature.
        """
        return 1.0 - (self.amplitude_reduction / 100.0) * curvature_reduction

    def get_frequency_factor(self, curvature_reduction: float) -> float:
        """
        Get frequency multiplication factor based on curvature.
        """
        return 1.0 - (self.frequency_reduction / 100.0) * curvature_reduction


class DiameterScaling:
    """Manages wave count scaling as layer diameter changes."""

    def __init__(
        self,
        scaling_type: str = "dynamic",
        base_diameter: float = 90.0,
        base_wave_count: int = 24
    ):
        """
        Initialize diameter scaling.

        Args:
            scaling_type: 'constant_wavelength' or 'dynamic'
            base_diameter: Reference diameter (mm)
            base_wave_count: Wave count at base diameter
        """
        self.scaling_type = scaling_type.lower()
        self.base_diameter = base_diameter
        self.base_wave_count = base_wave_count

        if self.scaling_type not in ["constant_wavelength", "dynamic"]:
            self.scaling_type = "dynamic"

        logger.info(
            f"Diameter scaling: type={self.scaling_type}, "
            f"base_diameter={base_diameter}mm"
        )

    def calculate_wave_count(self, diameter: float) -> int:
        """
        Calculate optimal wave count for given diameter.

        Args:
            diameter: Current layer diameter in mm

        Returns:
            Number of waves to apply
        """
        if self.scaling_type == "constant_wavelength":
            # Keep wavelength constant (4mm by default)
            # More/fewer waves on larger/smaller diameters
            circumference = math.pi * diameter
            wave_spacing = 4.0  # mm
            return max(3, int(circumference / wave_spacing))

        elif self.scaling_type == "dynamic":
            # Logarithmic scaling for smooth visual progression
            if diameter <= 0.1:
                return self.base_wave_count

            ratio = diameter / self.base_diameter
            scaled_count = self.base_wave_count * math.log(ratio + 1.0) + self.base_wave_count

            return max(3, int(scaled_count))

        return self.base_wave_count

    def calculate_amplitude_adjustment(self, diameter: float) -> float:
        """
        Calculate amplitude adjustment based on diameter change.
        Maintains visual consistency across layers.

        Args:
            diameter: Current layer diameter in mm

        Returns:
            Amplitude multiplier (0.5-1.5 typical range)
        """
        if self.base_diameter <= 0.1:
            return 1.0

        ratio = diameter / self.base_diameter

        # Logarithmic adjustment keeps proportions consistent
        return math.log(ratio + 1.0) / math.log(2.0)


class AdaptiveWaveBehavior:
    """Combined adaptive behavior controller."""

    def __init__(
        self,
        curvature_adaptation: CurvatureAdaptation,
        diameter_scaling: DiameterScaling
    ):
        """
        Initialize combined adaptive behavior.

        Args:
            curvature_adaptation: CurvatureAdaptation instance
            diameter_scaling: DiameterScaling instance
        """
        self.curvature = curvature_adaptation
        self.diameter = diameter_scaling

    def calculate_adjustments(
        self,
        perimeter_points: List[Vector3],
        diameter: float
    ) -> Tuple[float, float]:
        """
        Calculate final amplitude and frequency adjustments.

        Args:
            perimeter_points: Layer perimeter points
            diameter: Layer diameter

        Returns:
            (amplitude_factor, frequency_factor) tuple
        """
        # Analyze curvature
        curvature_regions = self.curvature.analyze_curvature_regions(perimeter_points)

        # Average reduction across layer
        avg_reduction = sum(factor for _, factor in curvature_regions) / len(curvature_regions) \
            if curvature_regions else 1.0

        # Apply diameter scaling
        diameter_adjustment = self.diameter.calculate_amplitude_adjustment(diameter)

        # Combine adjustments
        amplitude_factor = avg_reduction * diameter_adjustment
        frequency_factor = avg_reduction  # Frequency mostly affected by curvature

        return amplitude_factor, frequency_factor
