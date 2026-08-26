"""Install a stated sizing exactly as stated, then certify it.

The one rule here is that nothing is corrected. If the model names a bore that is
not on the preferred series, it is used anyway and recorded as a catalog
violation; if the relief sits above the system ceiling, it is installed and the
verifier refutes it. Silently rounding a number to the nearest legal value would
repair precisely the errors the ablation exists to count.

Two conversions are not corrections and are applied to every arm alike: areas
follow from the bore and rod by geometry, and a flow control stated in L/min is
turned into an orifice coefficient by solving the phase it acts on. Both are
physics, identical across arms; neither is a design decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..sizing.catalog_sizing import (
    BORE_SERIES,
    DISPLACEMENT_SERIES,
    MOTOR_SERIES,
    ROD_SERIES,
    VALVE_SIZES,
)
from ..sizing.certify import SizingCertificate, certify
from ..sizing.contract import AcceptanceContract
from ..sizing.intervals import standard_envelope
from ..sizing.network import build_phase_model
from ..sizing.quantities import Q, annulus_area, area_of_bore
from ..sizing.solve import solve_phase
from .schemas import DirectSizing


ETA_V, ETA_O = 0.90, 0.90


@dataclass
class DirectResult:
    sizing: dict[str, Any] = field(default_factory=dict)
    throttle_k: dict[str, float] = field(default_factory=dict)
    certificate: SizingCertificate | None = None
    catalog_violations: list[str] = field(default_factory=list)
    install_errors: list[str] = field(default_factory=list)


def _off_series(value: float, series: tuple[float, ...], label: str,
                violations: list[str], tolerance: float = 1e-6) -> None:
    if not any(abs(value - item) <= tolerance for item in series):
        violations.append(f"{label} {value:g} is not on the standard series")


def apply_direct(
    proposal: DirectSizing,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    load_tolerance: float = 0.0,
) -> DirectResult:
    result = DirectResult()
    sizing: dict[str, Any] = {}

    cylinder_ids = {
        str(component["id"])
        for component in topology.get("components", [])
        if component.get("comp_type") == "cylinder"
    }
    stated = {item.cylinder_id: item for item in proposal.cylinders}
    for cylinder_id in sorted(cylinder_ids):
        entry = stated.get(cylinder_id)
        if entry is None:
            result.install_errors.append(f"no size given for cylinder {cylinder_id}")
            continue
        if entry.rod_mm >= entry.bore_mm:
            result.install_errors.append(
                f"{cylinder_id}: rod {entry.rod_mm:g} mm is not smaller than bore {entry.bore_mm:g} mm")
            continue
        _off_series(entry.bore_mm, BORE_SERIES, f"{cylinder_id} bore", result.catalog_violations)
        _off_series(entry.rod_mm, ROD_SERIES, f"{cylinder_id} rod", result.catalog_violations)
        sizing[cylinder_id] = {
            "cap_area_m2": area_of_bore(entry.bore_mm).value,
            "annulus_area_m2": annulus_area(entry.bore_mm, entry.rod_mm).value,
            "bore_mm": entry.bore_mm,
            "rod_mm": entry.rod_mm,
        }
    if result.install_errors:
        return result

    _off_series(proposal.displacement_cm3, DISPLACEMENT_SERIES, "displacement",
                result.catalog_violations)
    _off_series(proposal.motor_kw, MOTOR_SERIES, "motor rating", result.catalog_violations)
    rated = dict(VALVE_SIZES).get(proposal.valve_size)
    if rated is None:
        result.catalog_violations.append(f"valve size {proposal.valve_size!r} is not a nominal size")
        rated = 60.0

    flow_lpm = proposal.displacement_cm3 * proposal.pump_speed_rpm * ETA_V / 1000.0
    sizing["__supply__"] = {
        "displacement_cm3": proposal.displacement_cm3,
        "speed_rpm": proposal.pump_speed_rpm,
        "flow_m3s": Q.of(flow_lpm, "l/min").value,
        "relief_pa": Q.of(proposal.relief_bar, "bar").value,
        "overall_efficiency": ETA_O,
        "motor_kw": proposal.motor_kw,
        "reservoir_l": proposal.reservoir_l,
        "valve_size": proposal.valve_size,
    }

    settings = {item.component_id: Q.of(item.setting_bar, "bar").value
                for item in proposal.settings}
    for component in topology.get("components", []):
        component_id = str(component["id"])
        if component_id in sizing:
            continue
        record: dict[str, Any] = {"rated_flow_lpm": rated}
        if component_id in settings:
            record["setting_pa"] = settings[component_id]
        sizing[component_id] = record

    # A flow control stated in L/min becomes an orifice coefficient by solving
    # the phase it acts on: the same bisection every arm goes through, on the
    # number this arm chose.
    for control in proposal.flow_controls:
        component = next(
            (c for c in topology.get("components", [])
             if str(c["id"]) == control.component_id), None)
        if component is None:
            result.install_errors.append(f"unknown flow control {control.component_id}")
            continue
        target_flow = Q.of(control.set_flow_lpm, "l/min").value
        if component.get("comp_type") == "pressure_comp_flow_control":
            sizing[control.component_id]["set_flow_m3s"] = target_flow
            continue
        k = _fit_throttle_to_flow(topology, requirements, control.phase_id, sizing,
                                  control.component_id, target_flow)
        if k is not None:
            result.throttle_k[control.component_id] = k

    result.sizing = sizing
    result.certificate = certify(
        contract.problem_id, topology, requirements, contract, sizing,
        result.throttle_k, standard_envelope(load_tolerance))
    if result.catalog_violations:
        from ..sizing.certify import Finding
        result.certificate.findings.append(Finding(
            "V1", "NON_STANDARD_SIZE", "warning",
            "; ".join(result.catalog_violations)))
    return result


def _fit_throttle_to_flow(topology, requirements, phase_id, sizing, control_id,
                          target_flow_m3s: float) -> float | None:
    """Coefficient that passes the stated flow through this control in this phase."""
    low, high = 1e-11, 1e-2
    for _ in range(160):
        guess = math.sqrt(low * high)
        model = build_phase_model(topology, requirements, str(phase_id), sizing,
                                  throttle_k={control_id: guess})
        if model is None:
            return None
        point = solve_phase(model)
        if not point.feasible:
            low = guess
            continue
        # Flow through the metered element is the velocity times the area on the
        # side it meters; the model already knows which that is.
        area = (model.exhaust_area.value
                if any(e.component_id == control_id for e in model.exhaust_elements)
                else model.supply_area.value)
        if point.velocity_m_s * area < target_flow_m3s:
            low = guess
        else:
            high = guess
    return math.sqrt(low * high)
