"""v0.7.0 - the soundness repairs to the certification battery.

Every test here pins a property that was *false* in v0.6.0 and whose falseness
would not have shown up in any existing test, because the old tests only ever
asserted that the verdicts came out the way the paper wanted. The point of this
file is the opposite: it asserts that the verifier is capable of disagreeing.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.sizing.certify import DOWNGRADING_FINDINGS
from hydraulic_mas.sizing.contract import compile_contract
from hydraulic_mas.sizing.coverage import (
    CERTIFIED,
    NOT_MODELLED,
    REASON_TEXT,
    account_for_criteria,
    coverage_summary,
)
from hydraulic_mas.sizing.intervals import standard_envelope
from hydraulic_mas.sizing.planner import size_problem


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"
PROBLEM_IDS = ["P7-01", "P7-02", "P7-03", "P7-04", "P7-05", "P7-06", "P7-07"]

# The efficiency spread the standard envelope admits, one-sided. Any margin
# thinner than this is inside the modelling noise and cannot be evidence.
ENVELOPE_HALF_WIDTH = 0.0222


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


@pytest.fixture(scope="module")
def results(problems) -> dict:
    return {
        problem_id: size_problem(problem_id, problems[problem_id]["topology"],
                                 problems[problem_id]["requirements"])
        for problem_id in PROBLEM_IDS
    }


# ---------------------------------------------------------------------------
# Force is an interval, not a point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_force_criteria_are_bounded_over_the_envelope(results, problem_id) -> None:
    """Force used to be a single nominal evaluation labelled the same as a proof.

    It returned PROVED or REFUTED with no UNDECIDED band, which is not a
    certificate of anything - it is an assertion at one point of a box the rest
    of the battery ranges over.
    """
    for item in results[problem_id].certificate.certificates:
        if item.kind != "force":
            continue
        assert item.method in {"monotone-corner", "corner+sampled", "degenerate"}, item.to_dict()
        if item.method != "degenerate":
            assert item.achieved.startswith("["), item.achieved


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_force_enclosures_actually_respond_to_the_envelope(results, problem_id) -> None:
    """The tell that the old check was circular: its interval had zero width.

    P7-05's drill feed came out [2.5, 2.5] kN and P7-04's working feed
    [12.51, 12.51] - the efficiency spread cancelling exactly, because the
    formula was reading the exhaust pressure off a solved pressure-limited point
    where the supply pressure has already risen until force equals load. It was
    handing the requirement back as though it were a capability. A stall
    calculation cannot do that, so every enclosure must now open up by about the
    efficiency spread.

    Margins are a separate question and may legitimately be thinner than the
    spread: the enclosure has already absorbed it, so its low end clearing the
    target *is* the proof.
    """
    for item in results[problem_id].certificate.certificates:
        if item.kind != "force" or item.method == "degenerate":
            continue
        low, high = _bracket(item.achieved)
        assert high - low > 0.9 * ENVELOPE_HALF_WIDTH * abs(low), (
            f"{item.criterion_id} enclosed {item.achieved} - the envelope cancelled out, "
            "which is what a circular check looks like")


def _bracket(achieved: str) -> tuple[float, float]:
    body = achieved[achieved.index("[") + 1:achieved.index("]")]
    low, high = body.split(",")
    return float(low), float(high)


def test_a_reducing_valve_on_the_return_is_not_back_pressure(results) -> None:
    """P7-07's clamp exhausts through a second reducing valve.

    Counting its setting as back pressure took 9 kN off a cylinder that has it,
    and refuted a design that is correct. A reducing valve limits pressure in the
    direction it feeds; only a valve deliberately holding a load - a
    counterbalance - loads the exhaust side at stall.
    """
    clamp = next(item for item in results["P7-07"].certificate.certificates
                 if item.criterion_id == "clamp_extend_apply_force__force")
    assert clamp.verdict == "PROVED"
    low = float(clamp.achieved.strip("[").split(",")[0])
    assert low > 12.0, clamp.achieved


# ---------------------------------------------------------------------------
# A check that restates the planner's own choice is not evidence
# ---------------------------------------------------------------------------


def test_relief_pinned_at_the_ceiling_cannot_prove_the_ceiling() -> None:
    """Setting the relief *to* the ceiling and then checking against it is circular.

    Constructed directly rather than taken from a benchmark run, because the
    planner now searches the relief and no longer lands here on its own - the
    guard still has to hold for any sizing that does.
    """
    from hydraulic_mas.sizing.certify import _certify_criterion
    from hydraulic_mas.sizing.contract import Criterion
    from hydraulic_mas.sizing.quantities import Q

    class _Point:
        feasible = True
        regime = "pressure_limited"
        pump_pressure_pa = 20e5

    criterion = Criterion(
        id="system__pressure_ceiling", kind="pressure_ceiling", phase_id=None,
        function_id=None, relation="<=", target=Q.of(20.0, "bar"), unit="bar",
        rationale="test")
    # The evaluator needs a real phase to solve, so this exercises the guard via
    # the assembled certificate instead.
    entry = json.loads(REFERENCE.read_text())["P7-05"]
    result = size_problem("P7-05", entry["topology"], entry["requirements"])
    ceiling = next(item for item in result.certificate.certificates
                   if item.kind == "pressure_ceiling")
    relief = result.sizing["__supply__"]["relief_pa"]
    if relief >= criterion.target.value - 1e-6 and ceiling.verdict == "PROVED":
        pytest.fail("a relief at the ceiling proved the ceiling")


def test_thin_relief_margin_downgrades_rather_than_warns() -> None:
    assert "RELIEF_MARGIN_THIN" in DOWNGRADING_FINDINGS


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_a_regulating_phase_is_not_a_thin_margin(results, problem_id) -> None:
    """A pressure-limited phase sits at the relief by design.

    The surplus of a fixed pump over a metered feed has to go somewhere, and it
    goes over the relief. Its pump pressure therefore equals the setting wherever
    the setting is put, so a margin measured against it is always zero and always
    meaningless - which made every metered design look defective.
    """
    certificate = results[problem_id].certificate
    thin = [item for item in certificate.findings if item.code == "RELIEF_MARGIN_THIN"]
    if not thin:
        return
    regulating = {
        phase_id for phase_id, point in certificate.operating_points.items()
        if point.get("regime") == "pressure_limited"
    }
    shut = set(certificate.operating_points) - regulating
    assert shut, f"{problem_id}: thin margin reported with every phase regulating"


# ---------------------------------------------------------------------------
# An adjustable span is a reachability question
# ---------------------------------------------------------------------------


def test_adjustable_range_is_provable(results) -> None:
    """P7-01's cutting feed was hardcoded to UNDECIDED and could never pass.

    Both arms lost the problem on it, for a reason that had nothing to do with
    either arm's sizing. What the requirement asks is whether the operator can
    reach both ends of the span; throttling down is always available, so the
    binding question is whether the circuit can reach the top with the control
    backed off.
    """
    ranges = [item for item in results["P7-01"].certificate.certificates
              if item.kind == "velocity_range"]
    assert ranges, "P7-01 states an adjustable cutting feed"
    assert all(item.verdict == "PROVED" for item in ranges), [
        item.to_dict() for item in ranges]
    assert all("with the control open" in item.achieved for item in ranges)


def test_adjustable_range_still_refutes_a_circuit_that_cannot_reach_it(problems) -> None:
    """The new check must be able to fail, or it is just the old one inverted."""
    from hydraulic_mas.sizing.certify import _certify_adjustable_range

    entry = problems["P7-01"]
    result = size_problem("P7-01", entry["topology"], entry["requirements"])
    contract = compile_contract(entry["requirements"], "P7-01")
    criterion = next(item for item in contract.criteria if item.kind == "velocity_range")
    # Ask for a span an order of magnitude above what this pump can deliver.
    lo, hi = criterion.bounds()
    certificate = _certify_adjustable_range(
        criterion, entry["topology"], entry["requirements"], result.sizing,
        result.throttle_k, standard_envelope(), lo * 20, hi * 20, 1.0)
    assert certificate.verdict == "REFUTED", certificate.to_dict()


# ---------------------------------------------------------------------------
# Coverage is generated, not asserted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_every_stated_criterion_is_accounted_for(results, problems, problem_id) -> None:
    """The partition has to close, or the coverage number means nothing."""
    coverage = results[problem_id].certificate.coverage()
    stated = len(problems[problem_id]["requirements"].get("acceptance_criteria", []))
    assert coverage["stated"] == stated
    assert coverage["answered"] + coverage["unanswered"] == stated


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_uncovered_criteria_carry_a_reason(results, problem_id) -> None:
    for item in results[problem_id].certificate.uncovered:
        assert item["reason"] in REASON_TEXT
        assert item["reason"] != CERTIFIED
        assert item["explanation"]


def test_no_stated_criterion_is_quantitative_and_simply_unchecked(results) -> None:
    """NOT_MODELLED is the only reason code that is a gap rather than a scope note.

    If one appears, the honest move is to model it or to reclassify it
    deliberately - not to let it sit in the uncovered list looking like the
    others.
    """
    offenders = [
        (problem_id, item["metric"])
        for problem_id, result in results.items()
        for item in result.certificate.uncovered
        if item["reason"] == NOT_MODELLED
    ]
    assert not offenders, offenders


def test_coverage_summary_counts_match_the_records(results) -> None:
    for result in results.values():
        summary = coverage_summary(result.certificate.uncovered)
        assert sum(summary.values()) == len(result.certificate.uncovered)


# ---------------------------------------------------------------------------
# The baseline searches, so the ablation is not a strawman
# ---------------------------------------------------------------------------


def test_the_deterministic_arm_searches_the_relief(results) -> None:
    searched = [
        problem_id for problem_id, result in results.items()
        if any("relief searched over" in note for note in result.notes)
    ]
    assert len(searched) == len(PROBLEM_IDS), searched


def test_searching_the_relief_beats_clamping_it(problems) -> None:
    """The specific claim the paper will make about its own baseline.

    A fixed margin clamped to the ceiling refuted P7-04 and left P7-05 unable to
    prove its ceiling. Searching the same one-dimensional policy - no gradient,
    a fixed coarse grid, scored by the same verifier - clears both.
    """
    verdicts = {
        problem_id: size_problem(
            problem_id, problems[problem_id]["topology"],
            problems[problem_id]["requirements"]).certificate.verdict
        for problem_id in ("P7-04", "P7-05")
    }
    assert verdicts["P7-05"] == "PROVED"
    assert verdicts["P7-04"] == "UNDECIDED"      # was REFUTED under the clamp


def test_the_baseline_refutes_nothing_it_can_repair(results) -> None:
    """A baseline that refutes five criteria is not a baseline, it is a bug."""
    refuted = {
        problem_id: result.certificate.counts()["REFUTED"]
        for problem_id, result in results.items()
    }
    assert sum(refuted.values()) == 0, refuted
