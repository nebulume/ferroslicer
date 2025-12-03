'''
Base integrity system for dimensional accuracy and transitions.
'''

import math
from enum import Enum
from typing import List
from .logger import setup_logger

logger = setup_logger("base_integrity")


class BaseMode(Enum):
    """Base integrity modes."""
    TIGHTER_WAVES = "tighter_waves"
    FEWER_GAPS = "fewer_gaps"  # Default
    SOLID_THEN_MESH = "solid_then_mesh"


class TransitionProfile(Enum):
    """Transition profiles for base to mesh gradient."""
    LINEAR = "linear"
    EXPONENTIAL = "exponential"  # Default
    STEP = "step"


class BaseIntegrityManager:
    """Manages base transitions and dimensional accuracy."""

    def __init__(
        self,
        base_height: float = 28.0,
        mode: str = "fewer_gaps",
        transition: str = "exponential"
    ):
        """
        Initialize base integrity manager.

        Args:
            base_height: Height in mm where special handling applies
            mode: 'tighter_waves', 'fewer_gaps', or 'solid_then_mesh'
            transition: 'linear', 'exponential', or 'step'
        """
        self.base_height = base_height

        # Parse mode
        try:
            self.mode = BaseMode[mode.upper().replace(' ', '_')]
        except KeyError:
            self.mode = BaseMode.FEWER_GAPS

        # Parse transition
        try:
            self.transition = TransitionProfile[transition.upper()]
        except KeyError:
            self.transition = TransitionProfile.EXPONENTIAL

        logger.info(
            f"Base integrity: height={base_height}mm, "
            f"mode={self.mode.value}, transition={self.transition.value}"
        )

    def get_amplitude_factor(self, z: float) -> float:
        """
        Get amplitude multiplier for given Z height.
        Returns value from 0.0 (no waves) to 1.0 (full amplitude).
        """
        if z > self.base_height:
            return 1.0

        # We're in base region
        if self.mode == BaseMode.FEWER_GAPS:
            # Gradual transition from 0 to full amplitude
            return self._calculate_transition(z / self.base_height)

        elif self.mode == BaseMode.TIGHTER_WAVES:
            # In base: tighter waves (handled by wave generator adjustments)
            return self._calculate_transition(z / self.base_height)

        elif self.mode == BaseMode.SOLID_THEN_MESH:
            # Solid for half base height, then transition
            solid_height = self.base_height / 2.0
            if z < solid_height:
                return 0.0
            return self._calculate_transition((z - solid_height) / solid_height)

        return 1.0

    def _calculate_transition(self, normalized_height: float) -> float:
        """
        Calculate transition factor (0-1) for given normalized height (0-1).

        Args:
            normalized_height: Z position normalized to 0-1 in base region

        Returns:
            Amplitude factor 0.0-1.0
        """
        normalized_height = max(0.0, min(1.0, normalized_height))

        if self.transition == TransitionProfile.LINEAR:
            return normalized_height

        elif self.transition == TransitionProfile.EXPONENTIAL:
            # Quadratic curve for smooth transition
            return normalized_height ** 2.0

        elif self.transition == TransitionProfile.STEP:
            # Step-based increases every 5mm
            # Assuming base_height is known context
            steps = max(1, int(normalized_height * 5))
            return min(1.0, (steps / 5.0) * 0.2)

        return normalized_height

    def get_frequency_adjustment(self) -> float:
        """
        Get wave frequency adjustment for base region.
        Used in tighter_waves mode.
        Returns multiplier for wave frequency.
        """
        if self.mode == BaseMode.TIGHTER_WAVES:
            return 1.5  # 150% increase
        return 1.0

    def get_amplitude_adjustment(self) -> float:
        """
        Get amplitude adjustment for base region.
        Used in tighter_waves mode.
        Returns multiplier for amplitude.
        """
        if self.mode == BaseMode.TIGHTER_WAVES:
            return 0.4  # 60% reduction (1 - 0.6)
        return 1.0


class BaseTransitionAnalyzer:
    """Analyzes model and determines optimal base settings."""

    @staticmethod
    def recommend_base_height(model_height: float) -> float:
        """
        Recommend base height based on model height.
        Typical is 28mm for lamp bases.
        """
        if model_height < 50:
            return min(model_height / 2, 20)
        return 28.0

    @staticmethod
    def analyze_base_geometry(
        layers: List,  # List of Layer objects from geometry_analyzer
        base_height: float
    ) -> dict:
        """
        Analyze geometry in base region.
        Returns statistics about base geometry.
        """
        base_layers = [l for l in layers if l.z <= base_height]

        if not base_layers:
            return {
                "base_layers": 0,
                "avg_diameter": 0,
                "diameter_change": 0,
                "suitable_for_solid": False
            }

        diameters = [l.diameter for l in base_layers]

        return {
            "base_layers": len(base_layers),
            "avg_diameter": sum(diameters) / len(diameters),
            "min_diameter": min(diameters),
            "max_diameter": max(diameters),
            "diameter_change": max(diameters) - min(diameters),
            "suitable_for_solid": max(diameters) - min(diameters) < 5.0
        }
