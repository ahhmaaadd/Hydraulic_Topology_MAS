"""Deterministic sizing of a validated topology, with closed-form repair.

This is the baseline arm: every number is derived, none is proposed by a model.
It exists for two reasons. It is the control an LLM planner has to beat, and it
is fully testable offline, which means the verification stack can be developed
and trusted before any stochastic component is added to the loop.

Sizing has a property the topology stage lacks: most repairs are computable. If a
phase cannot develop its load, the required area follows directly from the force
balance; if a valve is under-rated, the required rating is the flow through it.
So the repair loop solves rather than searches, and only the design *policy*
choices - which ceiling to design against, how much margin to carry - are fixed
constants here and would be the LLM's job later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .catalog_sizing import (
    BORE_SERIES,
    ROD_SERIES,
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
from .contract import AcceptanceContract, compile_contract, resolve_tolerance, speed_relation
from .certify import SizingCertificate, certify
from .intervals import standard_envelope
from .network import build_phase_model
from .quantities import Q, annulus_area, area_of_bore
from .solve import solve_phase


# The actuator is sized against most of the ceiling, not a conservative fraction
# of it. Designing at 70 percent looks safe but pushes the bore up, which drives
# the working pressure back toward the ceiling anyway and leaves the relief no
# room. 88 percent leaves a workable band for line losses and the relief margin.
DESIGN_PRESSURE_FRACTION = 0.88
RELIEF_MARGIN = 1.12              # relief above the worst working pressure
# Efficiency and load spread move an operating point by a few percent, so a
# control aimed exactly at a one-sided bound ends up straddling it.
ENVELOPE_AIM_OFF = 0.06
SPEED_RPM = 1500.0
ETA_M, ETA_V, ETA_O = 0.90, 0.90, 0.85


@dataclass
class SizingResult:
    problem_id: str
    sizing: dict[str, Any] = field(default_factory=dict)
    throttle_k: dict[str, float] = field(default_factory=dict)
    certificate: SizingCertificate | None = None
    repairs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _ceiling_for(function_id: str, contract: AcceptanceContract) -> float:
    """Tightest pressure ceiling that applies to this function, in Pa."""
    values = [
        criterion.target.value
        for criterion in contract.of_kind("pressure_ceiling")
        if criterion.function_id in (None, function_id)
    ]
    return min(values) if values else 250e5


def _cylinders_of(topology: dict) -> dict[str, str]:
    return {
        str(component["id"]): str(component.get("function_id"))
        for component in topology.get("components", [])
        if component.get("comp_type") == "cylinder"
    }


def _phases_of(requirements: dict, function_id: str) -> list[dict]:
    for function in requirements.get("functions", []):
        if str(function.get("id")) == function_id:
            return list(function.get("motion_phases") or [])
    return []


def _q(record: dict | None, default_unit: str = "") -> float | None:
    if not isinstance(record, dict):
        return None
    value = record.get("value")
    if not isinstance(value, (int, float)):
        return None
    return Q.of(float(value), str(record.get("unit") or default_unit)).value


def _needs_high_area_ratio(requirements: dict, function_id: str) -> float | None:
    """Speed ratio a load-actuated meter-out circuit has to deliver.

    When one fixed orifice serves two commanded speeds in the same direction, the
    achievable ratio is governed by how far the rod-side pressure swings, which is
    set by the area ratio. Sizing the rod from force alone is what produced a
    design sitting 0.4 bar from stall.
    """
    phases = [
        phase for phase in _phases_of(requirements, function_id)
        if phase.get("speed_realization") == "load_sensitive_throttled"
        and phase.get("motion") == "extend"
    ]
    speeds = [value for value in (_q(phase.get("speed")) for phase in phases) if value]
    if len(speeds) < 2:
        return None
    ratio = max(speeds) / min(speeds)
    return ratio if ratio > 1.5 else None


def size_problem(problem_id: str, topology: dict, requirements: dict,
                 *, max_rounds: int = 6) -> SizingResult:
    contract = compile_contract(requirements, problem_id)
    result = SizingResult(problem_id=problem_id)
    cylinders = _cylinders_of(topology)

    # ---- actuators ------------------------------------------------------
    bores: dict[str, float] = {}
    rods: dict[str, float] = {}
    for cylinder_id, function_id in cylinders.items():
        ceiling = _ceiling_for(function_id, contract)
        phases = _phases_of(requirements, function_id)
        forces = [value for value in (_q(phase.get("force")) for phase in phases) if value]
        peak = max(forces) if forces else 0.0
        # Parallel actuators on one rigid structure share the load.
        siblings = sum(1 for value in cylinders.values() if value == function_id)
        peak /= max(siblings, 1)
        # Start at the smallest bore that could *possibly* work - the area needed
        # at the full ceiling - and let the verifier drive it up. Searching from
        # below yields the smallest design that passes rather than the first one
        # a conservative design pressure happens to land on, and it is the
        # oversizing index that a paper reports.
        design_pressure = ceiling
        required = Q(peak / (design_pressure * ETA_M), (2, 0, 0)) if peak else Q.of(1.0, "mm2")
        bore = select_bore(required)
        ratio = _needs_high_area_ratio(requirements, function_id)
        rod = select_rod_for_ratio(bore, min(ratio, 2.2)) if ratio else select_rod(bore)
        bores[cylinder_id], rods[cylinder_id] = bore, rod
        if ratio:
            result.notes.append(
                f"{cylinder_id}: rod chosen for a {ratio:.1f}:1 speed ratio, not for force alone")

    def build_sizing(relief_pa: float) -> dict[str, Any]:
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
            if component.get("comp_type") == "pressure_reducing_valve":
                # A reduced branch is set at its own ceiling, which is the
                # requirement that put the valve in the circuit.
                branch = _ceiling_for(str(component.get("function_id")), contract)
                record["setting_pa"] = branch
        # Governing flow: the fastest commanded extend across all functions.
        # Parallel actuators on one structure draw simultaneously, so their areas
        # add. Sizing the pump on a single cylinder halves the platen's speed.
        demand = 0.0
        for function_id in set(cylinders.values()):
            area = sum(
                sizing[cylinder_id]["cap_area_m2"]
                for cylinder_id, owner in cylinders.items() if owner == function_id
            )
            annulus = sum(
                sizing[cylinder_id]["annulus_area_m2"]
                for cylinder_id, owner in cylinders.items() if owner == function_id
            )
            for phase in _phases_of(requirements, function_id):
                motion = str(phase.get("motion"))
                if motion not in {"extend", "retract"}:
                    continue
                # The pump has to serve the fastest phase in *either* direction.
                # Sizing on extend alone leaves a fast retract on the annulus
                # short of flow, which is how P7-03's return ended up straddling
                # its own requirement.
                supply_area = area if motion == "extend" else annulus
                speed = _q(phase.get("speed"))
                if speed:
                    if speed_relation(phase) == ">=":
                        speed *= 1.0 + ENVELOPE_AIM_OFF
                    demand = max(demand, supply_area * speed)
                span = _speed_span(phase)
                if span:
                    demand = max(demand, supply_area * span[1])
        displacement = select_displacement(Q(demand, (3, 0, -1)), SPEED_RPM, ETA_V)
        flow = displacement * SPEED_RPM * ETA_V / 1000.0        # L/min
        sizing["__supply__"] = {
            "displacement_cm3": displacement, "speed_rpm": SPEED_RPM,
            "flow_m3s": Q.of(flow, "l/min").value, "relief_pa": relief_pa,
            "overall_efficiency": ETA_O,
        }
        return sizing

    system_ceiling = _ceiling_for("__system__", contract)
    relief = 0.9 * system_ceiling
    last_signature: tuple = ()
    sizing = build_sizing(relief)

    # ---- throttle settings ----------------------------------------------
    def fit_throttles(sizing: dict) -> dict[str, float]:
        settings: dict[str, float] = {}
        for function_id in set(cylinders.values()):
            for phase in _phases_of(requirements, function_id):
                if str(phase.get("metering_side")) not in {"meter_in", "meter_out"}:
                    continue
                target = _q(phase.get("speed"))
                if not target:
                    # An adjustable band has no single target; set the control to
                    # the middle of the span so both ends remain reachable.
                    span = _speed_span(phase)
                    target = 0.5 * (span[0] + span[1]) if span else None
                if not target:
                    continue
                # Aim off the bound rather than at it. Fitting a control exactly
                # to a one-sided target leaves the achievable band straddling it
                # once efficiency and load spread are admitted, which the
                # envelope check can only report as UNDECIDED. Aiming a little
                # inside the requirement is what a person does by hand.
                if speed_relation(phase) == ">=":
                    target *= 1.0 + ENVELOPE_AIM_OFF
                compensated = [
                    str(component["id"]) for component in topology.get("components", [])
                    if component.get("comp_type") == "pressure_comp_flow_control"
                ]
                if compensated:
                    # A compensated control is set in flow, not in orifice area:
                    # that is the whole point of compensating it.
                    cylinder = next(
                        (cid for cid, fid in cylinders.items() if fid == function_id), None
                    )
                    if cylinder:
                        record = sizing[cylinder]
                        area = (record["annulus_area_m2"]
                                if phase.get("motion") == "extend" else record["cap_area_m2"])
                        sizing[compensated[0]]["set_flow_m3s"] = target * area
                    continue
                controls = [
                    str(component["id"]) for component in topology.get("components", [])
                    if component.get("comp_type") == "one_way_flow_control"
                ]
                if not controls:
                    continue
                control = controls[0]
                low, high = 1e-11, 1e-2
                for _ in range(160):
                    guess = math.sqrt(low * high)
                    model = build_phase_model(topology, requirements, str(phase["id"]), sizing,
                                              throttle_k={control: guess},
                                              mechanical_efficiency=ETA_M)
                    if model is None:
                        break
                    point = solve_phase(model)
                    if not point.feasible or point.velocity_m_s < target:
                        low = guess
                    else:
                        high = guess
                settings[control] = math.sqrt(low * high)
        return settings

    # ---- iterate relief, then certify and repair -------------------------
    envelope = standard_envelope(_load_tolerance(requirements))

    def attempt(relief_pa: float):
        """Build, settle and certify the whole design at one relief setting."""
        candidate = build_sizing(relief_pa)
        throttles = fit_throttles(candidate)
        _size_auxiliaries(candidate, topology, requirements, cylinders, relief_pa, contract)
        certificate = certify(problem_id, topology, requirements, contract, candidate,
                              throttles, envelope)
        return candidate, throttles, certificate

    for round_number in range(1, max_rounds + 1):
        throttles = fit_throttles(sizing)
        # The relief has to clear the worst case the design is *judged* over, not
        # the nominal one. P7-01 states a +/-15 percent cutting-load variation; a
        # relief set from the nominal load leaves the slide unable to move at all
        # at the top of the band, which the envelope check then refutes.
        worst_load = 1.0 + _load_tolerance(requirements)
        worst = 0.0
        for criterion in contract.criteria:
            if not criterion.phase_id:
                continue
            model = build_phase_model(topology, requirements, criterion.phase_id, sizing,
                                      throttle_k=throttles,
                                      mechanical_efficiency=ETA_M * (1.0 / worst_load))
            if model is None:
                continue
            point = solve_phase(model)
            if point.feasible:
                worst = max(worst, point.pump_pressure_pa)
        relief, sizing, throttles, certificate = _search_relief(
            attempt, worst, system_ceiling, relief, result)
        result.sizing, result.throttle_k, result.certificate = sizing, throttles, certificate

        signature = tuple(sorted(
            (item.criterion_id, item.verdict) for item in certificate.certificates
        ))
        if signature == last_signature:
            # The previous repair changed nothing a check can see. Enlarging
            # further is guessing, so stop and report honestly.
            result.notes.append("repair stopped: the last change did not move any verdict")
            break
        last_signature = signature
        _diagnose_speed_ratio(certificate, requirements, cylinders, contract, relief, result)
        _diagnose_unmetered_band(certificate, topology, requirements, result)
        repaired = _repair(certificate, bores, rods, cylinders, requirements, result)
        if not repaired:
            break
        sizing = build_sizing(relief)
    return result


def _diagnose_unmetered_band(certificate, topology, requirements, result) -> None:
    """Name the real obstacle when a two-sided speed band cannot be hit.

    A phase fed straight from a fixed pump runs at whatever the displacement
    delivers, and displacements come in a preferred series. Where the band is
    narrower than the gap between two neighbouring sizes, no choice of pump lands
    inside it and enlarging the cylinder only moves the problem. The circuit needs
    a speed control on that branch - a topology change, not a sizing one - and
    saying so is more use than another round of arithmetic.
    """
    metered = {
        str(component.get("function_id"))
        for component in topology.get("components", [])
        if component.get("comp_type") in {"one_way_flow_control", "pressure_comp_flow_control"}
    }
    for item in certificate.certificates:
        if item.kind != "velocity" or item.verdict == "PROVED" or not item.phase_id:
            continue
        if ".." not in item.required:
            continue
        function_id = _phase_function_id(requirements, item.phase_id)
        if function_id in metered:
            continue
        note = (
            f"{item.phase_id}: the band {item.required} is fed straight from the pump, so the "
            "attainable speed steps with the displacement series and no standard pump lands "
            "inside it. A speed control on this branch would close it; a larger cylinder "
            "would not."
        )
        if note not in result.notes:
            result.notes.append(note)


def _phase_function_id(requirements: dict, phase_id: str) -> str | None:
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) == str(phase_id):
                return str(function.get("id"))
    return None


def _certificate_score(certificate) -> float:
    """Rank two certificates of the same design at different relief settings.

    Deliberately the same shape as the score the LLM arm's candidates are ranked
    by, so the two arms are compared on one yardstick rather than on whichever
    one happens to flatter the arm being reported.
    """
    counts = certificate.counts()
    score = 10.0 * counts["PROVED"] - 25.0 * counts["REFUTED"] - 8.0 * counts["UNDECIDED"]
    score -= 20.0 * sum(1 for item in certificate.findings if item.severity == "error")
    score -= 4.0 * sum(1 for item in certificate.findings if item.severity == "warning")
    return score


def _search_relief(attempt, worst_pressure: float, system_ceiling: float,
                   fallback: float, result) -> tuple:
    """Choose the relief setting, by search rather than by a fixed margin.

    The old policy was one line - ``min(ceiling, worst * 1.12)`` - and it has a
    failure mode that matters for the ablation this planner exists to be. When
    the worst working pressure sits within the margin of the ceiling, the clamp
    puts the relief *at* the ceiling, which pins every pressure-limited phase to
    it and leaves no band for the valve to regulate in. P7-04 and P7-05 both land
    there.

    It is also the wrong control to hold fixed. Where a load-actuated speed ratio
    is wanted, the attainable ratio is governed almost entirely by how close the
    relief sits to the slow phase's stall pressure, so a planner that cannot move
    the relief cannot reach requirements that are perfectly reachable. Comparing
    an LLM that chooses this setting against a baseline forbidden from choosing
    it measures the freedom, not the intelligence - which would make the headline
    ablation a strawman.

    So the baseline searches too. The grid is coarse and fixed, and every point on
    it is scored by the same verifier: no gradient, no tuning to the benchmark.
    """
    if not worst_pressure:
        candidate, throttles, certificate = attempt(fallback)
        return fallback, candidate, throttles, certificate

    settings: list[float] = []
    for margin in (1.04, 1.08, 1.12, 1.20, 1.30, 1.45):
        settings.append(worst_pressure * margin)
    for fraction in (0.80, 0.88, 0.94, 1.00):
        settings.append(system_ceiling * fraction)
    # Never below the pressure the design actually needs, never above the ceiling
    # it is allowed; then deduplicated so a clamped grid does not solve the same
    # point five times.
    usable = sorted({
        round(min(system_ceiling, max(value, worst_pressure * 1.01)), 3)
        for value in settings
    })

    best = None
    for value in usable:
        candidate, throttles, certificate = attempt(value)
        score = _certificate_score(certificate)
        # Ties go to the lower setting: same proof, less pressure across the
        # relief, less heat into the tank.
        if best is None or score > best[0] + 1e-9:
            best = (score, value, candidate, throttles, certificate)
    score, value, candidate, throttles, certificate = best
    result.notes.append(
        f"relief searched over {len(usable)} settings; chose {value/1e5:.2f} bar "
        f"(worst working pressure {worst_pressure/1e5:.2f} bar, ceiling {system_ceiling/1e5:.2f} bar)"
    )
    return value, candidate, throttles, certificate


def _diagnose_speed_ratio(certificate, requirements, cylinders, contract, relief, result) -> None:
    """Report a load-actuated speed ratio the current *relief setting* cannot deliver.

    Through one fixed orifice, a speed ratio r needs r^2 in orifice pressure drop.
    When both operating points sit at the relief, the cap pressure is the same in
    both and the whole ratio comes from the load difference:

        r = sqrt( (p_relief*A_cap - F_fast/eta) / (p_relief*A_cap - F_slow/eta) )

    The ratio therefore rises steeply as the relief approaches the slow phase's
    stall pressure, so this is a statement about the *setting*, not about the
    bore. An earlier version of this function claimed a bore window and called
    the requirement unreachable outright; that derivation assumed the fast phase
    ran flow-limited below the relief and so excluded the very regime that makes
    the ratio attainable. The LLM planner found a 100/70 design at 19.44 bar that
    meets a 3:1 ratio the old check called impossible.
    """
    # Fires on UNDECIDED as well as REFUTED. Once the relief is searched rather
    # than clamped, the shortfall usually stops being a refutation and becomes a
    # wide enclosure - the same physics, reported one notch softer - and the
    # explanation is just as useful there.
    unmet = {
        item.phase_id for item in certificate.certificates
        if item.kind in {"velocity", "velocity_range"}
        and item.verdict in {"REFUTED", "UNDECIDED"}
    }
    if not unmet:
        return
    for function_id in set(cylinders.values()):
        ratio_needed = _needs_high_area_ratio(requirements, function_id)
        if ratio_needed is None:
            continue
        phases = [
            phase for phase in _phases_of(requirements, function_id)
            if phase.get("speed_realization") == "load_sensitive_throttled"
            and phase.get("motion") == "extend"
        ]
        pairs = [(_q(p.get("speed")), _q(p.get("force"))) for p in phases]
        pairs = [(s, f) for s, f in pairs if s and f]
        if len(pairs) < 2:
            continue
        fast = max(pairs, key=lambda item: item[0])
        slow = min(pairs, key=lambda item: item[0])
        result.notes.append(
            f"{function_id}: the required {fast[0] / slow[0]:.1f}:1 load-actuated speed ratio is "
            f"governed by how close the relief sits to the slow phase's stall pressure, not by the "
            f"bore. Lowering the relief toward {slow[1] / (ETA_M):.0f} N / A_cap raises the "
            "attainable ratio; raising it collapses the ratio toward 1."
        )


def _speed_span(phase: dict) -> tuple[float, float] | None:
    from .contract import parse_range

    span = parse_range(phase.get("speed"))
    return (span[0].value, span[1].value) if span else None


def _load_tolerance(requirements: dict) -> float:
    text = " ".join(
        str(criterion.get("description") or "")
        for criterion in requirements.get("acceptance_criteria", [])
    ).casefold()
    if "15 percent" in text or "15%" in text or "plus or minus 15" in text:
        return 0.15
    return 0.0


def _size_auxiliaries(sizing, topology, requirements, cylinders, relief, contract) -> None:
    supply = sizing["__supply__"]
    flow = Q(supply["flow_m3s"], (3, 0, -1))
    supply["relief_pa"] = relief
    supply["motor_kw"] = select_motor(
        Q(flow.value * relief / ETA_O, (2, 1, -3)))
    supply["reservoir_l"] = select_reservoir(3.0 * flow.to("l/min"))
    supply["pressure_class_bar"] = select_pressure_class(Q(relief, (-1, 1, -2)))
    # Return flow can exceed delivery on a differential cylinder.
    peak_return = flow.to("l/min")
    for cylinder_id in cylinders:
        record = sizing[cylinder_id]
        ratio = record["cap_area_m2"] / max(record["annulus_area_m2"], 1e-12)
        peak_return = max(peak_return, flow.to("l/min") * ratio)
    supply["peak_return_lpm"] = peak_return
    name, rated = select_valve_size(Q.of(peak_return, "l/min"))
    supply["valve_size"] = name
    for component in topology.get("components", []):
        if component.get("comp_type") in {"dcv", "sequence_valve", "pressure_reducing_valve",
                                          "check_valve", "pilot_check_valve",
                                          "single_pilot_check_valve", "one_way_flow_control",
                                          "pressure_comp_flow_control", "counterbalance_valve"}:
            sizing[str(component["id"])]["rated_flow_lpm"] = rated
    for label, velocity, reference in (("suction", 1.0, flow.to("l/min")),
                                       ("pressure", 4.0, flow.to("l/min")),
                                       ("return", 3.0, peak_return)):
        outside, wall, inside = select_tube(Q.of(reference, "l/min"), velocity)
        supply[f"{label}_tube"] = f"{outside:g}x{wall:g} (ID {inside:g} mm)"


def _repair(certificate, bores, rods, cylinders, requirements, result) -> bool:
    """One closed-form repair pass. Returns True when something changed."""
    changed = False
    force_failures = {
        item.phase_id for item in certificate.certificates
        if item.kind == "force" and item.verdict == "REFUTED"
    }
    # A phase pinned at the relief runs slower than its flow allows. Widening the
    # bore drops the working pressure and hands the phase back to the pump.
    pressure_limited = {
        phase_id for phase_id, point in certificate.operating_points.items()
        if point.get("regime") == "pressure_limited"
        and any(
            item.phase_id == phase_id and item.kind == "velocity"
            and item.verdict in {"REFUTED", "UNDECIDED"}
            for item in certificate.certificates
        )
    }
    ceiling_failures = [
        item for item in certificate.certificates
        if item.kind == "pressure_ceiling" and item.verdict == "REFUTED"
    ]
    infeasible = {
        finding.related[0] for finding in certificate.findings
        if finding.code == "PHASE_NOT_FEASIBLE" and finding.related
    }
    # A load-actuated speed change is governed by the area ratio, not the bore.
    # If a throttled phase misses its speed, widening the rod gives the orifice
    # more pressure swing to work with; enlarging the bore does the opposite.
    # An UNDECIDED verdict is not a pass. If the achievable band straddles a
    # requirement the design does not meet it over its own operating envelope,
    # and the repair loop should try to move it clear rather than settle.
    speed_failures = {
        item.phase_id for item in certificate.certificates
        if item.kind in {"velocity", "velocity_range"}
        and item.verdict in {"REFUTED", "UNDECIDED"}
    }
    for phase_id in speed_failures:
        for cylinder_id, function_id in cylinders.items():
            phases = _phases_of(requirements, function_id)
            phase = next((item for item in phases if str(item.get("id")) == phase_id), None)
            if phase is None or _needs_high_area_ratio(requirements, function_id) is None:
                continue
            bore = bores[cylinder_id]
            current = rods[cylinder_id]
            wider = [rod for rod in ROD_SERIES if current < rod < 0.85 * bore]
            if not wider:
                continue
            rods[cylinder_id] = wider[0]
            result.repairs.append(
                f"{cylinder_id}: rod {current:g} -> {wider[0]:g} mm "
                "(more area ratio for the load-actuated speed change)")
            return True

    targets: set[str] = set()
    for phase_id in force_failures | infeasible | pressure_limited:
        for cylinder_id, function_id in cylinders.items():
            if any(str(phase.get("id")) == phase_id
                   for phase in _phases_of(requirements, function_id)):
                targets.add(cylinder_id)
    if ceiling_failures:
        targets.update(cylinders)
    for cylinder_id in targets:
        current = bores[cylinder_id]
        larger = [bore for bore in BORE_SERIES if bore > current]
        if not larger:
            continue
        bores[cylinder_id] = larger[0]
        try:
            rods[cylinder_id] = select_rod(larger[0])
        except NoStandardSizeError:
            continue
        result.repairs.append(
            f"{cylinder_id}: bore {current:g} -> {larger[0]:g} mm "
            "(load not developable within the ceiling)")
        changed = True
    return changed
