"""Tests for the sizing and certification stack.

Two kinds of test here. The first kind pins physics against hand calculations
that were done independently, so a refactor cannot quietly change what the
solver believes. The second kind pins the *reporting* contract: an honest
verifier has to be able to say REFUTED and UNDECIDED, and tests that only ever
assert PROVED would let that ability rot away unnoticed.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from hydraulic_mas.sizing.catalog_sizing import (
    BORE_SERIES,
    NoStandardSizeError,
    select_bore,
    select_displacement,
    select_motor,
    select_rod,
    select_rod_for_ratio,
    select_tube,
)
from hydraulic_mas.sizing.contract import compile_contract, parse_range, speed_relation
from hydraulic_mas.sizing.intervals import (
    Enclosure,
    Envelope,
    Interval,
    bound_outcome,
    standard_envelope,
    verdict_for,
)
from hydraulic_mas.sizing.laws import pump_delivery, shaft_power, throttle_flow, throttle_k_for
from hydraulic_mas.sizing.network import build_phase_model
from hydraulic_mas.sizing.planner import size_problem
from hydraulic_mas.sizing.quantities import (
    DimensionError,
    Q,
    annulus_area,
    area_of_bore,
)
from hydraulic_mas.sizing.solve import solve_phase
from hydraulic_mas.validation import phase_flow_paths, validate_topology


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


PROBLEM_IDS = ["P7-01", "P7-02", "P7-03", "P7-04", "P7-05", "P7-06", "P7-07"]


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_units_convert_and_round_trip() -> None:
    assert Q.of(12, "kn").to("n") == pytest.approx(12000.0)
    assert Q.of(8, "kgf/cm2").to("bar") == pytest.approx(7.845, abs=1e-3)
    assert Q.of(1.5, "m/min").to("mm/s") == pytest.approx(25.0)


def test_incompatible_dimensions_are_rejected() -> None:
    with pytest.raises(DimensionError):
        Q.of(1, "kn") + Q.of(1, "mm2")
    with pytest.raises(DimensionError):
        Q.of(1, "bar").to("l/min")


def test_pressure_from_force_and_area_matches_hand_calculation() -> None:
    """12 kN on a 70 mm bore at eta 0.90 is 34.65 bar."""
    pressure = Q.of(12, "kn") / area_of_bore(70) / 0.90
    assert pressure.to("bar") == pytest.approx(34.65, abs=0.01)


def test_tolerance_band() -> None:
    low, high = Q.of(1.5, "m/min", 0.10).band()
    assert low * 60 == pytest.approx(1.35)
    assert high * 60 == pytest.approx(1.65)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_selected_bore_is_a_preferred_size() -> None:
    """A hand calculation lands on 70 mm; ISO 3320 does not offer it."""
    required = Q(12000 / (40e5 * 0.9), (2, 0, 0))
    bore = select_bore(required)
    assert bore in BORE_SERIES
    assert 70 not in BORE_SERIES
    assert area_of_bore(bore) >= required


def test_rod_selection_keeps_a_sane_proportion() -> None:
    for bore in (50, 80, 125):
        rod = select_rod(bore)
        assert 0.4 * bore <= rod <= 0.75 * bore


def test_rod_for_ratio_targets_the_area_ratio() -> None:
    rod = select_rod_for_ratio(100, 2.0)
    ratio = area_of_bore(100).value / annulus_area(100, rod).value
    assert ratio == pytest.approx(2.0, abs=0.15)


def test_oversize_request_reports_a_catalog_gap() -> None:
    with pytest.raises(NoStandardSizeError):
        select_motor(Q.of(500, "kw"))


def test_suction_tube_is_larger_than_pressure_tube() -> None:
    """1 m/s suction needs far more bore than 4 m/s pressure at the same flow."""
    flow = Q.of(40, "l/min")
    _, _, suction_id = select_tube(flow, 1.0)
    _, _, pressure_id = select_tube(flow, 4.0)
    assert suction_id > pressure_id


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_bounded_duration_inverts_into_a_speed_floor(problems) -> None:
    """"350 mm in no more than 5 seconds" is a speed floor, not a ceiling."""
    contract = compile_contract(problems["P7-01"]["requirements"], "P7-01")
    retract = next(c for c in contract.criteria if c.id == "slide_retract_return__velocity")
    assert retract.relation == ">="


def test_adjustable_span_becomes_its_own_criterion(problems) -> None:
    contract = compile_contract(problems["P7-01"]["requirements"], "P7-01")
    span = next(c for c in contract.criteria if c.kind == "velocity_range")
    low, high = span.bounds()
    assert low * 60 == pytest.approx(0.5)
    assert high * 60 == pytest.approx(1.0)


def test_parse_range_reads_a_span() -> None:
    span = parse_range({"value": None, "raw_text": "between 0.5 and 1.0 m/min"})
    assert span is not None
    assert span[0].to("m/min") == pytest.approx(0.5)
    assert span[1].to("m/min") == pytest.approx(1.0)


def test_time_budget_is_read_as_a_speed_floor() -> None:
    """Finishing a budgeted move early is not a failure."""
    phase = {"speed": {"value": 50.0, "unit": "mm/s", "raw_text": "250 mm in 5 seconds"}}
    assert speed_relation(phase) == ">="


def test_duration_is_not_restated_when_a_speed_is_given() -> None:
    """Emitting both turns one requirement into two that can contradict."""
    phase = {
        "id": "p", "name": "p", "motion": "extend",
        "speed": {"value": 5.0, "unit": "m/min", "raw_text": "at 5 m/min"},
        "duration": {"value": 9.6, "unit": "s", "raw_text": "9.6 s"},
        "distance": {"value": 800.0, "unit": "mm", "raw_text": "0.80 m"},
        "speed_realization": "sizing_only", "metering_side": "none",
        "metered_chamber": "none", "metered_flow": "none", "flow_compensation": "none",
        "motion_control_justification": "t",
    }
    requirements = {
        "functions": [{"id": "f", "motion_phases": [phase]}],
        "global_constraints": {}, "operational_logic": {"sequence": []},
    }
    contract = compile_contract(requirements, "x")
    assert not [c for c in contract.criteria if c.kind == "duration"]


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_every_problem_compiles_a_non_empty_contract(problems, problem_id) -> None:
    contract = compile_contract(problems[problem_id]["requirements"], problem_id)
    assert contract.criteria
    for criterion in contract.criteria:
        low, high = criterion.bounds()
        assert low <= high


# ---------------------------------------------------------------------------
# Laws and the solver
# ---------------------------------------------------------------------------


def test_orifice_law_round_trips() -> None:
    """Set K from one operating point, recover the flow at another."""
    k = throttle_k_for(Q.of(2.0, "l/min"), Q.of(2.58, "bar"))
    assert throttle_flow(k, 23.23e5) * 60000 == pytest.approx(6.0, abs=0.05)


def test_pump_delivery_and_shaft_power() -> None:
    assert pump_delivery(8.73, 1500, 0.9).to("l/min") == pytest.approx(11.79, abs=0.02)
    # Shaft power is hydraulic power divided by overall efficiency; conflating
    # the two is the error the reference audit found twice.
    assert shaft_power(Q.of(19.09, "l/min"), Q.of(65, "bar"), 0.85).to("kw") == pytest.approx(2.433, abs=0.01)


def test_solver_reproduces_the_hand_calculated_slide(problems) -> None:
    """P7-04 with a 100/70 cylinder at 19 bar, computed independently by hand.

    Approach 1.500 m/min with the relief shut, feed 0.500 m/min pinned at the
    relief. This is the case that motivates the whole method: no amount of
    per-phase arithmetic finds it, because the orifice law, the force balance and
    the relief complementarity have to be solved together.
    """
    entry = problems["P7-04"]
    topology, requirements = entry["topology"], entry["requirements"]

    def run(k: float, phase: str):
        sizing = {
            "SlideCylinder": {
                "cap_area_m2": area_of_bore(100).value,
                "annulus_area_m2": annulus_area(100, 70).value,
            },
            "DCV": {"rated_flow_lpm": 60.0},
            "RodEndMeterOutFlowControl": {"rated_flow_lpm": 60.0},
            "__supply__": {"flow_m3s": Q.of(11.78, "l/min").value, "relief_pa": 19e5},
        }
        model = build_phase_model(topology, requirements, phase, sizing,
                                  throttle_k={"RodEndMeterOutFlowControl": k},
                                  mechanical_efficiency=0.90)
        return solve_phase(model)

    low, high = 1e-10, 1e-2
    for _ in range(200):
        guess = math.sqrt(low * high)
        if run(guess, "slide_working_feed").velocity_m_s < 0.5 / 60:
            low = guess
        else:
            high = guess
    k = math.sqrt(low * high)

    approach = run(k, "slide_rapid_approach")
    feed = run(k, "slide_working_feed")
    assert approach.velocity_m_s * 60 == pytest.approx(1.500, abs=0.01)
    assert approach.regime == "flow_limited"
    assert approach.pump_pressure_pa / 1e5 == pytest.approx(15.38, abs=0.1)
    assert feed.velocity_m_s * 60 == pytest.approx(0.500, abs=0.01)
    assert feed.regime == "pressure_limited"
    assert feed.pump_pressure_pa / 1e5 == pytest.approx(19.0, abs=0.01)


def test_a_load_too_large_for_the_relief_is_infeasible(problems) -> None:
    entry = problems["P7-04"]
    sizing = {
        "SlideCylinder": {"cap_area_m2": area_of_bore(40).value,
                          "annulus_area_m2": annulus_area(40, 22).value},
        "DCV": {"rated_flow_lpm": 60.0}, "RodEndMeterOutFlowControl": {"rated_flow_lpm": 60.0},
        "__supply__": {"flow_m3s": Q.of(11.78, "l/min").value, "relief_pa": 20e5},
    }
    model = build_phase_model(entry["topology"], entry["requirements"], "slide_working_feed",
                              sizing, throttle_k={"RodEndMeterOutFlowControl": 1e-7})
    point = solve_phase(model)
    assert not point.feasible
    assert "relief" in point.reason


# ---------------------------------------------------------------------------
# Certified paths come from validation, not from a second opinion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_topologies_still_validate(problems, problem_id) -> None:
    entry = problems[problem_id]
    report = validate_topology(entry["topology"], entry["requirements"])
    assert report.verdict == "valid", [i.model_dump(mode="json") for i in report.issues]


def test_flow_paths_prefer_an_open_bypass(problems) -> None:
    """A phase with its bypass open is unrestricted, as the validator agrees."""
    entry = problems["P7-01"]
    paths = phase_flow_paths(entry["topology"], entry["requirements"], "slide_approach_extend")
    assert paths is not None
    info = next(iter(paths["cylinders"].values()))
    assert info["exhaust_metered"] is False


def test_flow_paths_are_metered_when_the_bypass_is_shut(problems) -> None:
    entry = problems["P7-01"]
    paths = phase_flow_paths(entry["topology"], entry["requirements"], "slide_cutting_feed_extend")
    info = next(iter(paths["cylinders"].values()))
    assert info["exhaust_metered"] is True


def test_hold_phases_have_no_flow_model(problems) -> None:
    entry = problems["P7-05"]
    assert phase_flow_paths(entry["topology"], entry["requirements"],
                            "clamp_hold_during_drilling") is None


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------


def test_monotone_function_is_bounded_exactly() -> None:
    envelope = Envelope().add("x", 1.0, 0.1)
    enclosure = bound_outcome(lambda p: 2.0 * p["x"], envelope)
    assert enclosure.proved
    assert enclosure.interval.lo == pytest.approx(1.8)
    assert enclosure.interval.hi == pytest.approx(2.2)


def test_non_monotone_function_is_not_claimed_as_proved() -> None:
    """A peak inside the box is invisible to the corners, so nothing is proved."""
    envelope = Envelope().add("x", 1.0, 1.0)          # x in [0, 2]
    enclosure = bound_outcome(lambda p: -((p["x"] - 1.0) ** 2), envelope)
    assert not enclosure.proved
    assert enclosure.method == "corner+sampled"
    # Both corners give -1, so a corner-only hull would be the single point
    # [-1, -1]. Sampling has to have widened it toward the interior maximum -
    # that widening is exactly what stops the method from over-claiming.
    assert enclosure.interval.hi > -1.0


def test_infeasible_corner_refutes() -> None:
    envelope = Envelope().add("x", 1.0, 0.1)
    enclosure = bound_outcome(lambda p: None if p["x"] > 1.05 else 1.0, envelope)
    assert enclosure.infeasible_points
    assert verdict_for(enclosure, 0.0, 2.0) == "REFUTED"


@pytest.mark.parametrize(
    "interval,expected",
    [((0.48, 0.52), "PROVED"), ((0.40, 0.52), "UNDECIDED"), ((0.1, 0.2), "REFUTED")],
)
def test_verdicts(interval, expected) -> None:
    enclosure = Enclosure(Interval(*interval), "monotone-corner", True)
    assert verdict_for(enclosure, 0.45, 0.55) == expected


def test_standard_envelope_is_deterministic() -> None:
    first = standard_envelope(0.15).interior_samples()
    second = standard_envelope(0.15).interior_samples()
    assert first == second, "a certificate that changes between runs is not a certificate"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_every_problem_sizes_without_error(problems, problem_id) -> None:
    entry = problems[problem_id]
    result = size_problem(problem_id, entry["topology"], entry["requirements"])
    assert result.certificate is not None
    assert result.certificate.certificates
    supply = result.sizing["__supply__"]
    for key in ("displacement_cm3", "relief_pa", "motor_kw", "reservoir_l"):
        assert supply.get(key), f"{problem_id} left {key} unsized"
    for record in result.sizing.values():
        if "bore_mm" in record:
            assert record["bore_mm"] in BORE_SERIES
            assert record["rod_mm"] < record["bore_mm"]


@pytest.mark.parametrize("problem_id", ["P7-02", "P7-03", "P7-05", "P7-06"])
def test_these_problems_certify_clean(problems, problem_id) -> None:
    entry = problems[problem_id]
    result = size_problem(problem_id, entry["topology"], entry["requirements"])
    assert result.certificate.verdict == "PROVED", [
        c.to_dict() for c in result.certificate.certificates if c.verdict != "PROVED"
    ]


def test_p7_04_speed_ratio_is_sensitive_to_the_relief_setting(problems) -> None:
    """P7-04 defeats the deterministic arm's *fixed relief policy*, not the bores.

    When both throttled operating points sit at the relief, the cap pressure is
    common to both and the ratio is

        r = sqrt( (p_relief*A_cap - F_fast/eta) / (p_relief*A_cap - F_slow/eta) )

    which rises steeply as the relief approaches the slow phase's stall
    pressure. The deterministic arm clamps the relief to a fixed margin over the
    worst load and so lands on the flat part of that curve; the LLM arm, free to
    choose the margin, reaches 3:1 at 19.44 bar with a 100/70 cylinder. The
    diagnosis must therefore name the setting, not declare the requirement
    unreachable.
    """
    entry = problems["P7-04"]
    result = size_problem("P7-04", entry["topology"], entry["requirements"])
    # Searching the setting instead of clamping it turns a refutation into an
    # open question: five of the six criteria now certify, and the one that does
    # not is the feed speed, whose enclosure is wide precisely because the
    # operating point sits close to the relief.
    assert result.certificate.verdict == "UNDECIDED"
    assert result.certificate.counts()["REFUTED"] == 0
    feed = next(item for item in result.certificate.certificates
                if item.criterion_id == "slide_working_feed__velocity")
    assert feed.verdict == "UNDECIDED"
    assert any("relief searched over" in note for note in result.notes)
    assert any("governed by how close the relief sits" in note for note in result.notes)


def test_certificates_carry_their_reasoning(problems) -> None:
    entry = problems["P7-02"]
    result = size_problem("P7-02", entry["topology"], entry["requirements"])
    for certificate in result.certificate.certificates:
        assert certificate.required and certificate.achieved
        assert certificate.method
        assert certificate.verdict in {"PROVED", "UNDECIDED", "REFUTED"}
    payload = result.certificate.to_dict()
    assert payload["problem_id"] == "P7-02"
    assert payload["criteria"] and payload["counts"]


# ---------------------------------------------------------------------------
# E1 - the reference audit as ground truth
# ---------------------------------------------------------------------------
#
# The defects below were found by hand, before this verifier existed. Asking it
# to rediscover them is the strongest evidence available that it works, because
# the labels were not derived from it.


import sys as _sys

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "reference"))
from pdf_reference_sizings import PDF_SIZINGS  # noqa: E402


def _certify_pdf(problem_id: str, problems: dict):
    from hydraulic_mas.sizing.certify import certify
    from hydraulic_mas.sizing.planner import _load_tolerance

    sizing, _, _ = PDF_SIZINGS[problem_id]
    entry = problems[problem_id]
    full = dict(sizing)
    for component in entry["topology"]["components"]:
        component_id = str(component["id"])
        full.setdefault(component_id, {}).setdefault("rated_flow_lpm", 60.0)
        if component.get("comp_type") == "pressure_reducing_valve":
            full[component_id]["setting_pa"] = 40e5
    contract = compile_contract(entry["requirements"], problem_id)
    return certify(problem_id, entry["topology"], entry["requirements"], contract, full, {},
                   standard_envelope(_load_tolerance(entry["requirements"])))


@pytest.mark.parametrize(
    "problem_id", ["P7-01", "P7-02", "P7-04", "P7-05", "P7-06", "P7-07"]
)
def test_reference_defects_are_rediscovered(problems, problem_id) -> None:
    certificate = _certify_pdf(problem_id, problems)
    assert certificate.verdict != "PROVED", (
        f"{problem_id} carries a defect the audit recorded, but nothing flagged it"
    )


def test_reference_defect_is_found_by_the_expected_layer(problems) -> None:
    """Each defect should be caught by the physics it actually violates."""
    expectations = {
        "P7-01": "V6",   # prime mover on the wrong phase
        "P7-04": "V6",   # hydraulic power quoted as shaft power
        "P7-05": "V4",   # drill cannot move inside the ceiling
        "P7-06": "V4",   # relief opens before the load is reached
    }
    for problem_id, layer in expectations.items():
        certificate = _certify_pdf(problem_id, problems)
        layers = {finding.layer for finding in certificate.findings}
        assert layer in layers, f"{problem_id}: expected a {layer} finding, got {sorted(layers)}"


def test_p7_07_branch_ceiling_is_the_reported_reason(problems) -> None:
    certificate = _certify_pdf("P7-07", problems)
    ceilings = [
        item for item in certificate.certificates
        if item.kind == "pressure_ceiling" and item.verdict == "REFUTED"
    ]
    assert ceilings, "the clamp branch exceeds 40 bar and that should be the stated reason"


def test_p7_03_is_not_falsely_flagged(problems) -> None:
    """A verifier that flags everything is worth nothing.

    The audit's P7-03 finding was that the *prose* quoted hydraulic power as
    shaft power. The motor actually specified, 1.5 kW, does cover the 1.45 kW the
    design needs, so the design itself is sound and must certify clean.
    """
    certificate = _certify_pdf("P7-03", problems)
    assert certificate.verdict == "PROVED", [
        item.to_dict() for item in certificate.certificates if item.verdict != "PROVED"
    ]
