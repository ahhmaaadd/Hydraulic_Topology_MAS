"""Solve one phase to its steady operating point.

The only discreteness left after the topology stage is *pressure-limiting
complementarity*: whether the relief valve is cracked or shut. Both regimes are
tried, each is solved exactly, and each is checked against its own consistency
condition. Because the enumeration is exhaustive the answer is certified rather
than assumed, and the two degenerate outcomes are informative in themselves:

* no consistent regime  -> the sizing cannot deliver this phase, with a reason;
* two consistent regimes -> the circuit has more than one stable operating point,
  which is a defect worth reporting even though every individual check passes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .network import PhaseModel
from .quantities import Q


@dataclass
class OperatingPoint:
    phase_id: str
    regime: str
    velocity_m_s: float
    supply_pressure_pa: float
    exhaust_pressure_pa: float
    pump_pressure_pa: float
    supply_flow_m3s: float
    exhaust_flow_m3s: float
    relief_flow_m3s: float
    feasible: bool = True
    reason: str = ""

    @property
    def velocity(self) -> Q:
        return Q(self.velocity_m_s, (1, 0, -1))

    @property
    def pump_pressure(self) -> Q:
        return Q(self.pump_pressure_pa, (-1, 1, -2))

    @property
    def supply_pressure(self) -> Q:
        return Q(self.supply_pressure_pa, (-1, 1, -2))


def _path_drop(elements, flow: float) -> float:
    return sum(element.pressure_drop(flow) for element in elements)


def _pressure_floor(elements) -> float:
    """Pressure a ``pressure_setting`` element pins downstream, if any."""
    for element in elements:
        if element.kind == "pressure_setting":
            return element.set_pressure_pa
    return 0.0


def _fixed_flow(elements) -> float | None:
    for element in elements:
        if element.kind == "flow_setting" and element.set_flow_m3s > 0:
            return element.set_flow_m3s
    return None


def solve_phase(model: PhaseModel, *, tolerance: float = 1e-9) -> OperatingPoint:
    """Return the certified operating point for this phase."""
    supply_area = model.supply_area.value
    exhaust_area = model.exhaust_area.value
    if supply_area <= 0 or exhaust_area <= 0:
        return OperatingPoint(model.phase_id, "invalid", 0, 0, 0, 0, 0, 0, 0,
                              feasible=False, reason="actuator areas are not sized")

    load = model.load.value / max(model.mechanical_efficiency, 1e-6)
    pump_flow = model.pump_flow.value
    relief = model.relief_pressure.value

    def pump_pressure_for(velocity: float) -> tuple[float, float, float]:
        """(p_pump, p_supply, p_exhaust) required to move at ``velocity``."""
        supply_flow = velocity * supply_area
        exhaust_flow = velocity * exhaust_area
        p_exhaust = _path_drop(model.exhaust_elements, exhaust_flow)
        p_supply = (load + p_exhaust * exhaust_area) / supply_area
        p_pump = p_supply + _path_drop(model.supply_elements, supply_flow)
        floor = _pressure_floor(model.supply_elements)
        if floor:
            # A reducing valve pins the branch; upstream must still reach it.
            p_pump = max(p_pump, floor)
        return p_pump, p_supply, p_exhaust

    candidates: list[OperatingPoint] = []

    # A compensated control fixes the flow outright; no regime search needed.
    metered_flow = _fixed_flow(model.exhaust_elements)
    if metered_flow is not None:
        velocity = metered_flow / exhaust_area
        p_pump, p_supply, p_exhaust = pump_pressure_for(velocity)
        supply_flow = velocity * supply_area
        candidates.append(
            OperatingPoint(
                model.phase_id, "flow_set", velocity, p_supply, p_exhaust,
                min(p_pump, relief) if relief else p_pump,
                supply_flow, velocity * exhaust_area,
                max(pump_flow - supply_flow, 0.0),
                feasible=supply_flow <= pump_flow + tolerance
                and (not relief or p_pump <= relief + 1.0),
                reason="" if supply_flow <= pump_flow + tolerance
                else "the compensated control demands more than the pump delivers",
            )
        )
        return candidates[0]

    # Regime A - the actuator takes the whole delivery and the relief stays shut.
    if pump_flow > 0:
        velocity = pump_flow / supply_area
        p_pump, p_supply, p_exhaust = pump_pressure_for(velocity)
        if not relief or p_pump <= relief + tolerance:
            candidates.append(
                OperatingPoint(
                    model.phase_id, "flow_limited", velocity, p_supply, p_exhaust, p_pump,
                    pump_flow, velocity * exhaust_area, 0.0,
                )
            )

    # Regime B - the relief is cracked, so the pump line sits at its setting and
    # the metering element decides how fast the actuator moves.
    if relief > 0 and pump_flow > 0:
        def residual(velocity: float) -> float:
            _, p_supply, _ = pump_pressure_for(velocity)
            supply_flow = velocity * supply_area
            return relief - _path_drop(model.supply_elements, supply_flow) - p_supply

        low, high = 0.0, pump_flow / supply_area
        if residual(low) >= 0.0 >= residual(high) or residual(low) * residual(high) < 0:
            for _ in range(200):
                mid = 0.5 * (low + high)
                if residual(mid) > 0:
                    low = mid
                else:
                    high = mid
            velocity = 0.5 * (low + high)
            p_pump, p_supply, p_exhaust = pump_pressure_for(velocity)
            supply_flow = velocity * supply_area
            if supply_flow <= pump_flow + tolerance:
                candidates.append(
                    OperatingPoint(
                        model.phase_id, "pressure_limited", velocity, p_supply, p_exhaust,
                        relief, supply_flow, velocity * exhaust_area,
                        max(pump_flow - supply_flow, 0.0),
                    )
                )

    if not candidates:
        p_pump, _, _ = pump_pressure_for(pump_flow / supply_area if pump_flow else 0.0)
        return OperatingPoint(
            model.phase_id, "infeasible", 0, 0, 0, p_pump, 0, 0, 0,
            feasible=False,
            reason=(
                f"no consistent regime: moving this load needs {p_pump / 1e5:.2f} bar at the pump "
                f"but the relief is set to {relief / 1e5:.2f} bar"
            ),
        )
    if len(candidates) > 1:
        chosen = candidates[0]
        chosen.reason = (
            "more than one steady operating point is consistent; the circuit can settle "
            "into either, which is a design ambiguity"
        )
        return chosen
    return candidates[0]
