"""Turn a design the system produced back into a stated one.

Two uses, both about trusting the harness rather than about the experiment.

The first is a faithfulness check. Feeding a certified design through the direct
path must reproduce its certificate. If it does not, the direct path is adding or
losing something and any difference measured between arms would partly be that
artefact rather than the arms.

The second is a source of controlled wrong answers. Perturbing an oracle by one
bore size, or leaving its numbers right and its predictions wrong, produces a
design whose failure mode is known in advance - which is the only way to show the
metrics detect what they claim to detect.
"""

from __future__ import annotations

from typing import Any

from ..sizing.catalog_sizing import BORE_SERIES, VALVE_SIZES
from ..sizing.quantities import Q
from .schemas import DirectCylinder, DirectFlowControl, DirectSetting, DirectSizing, PhasePrediction


def _phase_predictions(certificate: dict[str, Any]) -> list[PhasePrediction]:
    """The truth, read off the certificate, as though the arm had got it right."""
    by_phase: dict[str, PhasePrediction] = {}
    for item in certificate.get("criteria", []) or []:
        phase_id = item.get("phase_id")
        achieved = str(item.get("achieved") or "")
        if not phase_id or "[" not in achieved:
            continue
        try:
            body = achieved[achieved.index("[") + 1:achieved.index("]")]
            low, high = (float(part) for part in body.split(","))
        except (ValueError, IndexError):
            continue
        entry = by_phase.setdefault(str(phase_id), PhasePrediction(phase_id=str(phase_id)))
        middle = 0.5 * (low + high)
        if item.get("kind") == "velocity":
            entry.predicted_velocity_m_min = middle
        elif item.get("kind") == "force":
            entry.predicted_force_kn = middle
    for phase_id, point in (certificate.get("operating_points") or {}).items():
        entry = by_phase.setdefault(str(phase_id), PhasePrediction(phase_id=str(phase_id)))
        if isinstance(point.get("pump_pressure_bar"), (int, float)):
            entry.predicted_pressure_bar = float(point["pump_pressure_bar"])
    return list(by_phase.values())


def direct_from_sizing(sizing: dict[str, Any], topology: dict[str, Any],
                       throttle_k: dict[str, float],
                       certificate: dict[str, Any] | None = None,
                       requirements: dict[str, Any] | None = None) -> DirectSizing:
    supply = sizing.get("__supply__", {})
    cylinders = [
        DirectCylinder(cylinder_id=component_id,
                       bore_mm=float(record["bore_mm"]), rod_mm=float(record["rod_mm"]))
        for component_id, record in sizing.items()
        if isinstance(record, dict) and "bore_mm" in record
    ]

    settings = [
        DirectSetting(component_id=component_id,
                      setting_bar=float(record["setting_pa"]) / 1e5)
        for component_id, record in sizing.items()
        if isinstance(record, dict) and record.get("setting_pa")
    ]

    flow_controls: list[DirectFlowControl] = []
    metered_phase = _first_metered_phase(requirements or {})
    for component in topology.get("components", []):
        component_id = str(component["id"])
        comp_type = component.get("comp_type")
        record = sizing.get(component_id, {})
        if comp_type == "pressure_comp_flow_control" and record.get("set_flow_m3s"):
            flow_controls.append(DirectFlowControl(
                component_id=component_id,
                phase_id=metered_phase or "",
                set_flow_lpm=Q(float(record["set_flow_m3s"]), (3, 0, -1)).to("l/min")))
        elif comp_type == "one_way_flow_control" and component_id in throttle_k:
            flow_controls.append(DirectFlowControl(
                component_id=component_id,
                phase_id=metered_phase or "",
                set_flow_lpm=_flow_through(
                    component_id, metered_phase, sizing, throttle_k,
                    topology, requirements or {})))

    valve_size = str(supply.get("valve_size") or VALVE_SIZES[1][0])
    return DirectSizing(
        cylinders=cylinders,
        displacement_cm3=float(supply.get("displacement_cm3", 0.0)),
        pump_speed_rpm=float(supply.get("speed_rpm", 1500.0)),
        relief_bar=float(supply.get("relief_pa", 0.0)) / 1e5,
        motor_kw=float(supply.get("motor_kw", 0.0)),
        reservoir_l=float(supply.get("reservoir_l", 0.0)),
        valve_size=valve_size,
        flow_controls=flow_controls,
        settings=settings,
        predictions=_phase_predictions(certificate or {}),
        reasoning="reconstructed from a certified design",
    )


def _first_metered_phase(requirements: dict[str, Any]) -> str | None:
    for function in requirements.get("functions", []) or []:
        for phase in function.get("motion_phases") or []:
            if str(phase.get("metering_side")) in {"meter_in", "meter_out"}:
                return str(phase.get("id"))
    return None


def _flow_through(component_id: str, phase_id: str | None, sizing: dict[str, Any],
                  throttle_k: dict[str, float], topology: dict[str, Any],
                  requirements: dict[str, Any]) -> float:
    """Flow the throttle passes in the phase it is set for, in L/min.

    Solved from the design rather than read off the coefficient, because the
    coefficient is exactly what the direct path has to reconstruct. Handing it
    across would make the round trip pass by smuggling instead of by agreeing.
    """
    from ..sizing.network import build_phase_model
    from ..sizing.solve import solve_phase

    if not phase_id:
        return 0.0
    model = build_phase_model(topology, requirements, phase_id, sizing, throttle_k=throttle_k)
    if model is None:
        return 0.0
    point = solve_phase(model)
    if not point.feasible:
        return 0.0
    metered_on_exhaust = any(
        element.component_id == component_id for element in model.exhaust_elements)
    area = model.exhaust_area.value if metered_on_exhaust else model.supply_area.value
    return Q(point.velocity_m_s * area, (3, 0, -1)).to("l/min")


def perturb_bore(proposal: DirectSizing, steps: int = -1) -> DirectSizing:
    """Move every bore ``steps`` places along the preferred series."""
    changed = proposal.model_copy(deep=True)
    for cylinder in changed.cylinders:
        try:
            index = BORE_SERIES.index(cylinder.bore_mm)
        except ValueError:
            continue
        cylinder.bore_mm = BORE_SERIES[max(0, min(len(BORE_SERIES) - 1, index + steps))]
    return changed


def mispredict(proposal: DirectSizing, factor: float = 1.25) -> DirectSizing:
    """Keep the design and make its stated arithmetic wrong by a fixed factor."""
    changed = proposal.model_copy(deep=True)
    for entry in changed.predictions:
        if entry.predicted_velocity_m_min is not None:
            entry.predicted_velocity_m_min *= factor
        if entry.predicted_force_kn is not None:
            entry.predicted_force_kn *= factor
        if entry.predicted_pressure_bar is not None:
            entry.predicted_pressure_bar *= factor
    return changed
