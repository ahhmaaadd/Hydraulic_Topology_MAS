"""Turn a proposed sizing *policy* into concrete standard components.

Everything the model decides arrives as intent - design against this pressure,
let this phase govern the flow, choose the rod for area ratio - and everything
numeric is computed here from that intent. The model never multiplies anything,
so a candidate can be strategically wrong and still be arithmetically exact,
which is what makes a refutation informative instead of ambiguous.
"""

from __future__ import annotations

import math
from typing import Any

from .catalog_sizing import (
    NoStandardSizeError,
    select_bore,
    select_displacement,
    select_motor,
    select_pressure_class,
    select_reservoir,
    select_rod,
    select_rod_for_ratio,
    select_tube,
    select_valve_size,
)
from .certify import SizingCertificate, certify
from .contract import AcceptanceContract, speed_relation
from .intervals import standard_envelope
from .network import build_phase_model
from .quantities import Q, annulus_area, area_of_bore
from .schemas_sizing import SizingCandidate
from .solve import solve_phase


SPEED_RPM = 1500.0
ETA_M, ETA_V, ETA_O = 0.90, 0.90, 0.85


def _cylinders(topology: dict) -> dict[str, str]:
    return {
        str(component["id"]): str(component.get("function_id"))
        for component in topology.get("components", [])
        if component.get("comp_type") == "cylinder"
    }


def _phases(requirements: dict, function_id: str) -> list[dict]:
    for function in requirements.get("functions", []):
        if str(function.get("id")) == function_id:
            return list(function.get("motion_phases") or [])
    return []


def _si(record: dict | None) -> float | None:
    if not isinstance(record, dict):
        return None
    value = record.get("value")
    if not isinstance(value, (int, float)):
        return None
    return Q.of(float(value), str(record.get("unit") or "")).value


def _find_phase(requirements: dict, phase_id: str) -> tuple[str, dict] | None:
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) == str(phase_id):
                return str(function.get("id")), phase
    return None


def apply_candidate(
    candidate: SizingCandidate,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    *,
    load_tolerance: float = 0.0,
) -> tuple[dict[str, Any], dict[str, float], SizingCertificate, list[str]]:
    """Return ``(sizing, throttle_k, certificate, problems)``.

    ``problems`` records anything structurally wrong with the *candidate itself* -
    a cylinder it forgot, a phase that does not exist - as opposed to a design
    that is merely refuted by physics. The two are scored differently: the first
    means the model produced an unusable plan, the second means it produced a
    plan that does not work, and only the second is interesting engineering.
    """
    problems: list[str] = []
    cylinders = _cylinders(topology)
    decisions = {item.cylinder_id: item for item in candidate.actuator_decisions}

    missing = sorted(set(cylinders) - set(decisions))
    if missing:
        problems.append(f"no actuator decision for {missing}")

    bores: dict[str, float] = {}
    rods: dict[str, float] = {}
    for cylinder_id, function_id in cylinders.items():
        decision = decisions.get(cylinder_id)
        phases = _phases(requirements, function_id)
        forces = [value for value in (_si(phase.get("force")) for phase in phases) if value]
        siblings = sum(1 for owner in cylinders.values() if owner == function_id)

        if decision is not None:
            found = _find_phase(requirements, decision.governing_phase_id)
            if found is None:
                problems.append(
                    f"{cylinder_id}: governing phase {decision.governing_phase_id!r} does not exist"
                )
                peak = max(forces) if forces else 0.0
            else:
                peak = _si(found[1].get("force")) or (max(forces) if forces else 0.0)
            design_pressure = max(decision.design_pressure_bar, 0.1) * 1e5
        else:
            peak = max(forces) if forces else 0.0
            design_pressure = 100e5

        peak /= max(siblings, 1)
        required = Q(peak / (design_pressure * ETA_M), (2, 0, 0)) if peak else Q.of(1.0, "mm2")
        try:
            bore = select_bore(required)
        except NoStandardSizeError as error:
            problems.append(f"{cylinder_id}: {error}")
            bore = 320.0
        if decision is not None and decision.rod_strategy == "area_ratio" and decision.target_area_ratio:
            rod = select_rod_for_ratio(bore, float(decision.target_area_ratio))
        else:
            rod = select_rod(bore)
        bores[cylinder_id], rods[cylinder_id] = bore, rod

    settings = {item.component_id: item.setting_bar * 1e5 for item in candidate.setting_decisions}

    def build(relief_pa: float) -> dict[str, Any]:
        sizing: dict[str, Any] = {}
        for cylinder_id in cylinders:
            sizing[cylinder_id] = {
                "cap_area_m2": area_of_bore(bores[cylinder_id]).value,
                "annulus_area_m2": annulus_area(bores[cylinder_id], rods[cylinder_id]).value,
                "bore_mm": bores[cylinder_id], "rod_mm": rods[cylinder_id],
            }
        for component in topology.get("components", []):
            component_id = str(component["id"])
            if component_id in sizing:
                continue
            record = sizing.setdefault(component_id, {})
            record["rated_flow_lpm"] = 60.0
            if component_id in settings:
                record["setting_pa"] = settings[component_id]
            elif component.get("comp_type") == "pressure_reducing_valve":
                branch = min(
                    (
                        criterion.target.value
                        for criterion in contract.of_kind("pressure_ceiling")
                        if criterion.function_id in (None, str(component.get("function_id")))
                    ),
                    default=relief_pa,
                )
                record["setting_pa"] = branch
        demand = 0.0
        aim_off = 1.0 + candidate.supply_decision.speed_aim_off
        for function_id in set(cylinders.values()):
            cap = sum(sizing[cid]["cap_area_m2"] for cid, owner in cylinders.items() if owner == function_id)
            annulus = sum(sizing[cid]["annulus_area_m2"] for cid, owner in cylinders.items() if owner == function_id)
            for phase in _phases(requirements, function_id):
                motion = str(phase.get("motion"))
                if motion not in {"extend", "retract"}:
                    continue
                area = cap if motion == "extend" else annulus
                speed = _si(phase.get("speed"))
                if speed:
                    if speed_relation(phase) == ">=":
                        speed *= aim_off
                    demand = max(demand, area * speed)
                span = _speed_span(phase)
                if span:
                    demand = max(demand, area * span[1])
        displacement = select_displacement(Q(demand, (3, 0, -1)), SPEED_RPM, ETA_V)
        flow = displacement * SPEED_RPM * ETA_V / 1000.0
        sizing["__supply__"] = {
            "displacement_cm3": displacement, "speed_rpm": SPEED_RPM,
            "flow_m3s": Q.of(flow, "l/min").value, "relief_pa": relief_pa,
            "overall_efficiency": ETA_O,
        }
        return sizing

    def fit(sizing: dict) -> dict[str, float]:
        throttles: dict[str, float] = {}
        aim_off = 1.0 + candidate.supply_decision.speed_aim_off
        for function_id in set(cylinders.values()):
            for phase in _phases(requirements, function_id):
                if str(phase.get("metering_side")) not in {"meter_in", "meter_out"}:
                    continue
                target = _si(phase.get("speed"))
                if not target:
                    span = _speed_span(phase)
                    target = 0.5 * (span[0] + span[1]) if span else None
                if not target:
                    continue
                if speed_relation(phase) == ">=":
                    target *= aim_off
                compensated = [
                    str(c["id"]) for c in topology.get("components", [])
                    if c.get("comp_type") == "pressure_comp_flow_control"
                ]
                if compensated:
                    cylinder = next((cid for cid, owner in cylinders.items() if owner == function_id), None)
                    if cylinder:
                        record = sizing[cylinder]
                        area = (record["annulus_area_m2"] if phase.get("motion") == "extend"
                                else record["cap_area_m2"])
                        sizing[compensated[0]]["set_flow_m3s"] = target * area
                    continue
                controls = [
                    str(c["id"]) for c in topology.get("components", [])
                    if c.get("comp_type") == "one_way_flow_control"
                ]
                if not controls:
                    continue
                low, high = 1e-11, 1e-2
                for _ in range(160):
                    guess = math.sqrt(low * high)
                    model = build_phase_model(topology, requirements, str(phase["id"]), sizing,
                                              throttle_k={controls[0]: guess},
                                              mechanical_efficiency=ETA_M)
                    if model is None:
                        break
                    point = solve_phase(model)
                    if not point.feasible or point.velocity_m_s < target:
                        low = guess
                    else:
                        high = guess
                throttles[controls[0]] = math.sqrt(low * high)
        return throttles

    system_ceiling = min(
        (criterion.target.value for criterion in contract.of_kind("pressure_ceiling")
         if criterion.function_id is None),
        default=250e5,
    )
    relief = 0.9 * system_ceiling
    sizing = build(relief)
    throttles = fit(sizing)

    worst_load = 1.0 + load_tolerance
    worst = 0.0
    for criterion in contract.criteria:
        if not criterion.phase_id:
            continue
        model = build_phase_model(topology, requirements, criterion.phase_id, sizing,
                                  throttle_k=throttles,
                                  mechanical_efficiency=ETA_M / worst_load)
        if model is None:
            continue
        point = solve_phase(model)
        if point.feasible:
            worst = max(worst, point.pump_pressure_pa)
    if worst:
        relief = min(system_ceiling, worst * candidate.supply_decision.relief_margin)
        sizing = build(relief)
        throttles = fit(sizing)

    _auxiliaries(sizing, topology, cylinders, relief)
    certificate = certify(contract.problem_id, topology, requirements, contract, sizing,
                          throttles, standard_envelope(load_tolerance))
    return sizing, throttles, certificate, problems


def _speed_span(phase: dict) -> tuple[float, float] | None:
    from .contract import parse_range

    span = parse_range(phase.get("speed"))
    return (span[0].value, span[1].value) if span else None


def _auxiliaries(sizing: dict, topology: dict, cylinders: dict, relief: float) -> None:
    supply = sizing["__supply__"]
    flow = Q(supply["flow_m3s"], (3, 0, -1))
    supply["relief_pa"] = relief
    supply["motor_kw"] = select_motor(Q(flow.value * relief / ETA_O, (2, 1, -3)))
    supply["reservoir_l"] = select_reservoir(3.0 * flow.to("l/min"))
    supply["pressure_class_bar"] = select_pressure_class(Q(relief, (-1, 1, -2)))
    peak_return = flow.to("l/min")
    for cylinder_id in cylinders:
        record = sizing[cylinder_id]
        ratio = record["cap_area_m2"] / max(record["annulus_area_m2"], 1e-12)
        peak_return = max(peak_return, flow.to("l/min") * ratio)
    supply["peak_return_lpm"] = peak_return
    name, rated = select_valve_size(Q.of(peak_return, "l/min"))
    supply["valve_size"] = name
    for component in topology.get("components", []):
        if component.get("comp_type") in {
            "dcv", "sequence_valve", "pressure_reducing_valve", "check_valve",
            "pilot_check_valve", "single_pilot_check_valve", "one_way_flow_control",
            "pressure_comp_flow_control", "counterbalance_valve",
        }:
            sizing[str(component["id"])]["rated_flow_lpm"] = rated
    for label, velocity, reference in (("suction", 1.0, flow.to("l/min")),
                                       ("pressure", 4.0, flow.to("l/min")),
                                       ("return", 3.0, peak_return)):
        outside, wall, inside = select_tube(Q.of(reference, "l/min"), velocity)
        supply[f"{label}_tube"] = f"{outside:g}x{wall:g} (ID {inside:g} mm)"


def oversizing_index(sizing: dict, contract: AcceptanceContract, topology: dict,
                     requirements: dict) -> float:
    """How much larger than the theoretical minimum the actuators are.

    A design that passes because it is enormous is not a good design, and without
    this a trivial strategy of always going up three bore sizes would score
    perfectly on feasibility.
    """
    ratios: list[float] = []
    for component in topology.get("components", []):
        if component.get("comp_type") != "cylinder":
            continue
        record = sizing.get(str(component["id"]), {})
        if "cap_area_m2" not in record:
            continue
        function_id = str(component.get("function_id"))
        ceiling = min(
            (criterion.target.value for criterion in contract.of_kind("pressure_ceiling")
             if criterion.function_id in (None, function_id)),
            default=250e5,
        )
        forces = [value for value in
                  (_si(phase.get("force")) for phase in _phases(requirements, function_id)) if value]
        if not forces:
            continue
        siblings = sum(
            1 for other in topology.get("components", [])
            if other.get("comp_type") == "cylinder"
            and str(other.get("function_id")) == function_id
        )
        minimum = (max(forces) / max(siblings, 1)) / (ceiling * ETA_M)
        if minimum > 0:
            ratios.append(record["cap_area_m2"] / minimum)
    return sum(ratios) / len(ratios) if ratios else 1.0
