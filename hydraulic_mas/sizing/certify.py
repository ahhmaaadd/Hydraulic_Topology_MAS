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
from .coverage import account_for_criteria
from .intervals import Enclosure, Envelope, Interval, bound_outcome, verdict_for
from .laws import valve_element
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


# Warnings that describe a design sitting on a boundary rather than clear of it.
# A certificate cannot claim proof over these: the enclosure may pass, but only
# because the margin it passes on is thinner than the modelling itself.
DOWNGRADING_FINDINGS = {"RELIEF_MARGIN_THIN"}


@dataclass
class SizingCertificate:
    problem_id: str
    certificates: list[CriterionCertificate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    operating_points: dict[str, dict] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    uncovered: list[dict] = field(default_factory=list)

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
        if any(item.code in DOWNGRADING_FINDINGS for item in self.findings):
            return "UNDECIDED"
        return "PROVED"

    def coverage(self) -> dict[str, int]:
        """How much of what was asked this certificate actually speaks to.

        ``answered`` and ``unanswered`` partition the *stated* acceptance
        criteria. ``checks`` is a different number - the criteria this
        certificate actually evaluated - because the compiler also derives checks
        from the motion phases that no stated criterion names.
        """
        answered = getattr(self.uncovered, "certified", 0)
        return {
            "stated": answered + len(self.uncovered),
            "answered": answered,
            "unanswered": len(self.uncovered),
            "checks": len(self.certificates),
        }

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
            "coverage": self.coverage(),
            "uncovered": list(self.uncovered),
        }


def _evaluator(topology, requirements, phase_id, sizing, throttle_k, quantity: str,
               *, relief_pa: float = 0.0, wide_open: bool = False):
    """Build f(envelope point) -> outcome, or None where the phase is infeasible.

    ``wide_open`` removes the phase's metering restriction before solving, which
    answers "how fast could this circuit go if the operator opened the control",
    as distinct from "how fast does it go at the setting chosen here". That is
    the question an *adjustable* speed requirement actually asks.
    """

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
        if wide_open:
            _open_metering(model)
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
        if quantity == "force_available":
            return _available_force(model, sizing, relief_pa)
        return None

    return evaluate


def _open_metering(model) -> None:
    """Back the phase's speed control fully off, leaving the rest of the path.

    A throttle wound open still has a body to flow through, so it becomes the
    valve's own square-law resistance rather than vanishing. A compensated
    control set wide open stops setting the flow at all.
    """
    for elements in (model.supply_elements, model.exhaust_elements):
        for index, element in enumerate(elements):
            if element.kind == "flow_setting":
                elements[index] = valve_element(
                    element.component_id, element.comp_type,
                    rated_flow_lpm=max(element.set_flow_m3s * 60000.0, 1.0) * 4.0)
            elif element.throttle_k is not None:
                elements[index] = valve_element(
                    element.component_id, element.comp_type, rated_flow_lpm=60.0)


def _available_force(model, sizing: dict, relief_pa: float) -> float:
    """Force the actuator can develop at stall, on the pressure available to it.

    Deliberately a *stall* calculation, not an evaluation at the running
    operating point. Using the solved exhaust pressure makes the check circular
    on any pressure-limited phase: there the supply pressure has already risen
    until the actuator force equals the load, so the formula recovers the load
    and reports the requirement back to itself. That is how two designs came to
    be certified on margins of 0.21 N and 8.25 N, with the efficiency spread
    cancelling out entirely instead of showing up as an interval.

    At stall nothing flows, so every resistive drop is zero and the only back
    pressure left is what a pressure-setting element deliberately holds - a
    counterbalance keeping a suspended load up, for instance. The supply side is
    capped by the relief or, on a reduced branch, by the reducing valve: judging
    a reduced branch against the raw relief overstates it and sends the repair
    loop chasing ever-larger bores for a cylinder that was never the problem.
    """
    supply_ceiling = relief_pa
    for element in model.supply_elements:
        if element.kind == "pressure_setting" and element.set_pressure_pa > 0:
            supply_ceiling = min(supply_ceiling, element.set_pressure_pa)
    # Only a valve that deliberately *holds* pressure loads the exhaust side at
    # stall. A counterbalance does, because it is carrying a suspended load. A
    # reducing valve does not: it limits pressure downstream in the direction it
    # feeds, and on a return line it is either bypassed by its reverse check or
    # simply not the thing setting the pressure. Counting one as back pressure
    # cost P7-07's clamp 9 kN of perfectly real force.
    back_pressure = 0.0
    for element in model.exhaust_elements:
        if element.comp_type in {"counterbalance_valve", "sequence_valve"}:
            back_pressure += float(
                sizing.get(element.component_id, {}).get("setting_pa", 0.0))
    return (supply_ceiling * model.supply_area.value
            - back_pressure * model.exhaust_area.value) * model.mechanical_efficiency


def certify(
    problem_id: str,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    sizing: dict,
    throttle_k: dict[str, float],
    envelope: Envelope,
    *,
    transient_check: bool = False,
) -> SizingCertificate:
    result = SizingCertificate(
        problem_id=problem_id,
        assumptions=list(contract.assumptions),
        uncovered=account_for_criteria(requirements, contract),
    )
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
    # A phase that runs *pressure-limited* sits at the relief by design: the
    # throttle admits less than the pump delivers and the surplus crosses the
    # valve, which is the whole point of a fixed pump with a metered feed. Its
    # pressure therefore equals the relief setting no matter where that setting
    # is put, so measuring a margin against it is meaningless - and reporting a
    # thin one made every such design look defective however it was sized. Only
    # a phase the relief is supposed to stay shut for can have a thin margin.
    regulating = {
        phase_id for phase_id, point in nominal_points.items()
        if point.feasible and point.regime == "pressure_limited"
    }
    worst_shut = max(
        (point.pump_pressure_pa for phase_id, point in nominal_points.items()
         if point.feasible and phase_id not in regulating), default=0.0)
    if relief_pa and worst > relief_pa + 1e-9:
        result.findings.append(Finding(
            "V7", "RELIEF_BELOW_WORKING_PRESSURE", "error",
            f"the worst phase needs {worst/1e5:.2f} bar at the pump but the relief is set to "
            f"{relief_pa/1e5:.2f} bar, so it opens before the load is reached"))
    elif relief_pa and worst_shut and (relief_pa - worst_shut) / relief_pa < 0.05:
        result.findings.append(Finding(
            "V7", "RELIEF_MARGIN_THIN", "warning",
            f"only {(relief_pa-worst_shut)/1e5:.2f} bar between the relief setting and the worst "
            "phase it is meant to stay shut for; the valve will simmer"))

    # --- V5: is a steady-state criterion even meaningful here? ---------------
    # Off by default: it integrates, and the whole point of the quasi-static
    # battery is that it does not have to. Turned on for the study that quotes it.
    if transient_check:
        _certify_kinematic_admissibility(result, topology, requirements, sizing,
                                         throttle_k, nominal_points, contract)

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

    if criterion.kind == "velocity_range":
        return _certify_adjustable_range(
            criterion, topology, requirements, sizing, throttle_k, envelope, lo, hi, factor)

    if criterion.kind == "velocity":
        evaluate = _evaluator(topology, requirements, criterion.phase_id, sizing, throttle_k, "velocity")
        enclosure = bound_outcome(evaluate, envelope)
        verdict = verdict_for(enclosure, lo, hi)
        margin = None
        if enclosure.method != "degenerate":
            margin = min(enclosure.interval.lo - lo, hi - enclosure.interval.hi)
        # A degenerate enclosure has no numbers in it. Formatting it anyway
        # prints "[nan, nan]", which reads as an interval and parses as one -
        # exactly the shape a downstream metric would pick up and average.
        achieved = (
            "not reached" if enclosure.method == "degenerate" else
            f"[{enclosure.interval.lo/factor:.4g}, {enclosure.interval.hi/factor:.4g}] {criterion.unit}"
        )
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id,
            criterion.describe_band(), achieved,
            verdict, enclosure.method, margin,
            ["force_balance", "continuity", "orifice_law", "relief_complementarity"],
            note=("infeasible somewhere in the envelope"
                  if enclosure.infeasible_points else ""),
        )

    if criterion.kind == "force":
        # Force is bounded over the same envelope as every other outcome. It was
        # once a single nominal evaluation returning PROVED or REFUTED with no
        # UNDECIDED band, which certified two designs on margins of 0.21 N and
        # 10.2 N - inside the efficiency spread, so not evidence of anything.
        point = nominal_points.get(criterion.phase_id)
        if point is None and _is_hold(requirements, criterion.phase_id):
            # A hold phase moves nothing, so there is no operating point to
            # solve. What matters is whether the actuator can *sustain* the load
            # on the pressure available to it, which is a static question - but
            # still one whose answer moves with mechanical efficiency.
            def hold_at(sample: dict[str, float]) -> float | None:
                return _static_hold_force(
                    topology, requirements, criterion, sizing, relief_pa,
                    mechanical_efficiency=sample.get("mechanical_efficiency", 0.90))

            if hold_at(envelope.nominal()) is None:
                return None
            enclosure = bound_outcome(hold_at, envelope)
            return CriterionCertificate(
                criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
                f"[{enclosure.interval.lo/1e3:.4g}, {enclosure.interval.hi/1e3:.4g}] kn sustainable",
                verdict_for(enclosure, lo, hi), enclosure.method,
                _margin_of(enclosure, lo, hi),
                ["force_balance", "load_lock_retention"])
        if point is None or not point.feasible:
            return CriterionCertificate(
                criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
                "not reached", "REFUTED", "degenerate", None,
                ["force_balance"], "the phase has no feasible operating point")
        evaluate = _evaluator(topology, requirements, criterion.phase_id, sizing,
                              throttle_k, "force_available", relief_pa=relief_pa)
        enclosure = bound_outcome(evaluate, envelope)
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
            f"[{enclosure.interval.lo/1e3:.4g}, {enclosure.interval.hi/1e3:.4g}] kn available",
            verdict_for(enclosure, lo, hi), enclosure.method,
            _margin_of(enclosure, lo, hi),
            ["force_balance", "pressure_ceiling"],
            note=("infeasible somewhere in the envelope"
                  if enclosure.infeasible_points else ""))

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
            phase_id for phase_id, point in nominal_points.items()
            if point.feasible and (
                criterion.function_id is None
                or _phase_function(requirements, phase_id) == criterion.function_id)
        ]
        if not relevant:
            return None
        worst = Interval(float("-inf"), float("-inf"))
        method = "monotone-corner"
        infeasible = 0
        for phase_id in relevant:
            evaluate = _evaluator(topology, requirements, phase_id, sizing,
                                  throttle_k, "pump_pressure")
            enclosure = bound_outcome(evaluate, envelope)
            if enclosure.method == "degenerate":
                method = "degenerate"
                continue
            infeasible += enclosure.infeasible_points
            if enclosure.method != "monotone-corner" and method != "degenerate":
                method = enclosure.method
            worst = Interval(max(worst.lo, enclosure.interval.lo),
                             max(worst.hi, enclosure.interval.hi))
        if method == "degenerate":
            return CriterionCertificate(
                criterion.id, criterion.kind, None, criterion.describe_band(),
                "not reached", "REFUTED", "degenerate", None, ["pressure_ceiling"])
        combined = Enclosure(worst, method, method == "monotone-corner", infeasible)
        verdict = verdict_for(combined, lo, hi)
        note = ""
        # A relief set *at* the ceiling pins every pressure-limited phase to the
        # ceiling, so checking the resulting pressure against that same ceiling
        # restates the planner's own choice. It is not independent evidence and
        # must not read as proof.
        pinned = any(
            nominal_points[phase_id].regime == "pressure_limited" for phase_id in relevant
        )
        if verdict == "PROVED" and pinned and relief_pa >= criterion.target.value - 1e-6:
            verdict = "UNDECIDED"
            note = ("the relief is set at this ceiling and the worst phase runs against it, "
                    "so the check restates the setting rather than testing it")
        return CriterionCertificate(
            criterion.id, criterion.kind, None, criterion.describe_band(),
            f"[{worst.lo/1e5:.4g}, {worst.hi/1e5:.4g}] bar worst phase", verdict,
            method, _margin_of(combined, lo, hi), ["pressure_ceiling"], note)
    return None


def _margin_of(enclosure: Enclosure, lo: float, hi: float) -> float | None:
    if enclosure.method == "degenerate":
        return None
    return min(enclosure.interval.lo - lo, hi - enclosure.interval.hi)


# A phase whose acceleration eats this much of its stroke is not well described
# by a steady operating point. Set from the transient cross-check in
# ``transient.py``: below roughly a fifth of the stroke the settled velocity
# agrees with the quasi-static one to about a percent, and above it the agreement
# stops meaning anything because the motion barely reaches the steady point.
TRANSIENT_STROKE_FRACTION = 0.20


def _certify_kinematic_admissibility(result: SizingCertificate, topology, requirements,
                                     sizing, throttle_k, nominal_points, contract) -> None:
    """Flag phases where a steady-state velocity criterion answers the wrong question.

    Everything else in this battery assumes each phase can be judged at a steady
    operating point. That assumption is not free and was never checked until the
    transient study was run: P7-07's clamp accelerates for longer than its whole
    50 mm stroke, so its certified velocity is a speed the clamp never reaches.

    This runs the real integration, which is why it is off by default. A
    closed-form proxy was tried first - chamber charging plus mass acceleration -
    and under-predicted the settling distance by two orders of magnitude, because
    what actually dominates is the asymptotic approach to the operating point
    rather than either of those terms. A cheap check that wrong is worse than no
    check, so it was removed rather than tuned into agreement: tuning a
    two-parameter estimate against the very integration it is standing in for
    produces a number that matches the benchmark and nothing else.

    So the honest arrangement is this. The integration is available, exact within
    its own assumptions, and costs seconds rather than milliseconds; the paper
    quotes it once per problem; the per-design loop states the assumption instead
    of pretending to check it.
    """
    from .transient import integrate_phase

    for phase_id, point in nominal_points.items():
        if not point.feasible or point.velocity_m_s <= 0:
            continue
        stroke = _stroke_of(requirements, phase_id)
        if not stroke:
            continue
        outcome = integrate_phase(topology, requirements, phase_id, sizing, throttle_k)
        if outcome is None or outcome.diverged:
            continue
        fraction = outcome.stroke_fraction_settling
        if fraction is None or fraction < TRANSIENT_STROKE_FRACTION:
            continue
        result.findings.append(Finding(
            "V5", "PHASE_MOSTLY_TRANSIENT", "warning",
            f"{phase_id}: reaching {outcome.settled_velocity_m_s*60:.2f} m/min takes "
            f"{outcome.settling_distance_m*1000:.0f} mm of a {stroke*1000:.0f} mm stroke, so "
            "the motion is largely acceleration and a steady-state velocity criterion "
            "describes a condition it barely reaches. The estimate assumes the stated load "
            "is an accelerated mass; where it is a reaction it will be pessimistic",
            [phase_id]))


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
    # ``default`` matters: a design in which *every* phase is infeasible reaches
    # here with nothing to take a maximum over. The deterministic planner never
    # produces one, so this went unnoticed until the ablation started feeding in
    # designs that fail outright - which is the normal case for an arm with no
    # verifier, and must be a verdict rather than a traceback.
    dissipated = max(
        ((flow - point.supply_flow_m3s) * point.pump_pressure_pa
         for point in nominal_points.values() if point.feasible),
        default=0.0,
    )
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


def _certify_adjustable_range(criterion, topology, requirements, sizing, throttle_k,
                              envelope, lo, hi, factor) -> CriterionCertificate:
    """Certify a speed stated as an *adjustment span*, e.g. "0.5 .. 1 m/min".

    A fixed throttle setting sits at one point inside the span, so comparing that
    one point against the whole band can never succeed - which is why this used
    to be hardcoded to UNDECIDED, making the criterion unprovable by
    construction and quietly costing P7-01 its verdict on both arms.

    What the requirement actually asks is whether the *operator* can reach both
    ends. Throttling further down is always available, so the slow end is free
    and the binding question is the fast one: with the speed control backed fully
    off, does the circuit still deliver at least the top of the span everywhere
    in the envelope? That is a genuine two-sided test of the pump, the areas and
    the path losses, and it refutes a circuit that is simply too slow.
    """
    evaluate = _evaluator(topology, requirements, criterion.phase_id, sizing,
                          throttle_k, "velocity", wide_open=True)
    enclosure = bound_outcome(evaluate, envelope)
    if enclosure.method == "degenerate":
        return CriterionCertificate(
            criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
            "not reached", "REFUTED", "degenerate", None,
            ["force_balance", "continuity", "orifice_law"],
            "the phase has no feasible operating point with the control open")
    # Reachability of the fast end is a one-sided bound: attaining more than the
    # top of the span is not a failure, it is headroom the throttle removes.
    reach = Enclosure(enclosure.interval, enclosure.method, enclosure.monotone,
                      enclosure.infeasible_points)
    verdict = verdict_for(reach, hi, float("inf"))
    return CriterionCertificate(
        criterion.id, criterion.kind, criterion.phase_id, criterion.describe_band(),
        f"attainable up to [{enclosure.interval.lo/factor:.4g}, "
        f"{enclosure.interval.hi/factor:.4g}] {criterion.unit} with the control open",
        verdict, enclosure.method, enclosure.interval.lo - hi,
        ["force_balance", "continuity", "orifice_law", "relief_complementarity"],
        note=("the span is reachable by throttling down from this maximum"
              if verdict == "PROVED" else
              "the circuit cannot reach the top of the adjustment span"
              if verdict == "REFUTED" else
              "the top of the adjustment span is not attainable everywhere in the envelope"))


def _static_hold_force(topology, requirements, criterion, sizing, relief_pa,
                       *, mechanical_efficiency: float = 0.90) -> float | None:
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
    return ceiling * sum(areas) * mechanical_efficiency


def _stroke_of(requirements: dict, phase_id: str) -> float | None:
    phase = _phase_of(requirements, phase_id) or {}
    distance = phase.get("distance") or {}
    if isinstance(distance.get("value"), (int, float)):
        return Q.of(float(distance["value"]), str(distance.get("unit") or "mm")).value
    return None


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
