"""Constitutive laws for the sizing stage, one per generic component class.

Each element on a flow path is exactly one of three kinds:

* **resistive** - imposes a pressure drop that depends on the flow through it;
* **flow-setting** - fixes the flow regardless of the drop (a compensated control);
* **pressure-setting** - fixes the downstream pressure (a reducing valve).

That taxonomy is what keeps the phase solve small. Everything else about a
component - how it switches, when it opens, which direction it passes - was
already decided and proved at the topology stage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .quantities import Q


ElementKind = Literal["resistive", "flow_setting", "pressure_setting"]

# Fluid properties for a typical ISO VG 46 mineral oil at operating temperature.
RHO = 870.0                # kg/m3
NU_NOMINAL = 46e-6         # m2/s at 40 C
BULK_MODULUS = 1.4e9       # Pa, used only by the decompression screen
DISCHARGE_COEFF = 0.62


@dataclass
class Element:
    """One component's contribution to a directed flow path."""

    component_id: str
    comp_type: str
    kind: ElementKind
    # resistive: pressure drop at the reference flow, used to scale a square law
    reference_drop_pa: float = 0.0
    reference_flow_m3s: float = 0.0
    # flow_setting
    set_flow_m3s: float = 0.0
    # pressure_setting
    set_pressure_pa: float = 0.0
    # throttle coefficient K where Q = K*sqrt(dp); derived, not given
    throttle_k: float | None = None

    def pressure_drop(self, flow_m3s: float) -> float:
        """Pressure drop in Pa carrying ``flow_m3s`` through this element."""
        if self.kind != "resistive":
            return 0.0
        flow = abs(flow_m3s)
        if self.throttle_k is not None:
            if self.throttle_k <= 0:
                return float("inf")
            # Q = K*sqrt(dp)  =>  dp = (Q/K)^2
            return (flow / self.throttle_k) ** 2
        if self.reference_flow_m3s <= 0:
            return 0.0
        # Standard valve practice: drop scales with the square of flow.
        return self.reference_drop_pa * (flow / self.reference_flow_m3s) ** 2

    def derivative(self, flow_m3s: float) -> float:
        """d(dp)/dQ, used by the Newton solve."""
        if self.kind != "resistive":
            return 0.0
        flow = abs(flow_m3s)
        if self.throttle_k is not None:
            if self.throttle_k <= 0:
                return float("inf")
            return 2.0 * flow / self.throttle_k**2
        if self.reference_flow_m3s <= 0:
            return 0.0
        return 2.0 * self.reference_drop_pa * flow / self.reference_flow_m3s**2


def throttle_k_for(flow: Q, drop: Q) -> float:
    """Throttle coefficient that passes ``flow`` at ``drop``: K = Q/sqrt(dp)."""
    if drop.value <= 0:
        raise ValueError("a throttle needs a positive pressure drop to pass flow")
    return flow.value / math.sqrt(drop.value)


def throttle_flow(k: float, drop_pa: float) -> float:
    return k * math.sqrt(max(drop_pa, 0.0))


def valve_element(component_id: str, comp_type: str, rated_flow_lpm: float,
                  reference_drop_bar: float = 3.0) -> Element:
    """A spool or poppet valve as a square-law resistance.

    Catalogue valves are quoted as a pressure drop at a rated flow; between those
    points the drop follows the flow squared closely enough for sizing.
    """
    return Element(
        component_id=component_id,
        comp_type=comp_type,
        kind="resistive",
        reference_drop_pa=reference_drop_bar * 1e5,
        reference_flow_m3s=rated_flow_lpm * 1e-3 / 60.0,
    )


def line_element(component_id: str, inside_diameter_mm: float, length_m: float,
                 nu_m2s: float = NU_NOMINAL) -> Element:
    """A run of tube, linearised about its own operating point by Darcy-Weisbach.

    Laminar below Re 2300 (which is the usual regime at sizing velocities) and
    Blasius turbulent above it.
    """
    diameter = inside_diameter_mm * 1e-3
    area = math.pi * diameter**2 / 4.0

    def drop_at(flow: float) -> float:
        if flow <= 0 or diameter <= 0:
            return 0.0
        velocity = flow / area
        reynolds = velocity * diameter / nu_m2s
        if reynolds < 2300.0:
            friction = 64.0 / max(reynolds, 1e-6)
        else:
            friction = 0.3164 / reynolds**0.25
        return friction * (length_m / diameter) * RHO * velocity**2 / 2.0

    reference_flow = area * 4.0          # at a 4 m/s reference velocity
    return Element(
        component_id=component_id,
        comp_type="line",
        kind="resistive",
        reference_drop_pa=drop_at(reference_flow),
        reference_flow_m3s=reference_flow,
    )


def check_element(component_id: str, comp_type: str, cracking_bar: float = 0.35,
                  rated_flow_lpm: float = 60.0) -> Element:
    element = valve_element(component_id, comp_type, rated_flow_lpm, reference_drop_bar=1.5)
    element.reference_drop_pa += cracking_bar * 1e5
    return element


def pump_delivery(displacement_cm3: float, speed_rpm: float, volumetric_efficiency: float) -> Q:
    """Delivered flow of a fixed pump."""
    return Q.of(displacement_cm3 * speed_rpm * volumetric_efficiency / 1000.0, "l/min")


def shaft_power(flow: Q, pressure: Q, overall_efficiency: float) -> Q:
    """Shaft power the prime mover must supply.

    The reference audit found two designs whose prime mover was quoted at the
    *hydraulic* power. The efficiency division is the whole difference, and it is
    the reason this returns shaft power and is named accordingly.
    """
    return Q(flow.value * pressure.value / overall_efficiency, (2, 1, -3))
