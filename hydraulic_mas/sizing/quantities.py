"""Unit-safe scalars for the sizing stage.

Every number that crosses a module boundary carries its dimension. The reference
audit that motivated this work found defects that are, at root, unit and
convention errors - hydraulic power reported as shaft power, force divided by area
without the mechanical efficiency, pressure quoted in kgf/cm2 next to bar. Those
are exactly the mistakes a dimension-checked representation makes structurally
impossible rather than merely unlikely.

Values are stored in SI internally and converted only at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


# dimension vector: (length, mass, time)
Dimension = tuple[int, int, int]

DIMENSIONLESS: Dimension = (0, 0, 0)
LENGTH: Dimension = (1, 0, 0)
AREA: Dimension = (2, 0, 0)
VOLUME: Dimension = (3, 0, 0)
TIME: Dimension = (0, 0, 1)
VELOCITY: Dimension = (1, 0, -1)
FORCE: Dimension = (1, 1, -2)
PRESSURE: Dimension = (-1, 1, -2)
FLOW: Dimension = (3, 0, -1)
POWER: Dimension = (2, 1, -3)


# unit -> (factor to SI, dimension)
_UNITS: dict[str, tuple[float, Dimension]] = {
    "": (1.0, DIMENSIONLESS),
    "-": (1.0, DIMENSIONLESS),
    "m": (1.0, LENGTH),
    "mm": (1e-3, LENGTH),
    "cm": (1e-2, LENGTH),
    "m2": (1.0, AREA),
    "mm2": (1e-6, AREA),
    "m3": (1.0, VOLUME),
    "l": (1e-3, VOLUME),
    "cm3": (1e-6, VOLUME),
    "s": (1.0, TIME),
    "min": (60.0, TIME),
    "m/s": (1.0, VELOCITY),
    "mm/s": (1e-3, VELOCITY),
    "m/min": (1.0 / 60.0, VELOCITY),
    "mm/min": (1e-3 / 60.0, VELOCITY),
    "n": (1.0, FORCE),
    "kn": (1e3, FORCE),
    "kgf": (9.80665, FORCE),
    "pa": (1.0, PRESSURE),
    "kpa": (1e3, PRESSURE),
    "mpa": (1e6, PRESSURE),
    "bar": (1e5, PRESSURE),
    "kgf/cm2": (9.80665e4, PRESSURE),
    "m3/s": (1.0, FLOW),
    "l/min": (1e-3 / 60.0, FLOW),
    "l/s": (1e-3, FLOW),
    "w": (1.0, POWER),
    "kw": (1e3, POWER),
}

_DIMENSION_NAMES = {
    DIMENSIONLESS: "dimensionless",
    LENGTH: "length",
    AREA: "area",
    VOLUME: "volume",
    TIME: "time",
    VELOCITY: "velocity",
    FORCE: "force",
    PRESSURE: "pressure",
    FLOW: "flow",
    POWER: "power",
}


class DimensionError(ValueError):
    """Raised when an operation would combine incompatible dimensions."""


def _normalize(unit: str) -> str:
    return str(unit or "").strip().casefold().replace("^", "").replace("³", "3").replace("²", "2")


def unit_info(unit: str) -> tuple[float, Dimension]:
    key = _normalize(unit)
    if key not in _UNITS:
        raise DimensionError(f"unknown unit {unit!r}")
    return _UNITS[key]


def dimension_name(dimension: Dimension) -> str:
    return _DIMENSION_NAMES.get(dimension, f"custom{dimension}")


@dataclass(frozen=True)
class Q:
    """A scalar in SI with a dimension, and an optional symmetric tolerance.

    ``tolerance`` is a *relative* half-width. ``Q.of(1.5, "m/min", tol=0.1)``
    means 1.5 m/min +/- 10 percent, which is how the briefs' "approximately"
    targets become checkable.
    """

    value: float
    dimension: Dimension
    tolerance: float = 0.0

    # -- construction ------------------------------------------------------
    @staticmethod
    def of(value: float, unit: str, tol: float = 0.0) -> "Q":
        factor, dimension = unit_info(unit)
        return Q(float(value) * factor, dimension, float(tol))

    @staticmethod
    def zero(dimension: Dimension) -> "Q":
        return Q(0.0, dimension)

    # -- conversion --------------------------------------------------------
    def to(self, unit: str) -> float:
        factor, dimension = unit_info(unit)
        if dimension != self.dimension:
            raise DimensionError(
                f"cannot express {dimension_name(self.dimension)} in {unit!r} "
                f"({dimension_name(dimension)})"
            )
        return self.value / factor

    def band(self) -> tuple[float, float]:
        """Absolute SI bounds implied by the relative tolerance."""
        spread = abs(self.value) * self.tolerance
        return self.value - spread, self.value + spread

    def with_tolerance(self, tolerance: float) -> "Q":
        return replace(self, tolerance=float(tolerance))

    # -- arithmetic --------------------------------------------------------
    def _require(self, other: "Q") -> None:
        if self.dimension != other.dimension:
            raise DimensionError(
                f"cannot combine {dimension_name(self.dimension)} with "
                f"{dimension_name(other.dimension)}"
            )

    def __add__(self, other: "Q") -> "Q":
        self._require(other)
        return Q(self.value + other.value, self.dimension)

    def __sub__(self, other: "Q") -> "Q":
        self._require(other)
        return Q(self.value - other.value, self.dimension)

    def __mul__(self, other: "Q | float | int") -> "Q":
        if isinstance(other, (int, float)):
            return Q(self.value * other, self.dimension)
        dimension = tuple(a + b for a, b in zip(self.dimension, other.dimension))
        return Q(self.value * other.value, dimension)  # type: ignore[arg-type]

    __rmul__ = __mul__

    def __truediv__(self, other: "Q | float | int") -> "Q":
        if isinstance(other, (int, float)):
            return Q(self.value / other, self.dimension)
        dimension = tuple(a - b for a, b in zip(self.dimension, other.dimension))
        return Q(self.value / other.value, dimension)  # type: ignore[arg-type]

    def __neg__(self) -> "Q":
        return Q(-self.value, self.dimension)

    # -- comparison --------------------------------------------------------
    def __lt__(self, other: "Q") -> bool:
        self._require(other)
        return self.value < other.value

    def __le__(self, other: "Q") -> bool:
        self._require(other)
        return self.value <= other.value

    def __gt__(self, other: "Q") -> bool:
        self._require(other)
        return self.value > other.value

    def __ge__(self, other: "Q") -> bool:
        self._require(other)
        return self.value >= other.value

    def __repr__(self) -> str:
        return f"Q({self.value:g} SI {dimension_name(self.dimension)})"


def from_requirement(quantity: dict | None, default_tolerance: float = 0.0) -> Q | None:
    """Read a RequirementsSpec Quantity record into a checked ``Q``.

    Returns ``None`` when the requirement did not state the value, which is a
    normal and frequent case - the caller decides whether that is a problem.
    """
    if not isinstance(quantity, dict):
        return None
    value = quantity.get("value")
    if not isinstance(value, (int, float)):
        return None
    tolerance = quantity.get("tolerance")
    if not isinstance(tolerance, (int, float)):
        tolerance = default_tolerance
    return Q.of(float(value), str(quantity.get("unit") or ""), float(tolerance))


def area_of_bore(bore_mm: float) -> Q:
    import math

    return Q(math.pi * (bore_mm * 1e-3) ** 2 / 4.0, AREA)


def annulus_area(bore_mm: float, rod_mm: float) -> Q:
    import math

    return Q(math.pi * ((bore_mm * 1e-3) ** 2 - (rod_mm * 1e-3) ** 2) / 4.0, AREA)


def total(values: Iterable[Q], dimension: Dimension) -> Q:
    result = Q.zero(dimension)
    for value in values:
        result = result + value
    return result
