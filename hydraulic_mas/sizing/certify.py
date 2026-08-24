"""The verification battery and the certificates it produces.

Seven layers, ordered by cost so that a cheap refutation happens before an
expensive solve. Each finding names the layer that produced it, so a failure is
traceable to a specific piece of physics rather than to "the sizer".

The layers are the ones the reference audit needed: V3 catches an actuator that
cannot develop its load inside a ceiling, V4 catches a coupled operating point
that independent per-phase arithmetic misses, V6 catches a prime mover sized on
hydraulic instead of shaft power, V7 catches a relief set below the working
pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contract import AcceptanceContract, Criterion
from .intervals import Enclosure, Envelope, Interval, bound_outcome, verdict_for
from .network import build_phase_model
from .quantities import Q
from .solve import solve_phase


@dataclass
class Finding:
    layer: str
    code: str
    severity: str          # error | warning
    message: str
    related: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "layer": self.layer, "code": self.code, "severity": self.severity,
            "message": self.message, "related": list(self.related),
        }


@dataclass
class CriterionCertificate:
    criterion_id: str
    kind: str
    phase_id: str | None
    required: str
    achieved: str
    verdict: str
    method: str
    margin: float | None
    equations: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id, "kind": self.kind, "phase_id": self.phase_id,
            "required": self.required, "achieved": self.achieved, "verdict": self.verdict,
            "method": self.method, "margin": self.margin, "governing_equations": list(self.equations),
            "note": self.note,
        }


@dataclass
class SizingCertificate:
    problem_id: str
    certificates: list[CriterionCertificate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    operating_points: dict[str, dict] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(item.code == "NO_CHECKABLE_CRITERIA" for item in self.findings):
            # An empty certificate cannot be a pass.
            return "UNDECIDED"
        if any(item.severity == "error" for item in self.findings):
            return "REFUTED"
        if any(item.verdict == "REFUTED" for item in self.certificates):
            return "REFUTED"
        if any(item.verdict == "UNDECIDED" for item in self.certificates):
            return "UNDECIDED"
        return "PROVED"

    def counts(self) -> dict[str, int]:
        result = {"PROVED": 0, "UNDECIDED": 0, "REFUTED": 0}
        for item in self.certificates:
            result[item.verdict] = result.get(item.verdict, 0) + 1
        return result

    def to_dict(self) -> dict:
        return {
            "problem_id": self.problem_id, "verdict": self.verdict, "counts": self.counts(),
            "criteria": [item.to_dict() for item in self.certificates],
            "findings": [item.to_dict() for item in self.findings],
            "operating_points": self.operating_points,
            "assumptions": list(self.assumptions),
        }


def _evaluator(topology, requirements, phase_id, sizing, throttle_k, quantity: str):
    """Build f(envelope point) -> outcome, or None where the phase is infeasible."""

    def evaluate(point: dict[str, float]) -> float | None:
        scaled = dict(sizing)
        supply = dict(sizing.get("__supply__", {}))
        if "volumetric_efficiency" in point and supply.get("displacement_cm3"):
            supply["flow_m3s"] = Q.of(
                supply["displacement_cm3"] * supply.get("speed_rpm", 1500)
                * point["volumetric_efficiency"] / 1000.0, "l/min").value
        scaled["__supply__"] = supply
        model = build_phase_model(
            topology, requirements, phase_id, scaled,
            throttle_k=throttle_k,
            mechanical_efficiency=point.get("mechanical_efficiency", 0.90),
        )
        if model is None:
            return None
        if "load_factor" in point:
            model.load = Q(model.load.value * point["load_factor"], (1, 1, -2))
        operating = solve_phase(model)
        if not operating.feasible:
            return None
        if quantity == "velocity":
            return operating.velocity_m_s
        if quantity == "pump_pressure":
            return operating.pump_pressure_pa
        if quantity == "supply_pressure":
            return operating.supply_pressure_pa
        return None

    return evaluate


def certify(
    problem_id: str,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    sizing: dict,
    throttle_k: dict[str, float],
    envelope: Envelope,
) -> SizingCertificate:
    result = SizingCertificate(problem_id=problem_id, assumptions=list(contract.assumptions))
    supply = sizing.get("__supply__", {})
    relief_pa = float(supply.get("relief_pa", 0.0))

    # --- V4: solve every motion phase once at nominal, for the record ---------
    nominal_points: dict[str, Any] = {}
    for criterion in contract.criteria:
        if criterion.phase_id and criterion.phase_id not in nominal_points:
            model = build_phase_model(topology, requirements, criterion.phase_id, sizing,
                                      throttle_k=throttle_k)
            if model is None:
                continue
            operating = solve_phase(model)
            nominal_points[criterion.phase_id] = operating
            result.operating_points[criterion.phase_id] = {
                "regime": operating.regime,
                "velocity_m_min": operating.velocity_m_s * 60.0,
                "pump_pressure_bar": operating.pump_pressure_pa / 1e5,
                "supply_pressure_bar": operating.supply_pressure_pa / 1e5,
                "exhaust_pressure_bar": operating.exhaust_pressure_pa / 1e5,
                "relief_flow_lpm": operating.relief_flow_m3s * 60000.0,
                "feasible": operating.feasible,
            }
            if not operating.feasible:
                result.findings.append(Finding(
                    "V4", "PHASE_NOT_FEASIBLE", "error",
                    f"{criterion.phase_id}: {operating.reason}", [criterion.phase_id]))
            elif operating.reason:
                result.findings.append(Finding(
                    "V4", "OPERATING_POINT_AMBIGUOUS", "warning",
                    f"{criterion.phase_id}: {operating.reason}", [criterion.phase_id]))

    # --- V2/V3: every criterion, bounded over the envelope -------------------
    for criterion in contract.criteria:
        certificate = _certify_criterion(
            criterion, topology, requirements, sizing, throttle_k, envelope,
            nominal_points, relief_pa)
        if certificate is not None:
            result.certificates.append(certificate)

    # --- V7: settings and safety ordering ------------------------------------
    ceilings = [c for c in contract.of_kind("pressure_ceiling") if c.function_id is None]
    if ceilings and relief_pa:
        ceiling = ceilings[0].target.value
        if relief_pa > ceiling + 1e-9:
            result.findings.append(Finding(
                "V7", "RELIEF_ABOVE_SYSTEM_CEILING", "error",
                f"relief set to {relief_pa/1e5:.1f} bar, above the {ceiling/1e5:.1f} bar system ceiling"))
    worst = max((point.pump_pressure_pa for point in nominal_points.values()
                 if point.feasible), default=0.0)
    if relief_pa and worst > relief_pa + 1e-9:
        result.findings.append(Finding(
            "V7", "RELIEF_BELOW_WORKING_PRESSURE", "error",
            f"the worst phase needs {worst/1e5:.2f} bar at the pump but the relief is set to "
            f"{relief_pa/1e5:.2f} bar, so it opens before the load is reached"))
    elif relief_pa and worst and (relief_pa - worst) / relief_pa < 0.05:
        result.findings.append(Finding(
            "V7", "RELIEF_MARGIN_THIN", "warning",
            f"only {(relief_pa-worst)/1e5:.2f} bar between the worst working pressure and the "
            "relief setting; the valve will simmer"))

    # --- V6: energy and thermal ---------------------------------------------
    _certify_power(result, sizing, nominal_points)

    # Nothing checked is not the same as everything checked. A requirements
    # extraction carrying no loads or speeds, or one whose criteria all resolve
    # to nothing because no phase could be solved, would otherwise leave an empty
    # certificate reporting PROVED - the most dangerous output this stage could
    # produce, because it looks exactly like success.
    if not result.certificates:
        detail = (
            "the requirements contain no load, speed or pressure limit to size against"
            if not contract.criteria
            else f"none of the {len(contract.criteria)} stated criteria could be evaluated, "
                 "because no motion phase produced a solvable operating point"
        )
        result.findings.append(Finding(
            "V0", "NO_CHECKABLE_CRITERIA", "warning",
            f"this certificate proves nothing: {detail}. Check the extraction and the "
            "phase configurations before trusting the sizing."))
    return result


def _certify_criterion(criterion: Criterion, topology, requirements, sizing, throttle_k,
                       envelope, nominal_points, relief_pa) -> CriterionCertificate | None:
    lo, hi = criterion.bounds()
    factor = criterion.target.value / criterion.target.to(criterion.unit) if criterion.target.value else 1.0

    if criterion.kind in {"velocity", "velocity_range"}:
        evaluate = _evaluator(topology, requirements, criterion.phase_id, sizing, throttle_k, "velocity")
        enclosure = bound_outcome(evaluate, envelope)
        verdict = verdict_for(enclosure, lo, hi)
        if criterion.kind == "velocity_range":
            # The band is the *adjustment span*, not a single operating point;
            # a fixed throttle setting can only prove one end of it.
            verdict = "UNDECIDED" if verdict != "REFUTED" else verdict
        margin = None
        if enclosure.method != "degenerate":
            margin = min(enclosure.interval.lo - lo, hi - enclosure.interval.hi)
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id,
            criterion.describe_band(),
            f"[{enclosure.interval.lo/factor:.4g}, {enclosure.interval.hi/factor:.4g}] {criterion.unit}",
            verdict, enclosure.method, margin,
            ["force_balance", "continuity", "orifice_law", "relief_complementarity"],
            note=("infeasible somewhere in the envelope"
                  if enclosure.infeasible_points else ""),
        )

    if criterion.kind == "force":
        point = nominal_points.get(criterion.phase_id)
        if point is None and _is_hold(requirements, criterion.phase_id):
            # A hold phase moves nothing, so there is no operating point to
            # solve. What matters is whether the actuator can *sustain* the load
            # on the pressure available to it, which is a static question.
            held = _static_hold_force(topology, requirements, criterion, sizing, relief_pa)
            if held is None:
                return None
            verdict = "PROVED" if held >= criterion.target.value else "REFUTED"
            return CriterionCertificate(
                criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
                f"{held/1e3:.3g} kn sustainable", verdict, "static-hold-balance",
                held - criterion.target.value, ["force_balance", "load_lock_retention"])
        if point is None or not point.feasible:
            return CriterionCertificate(
                criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
                "not reached", "REFUTED", "degenerate", None,
                ["force_balance"], "the phase has no feasible operating point")
        model = build_phase_model(topology, requirements, criterion.phase_id, sizing,
                                  throttle_k=throttle_k)
        # The force available at the actuator is not the relief setting: the
        # supply path drops pressure on the way, and a reducing valve caps the
        # branch outright. Judging against the raw relief setting overstates a
        # reduced branch and sends the repair loop chasing ever-larger bores for
        # a cylinder that was never the problem.
        supply_ceiling = relief_pa
        for element in model.supply_elements:
            if element.kind == "pressure_setting" and element.set_pressure_pa > 0:
                supply_ceiling = min(supply_ceiling, element.set_pressure_pa)
        stall_drop = sum(
            element.pressure_drop(0.0) for element in model.supply_elements
        )
        available = ((supply_ceiling - stall_drop) * model.supply_area.value
                     - point.exhaust_pressure_pa * model.exhaust_area.value) * model.mechanical_efficiency
        verdict = "PROVED" if available >= criterion.target.value else "REFUTED"
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
            f"{available/1e3:.3g} kn available at the relief setting", verdict,
            "static-force-balance", available - criterion.target.value,
            ["force_balance", "pressure_ceiling"])

    if criterion.kind == "duration":
        point = nominal_points.get(criterion.phase_id)
        phase = _phase_of(requirements, criterion.phase_id)
        distance = ((phase or {}).get("distance") or {}).get("value")
        if point is None or not point.feasible or not isinstance(distance, (int, float)):
            return None
        travel = Q.of(float(distance), str(phase["distance"].get("unit") or "mm")).value
        evaluate = _evaluator(topology, requirements, criterion.phase_id, sizing, throttle_k, "velocity")
        enclosure = bound_outcome(evaluate, envelope)
        if enclosure.method == "degenerate" or enclosure.interval.lo <= 0:
            return CriterionCertificate(
                criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
                "not reached", "REFUTED", "degenerate", None, ["kinematics"])
        times = Interval(travel / enclosure.interval.hi, travel / enclosure.interval.lo)
        verdict = verdict_for(Enclosure(times, enclosure.method, enclosure.monotone,
                                        enclosure.infeasible_points), lo, hi)
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
            f"[{times.lo:.4g}, {times.hi:.4g}] s", verdict, enclosure.method,
            min(times.lo - lo, hi - times.hi), ["kinematics", "force_balance"])

    if criterion.kind == "pressure_ceiling":
        relevant = [
            point for phase_id, point in nominal_points.items()
            if point.feasible and (
                criterion.function_id is None
                or _phase_function(requirements, phase_id) == criterion.function_id)
        ]
        if not relevant:
            return None
        worst = max(point.pump_pressure_pa for point in relevant)
        verdict = "PROVED" if worst <= criterion.target.value + 1e-9 else "REFUTED"
        return CriterionCertificate(
            criterion.id, criterion.kind, None, criterion.describe_band(),
            f"{worst/1e5:.3g} bar worst phase", verdict, "worst-phase-nominal",
            (criterion.target.value - worst) / 1e5, ["pressure_ceiling"])
    return None


def _certify_power(result: SizingCertificate, sizing: dict, nominal_points: dict) -> None:
    supply = sizing.get("__supply__", {})
    motor_kw = float(supply.get("motor_kw", 0.0))
    overall = float(supply.get("overall_efficiency", 0.85))
    flow = float(supply.get("flow_m3s", 0.0))
    if not motor_kw or not flow:
        return
    worst_shaft = 0.0
    worst_phase = ""
    for phase_id, point in nominal_points.items():
        if not point.feasible:
            continue
        # The pump turns against its own outlet pressure carrying its whole
        # delivery, not just the part the actuator accepts. Sizing on the
        # actuator's share is the hydraulic-power error the audit found twice.
        shaft = flow * point.pump_pressure_pa / overall
        if shaft > worst_shaft:
            worst_shaft, worst_phase = shaft, phase_id
    if worst_shaft / 1000.0 > motor_kw + 1e-9:
        result.findings.append(Finding(
            "V6", "PRIME_MOVER_UNDERSIZED", "error",
            f"the worst phase ({worst_phase}) needs {worst_shaft/1000:.2f} kW at the shaft but the "
            f"prime mover is {motor_kw:.2f} kW; the pump carries full delivery against the relief, "
            "so hydraulic power at the actuator understates the duty",
            [worst_phase]))
    dissipated = max(
        (flow - point.supply_flow_m3s) * point.pump_pressure_pa
        for point in nominal_points.values() if point.feasible
    ) if nominal_points else 0.0
    reservoir_l = float(supply.get("reservoir_l", 0.0))
    if dissipated > 0 and reservoir_l:
        # A common rule of thumb: a bare steel reservoir sheds roughly 1 kW per
        # 100 L of surface-equivalent volume at a 40 K rise.
        capacity_w = reservoir_l * 10.0
        if dissipated > capacity_w:
            result.findings.append(Finding(
                "V6", "THERMAL_CAPACITY_EXCEEDED", "warning",
                f"{dissipated/1000:.2f} kW crosses the relief at the worst phase but a {reservoir_l:.0f} L "
                f"reservoir sheds roughly {capacity_w/1000:.2f} kW; a cooler is needed"))


def _is_hold(requirements: dict, phase_id: str | None) -> bool:
    phase = _phase_of(requirements, phase_id)
    return bool(phase and str(phase.get("motion")) == "hold")


def _static_hold_force(topology, requirements, criterion, sizing, relief_pa) -> float | None:
    """Force the held actuator can sustain on the pressure available to it."""
    function_id = criterion.function_id
    areas = [
        record.get("cap_area_m2", 0.0)
        for component in topology.get("components", [])
        if component.get("comp_type") == "cylinder"
        and str(component.get("function_id")) == function_id
        and (record := sizing.get(str(component["id"]), {}))
    ]
    if not areas:
        return None
    ceiling = relief_pa
    for component in topology.get("components", []):
        if (component.get("comp_type") == "pressure_reducing_valve"
                and str(component.get("function_id")) == function_id):
            setting = sizing.get(str(component["id"]), {}).get("setting_pa")
            if setting:
                ceiling = min(ceiling, float(setting))
    return ceiling * sum(areas) * 0.90


def _phase_of(requirements: dict, phase_id: str | None) -> dict | None:
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) == str(phase_id):
                return phase
    return None


def _phase_function(requirements: dict, phase_id: str) -> str | None:
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) == str(phase_id):
                return str(function.get("id"))
    return None
