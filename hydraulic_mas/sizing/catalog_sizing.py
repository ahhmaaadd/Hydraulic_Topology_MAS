"""Parametric size series layered over the generic topology catalog.

The topology catalog deliberately carries no dimensions: it answers "what kind of
valve", never "how big". This module answers "how big", and keeps the same
vendor-neutral boundary - standard *series* and preferred numbers, not part
numbers. A design expressed in these terms is procurable without being tied to a
manufacturer, which is the right level for a benchmark and for a paper.

Series used
-----------
* cylinder bores   ISO 3320 preferred bores
* piston rods      ISO 4395 preferred rod diameters
* pump displacement, valve nominal sizes, tube ODs: standard commercial series
* pressure classes: the common 160 / 250 / 315 / 420 bar families
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .quantities import Q, annulus_area, area_of_bore


# ISO 3320 preferred cylinder bores, mm
BORE_SERIES: tuple[float, ...] = (
    25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 320,
)

# ISO 4395 preferred piston rod diameters, mm
ROD_SERIES: tuple[float, ...] = (
    12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 50, 56, 63, 70, 80, 90, 100, 110, 125, 140,
)

# Commercially common fixed-displacement pump sizes, cm3/rev
DISPLACEMENT_SERIES: tuple[float, ...] = (
    1.6, 2.5, 4.0, 5.5, 8.0, 11.0, 14.0, 16.0, 19.0, 22.0, 25.0, 28.0,
    32.0, 40.0, 45.0, 50.0, 63.0, 71.0, 80.0, 100.0, 125.0,
)

# Valve nominal sizes and their rated flow at the usual reference drop, L/min
VALVE_SIZES: tuple[tuple[str, float], ...] = (
    ("NG4", 20.0), ("NG6", 60.0), ("NG10", 120.0), ("NG16", 300.0), ("NG25", 600.0),
)

# Standard hydraulic tube: outside diameter mm -> available wall thicknesses mm
TUBE_SERIES: dict[float, tuple[float, ...]] = {
    6.0: (1.0, 1.5), 8.0: (1.0, 1.5, 2.0), 10.0: (1.0, 1.5, 2.0),
    12.0: (1.5, 2.0, 2.5), 15.0: (1.5, 2.0, 2.5), 16.0: (1.5, 2.0, 2.5),
    18.0: (1.5, 2.0, 2.5, 3.0), 20.0: (2.0, 2.5, 3.0), 22.0: (2.0, 2.5, 3.0),
    25.0: (2.0, 2.5, 3.0, 4.0), 28.0: (2.0, 2.5, 3.0, 4.0), 30.0: (2.5, 3.0, 4.0),
    35.0: (2.5, 3.0, 4.0, 5.0), 38.0: (3.0, 4.0, 5.0), 42.0: (3.0, 4.0, 5.0),
    # Suction runs at 1 m/s need far larger bores than pressure lines at 4 m/s,
    # so the series has to reach well past the pressure-side sizes.
    48.0: (3.0, 4.0, 5.0), 60.0: (3.0, 4.0, 5.0), 76.0: (3.0, 4.0, 5.0),
    89.0: (4.0, 5.0), 102.0: (4.0, 5.0),
}

# IEC standard three-phase motor ratings, kW
MOTOR_SERIES: tuple[float, ...] = (
    0.25, 0.37, 0.55, 0.75, 1.1, 1.5, 2.2, 3.0, 4.0, 5.5, 7.5, 11.0, 15.0, 18.5, 22.0, 30.0,
)

# Reservoir nominal sizes, L
RESERVOIR_SERIES: tuple[float, ...] = (
    10, 16, 25, 40, 60, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630,
)

PRESSURE_CLASSES: tuple[float, ...] = (100.0, 160.0, 250.0, 315.0, 420.0)


def _select_at_least(value: float, series: tuple[float, ...], name: str) -> float:
    for candidate in series:
        if candidate >= value - 1e-9:
            return candidate
    raise NoStandardSizeError(
        f"no standard {name} reaches {value:g}; largest available is {series[-1]:g}"
    )


class NoStandardSizeError(ValueError):
    """A required size exceeds every standard option.

    Mirrors the topology stage's CatalogGap contract: a genuine absence is a
    reportable design outcome, not a crash.
    """


def select_bore(required_area: Q) -> float:
    """Smallest ISO bore whose cap area covers ``required_area``."""
    target = required_area.to("mm2")
    for bore in BORE_SERIES:
        if area_of_bore(bore).to("mm2") >= target - 1e-9:
            return bore
    raise NoStandardSizeError(f"no standard bore provides {target:.0f} mm2 of cap area")


def select_rod(bore_mm: float, *, min_ratio: float = 0.4, max_ratio: float = 0.75) -> float:
    """A rod in the usual proportion band for the chosen bore.

    Below about 0.4 the rod is slender and buckling-limited; above about 0.75 the
    annulus becomes so small that retraction speed and return pressure suffer.
    The mid-band choice keeps the area ratio near 1.4-1.6, which is the standard
    general-purpose proportion.
    """
    candidates = [
        rod for rod in ROD_SERIES if min_ratio * bore_mm <= rod <= max_ratio * bore_mm
    ]
    if not candidates:
        raise NoStandardSizeError(f"no standard rod suits a {bore_mm:g} mm bore")
    target = 0.55 * bore_mm
    return min(candidates, key=lambda rod: abs(rod - target))


def select_rod_for_ratio(bore_mm: float, target_ratio: float) -> float:
    """Rod giving a cap:annulus area ratio closest to ``target_ratio``.

    Needed for load-actuated meter-out circuits, where the achievable speed ratio
    is governed by the area ratio rather than by force.
    """
    best, best_error = None, float("inf")
    for rod in ROD_SERIES:
        if rod >= bore_mm:
            continue
        ratio = area_of_bore(bore_mm).value / annulus_area(bore_mm, rod).value
        error = abs(ratio - target_ratio)
        if error < best_error:
            best, best_error = rod, error
    if best is None:
        raise NoStandardSizeError(f"no standard rod suits a {bore_mm:g} mm bore")
    return best


def select_displacement(flow: Q, speed_rpm: float, volumetric_efficiency: float) -> float:
    """Displacement in cm3/rev delivering at least ``flow`` at the given speed."""
    required = flow.to("l/min") * 1000.0 / (speed_rpm * volumetric_efficiency)
    return _select_at_least(required, DISPLACEMENT_SERIES, "pump displacement (cm3/rev)")


def select_valve_size(flow: Q, margin: float = 1.25) -> tuple[str, float]:
    """Smallest valve whose rated flow covers ``margin`` times the actual flow."""
    required = flow.to("l/min") * margin
    for name, rated in VALVE_SIZES:
        if rated >= required - 1e-9:
            return name, rated
    raise NoStandardSizeError(f"no standard valve size passes {required:.1f} L/min")


def select_tube(flow: Q, target_velocity_m_s: float) -> tuple[float, float, float]:
    """Return ``(outside_diameter, wall, inside_diameter)`` in mm.

    Picks the smallest standard tube whose bore keeps the velocity at or below
    the target, which is how the suction / pressure / return velocity rules in
    the design basis become concrete hardware.
    """
    import math

    required_area = flow.value / target_velocity_m_s          # m2
    required_id = math.sqrt(4.0 * required_area / math.pi) * 1e3
    best = None
    for outside in sorted(TUBE_SERIES):
        for wall in sorted(TUBE_SERIES[outside]):
            inside = outside - 2.0 * wall
            if inside >= required_id - 1e-9:
                if best is None or inside < best[2]:
                    best = (outside, wall, inside)
        if best is not None:
            return best
    raise NoStandardSizeError(f"no standard tube provides a {required_id:.1f} mm bore")


def select_motor(shaft_power: Q) -> float:
    return _select_at_least(shaft_power.to("kw"), MOTOR_SERIES, "motor rating (kW)")


def select_reservoir(volume_litres: float) -> float:
    return _select_at_least(volume_litres, RESERVOIR_SERIES, "reservoir size (L)")


def select_pressure_class(working_pressure: Q, margin: float = 1.25) -> float:
    return _select_at_least(
        working_pressure.to("bar") * margin, PRESSURE_CLASSES, "pressure class (bar)"
    )


@dataclass(frozen=True)
class CylinderSize:
    bore_mm: float
    rod_mm: float
    stroke_mm: float
    pressure_class_bar: float

    @property
    def cap_area(self) -> Q:
        return area_of_bore(self.bore_mm)

    @property
    def annulus_area(self) -> Q:
        return annulus_area(self.bore_mm, self.rod_mm)

    @property
    def area_ratio(self) -> float:
        return self.cap_area.value / self.annulus_area.value

    def describe(self) -> str:
        return f"{self.bore_mm:g}/{self.rod_mm:g} mm, {self.stroke_mm:g} mm stroke"


@dataclass
class SizedComponent:
    """The sizing record attached to one generic topology component."""

    component_id: str
    catalog_key: str
    comp_type: str
    parameters: dict[str, float | str] = field(default_factory=dict)
    ratings: dict[str, float] = field(default_factory=dict)
    basis: str = ""

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "catalog_key": self.catalog_key,
            "comp_type": self.comp_type,
            "parameters": dict(self.parameters),
            "ratings": dict(self.ratings),
            "basis": self.basis,
        }
