"""The transient cross-check: what the quasi-static assumption costs.

The point of this file is not to show the two models agree. It is to establish
that the comparison is capable of showing they *don't*, and to pin the two
invented parameters the integration needed so that a settling time quoted in the
paper carries its own sensitivity with it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.sizing.planner import size_problem
from hydraulic_mas.sizing.transient import (
    cross_check,
    integrate_phase,
    sensitivity_to_line_length,
    sensitivity_to_moving_mass,
    summarise_cross_check,
)


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


@pytest.fixture(scope="module")
def designs(problems) -> dict:
    return {
        problem_id: (entry, size_problem(problem_id, entry["topology"], entry["requirements"]))
        for problem_id, entry in problems.items()
    }


@pytest.fixture(scope="module")
def all_results(designs) -> list:
    results = []
    for entry, planned in designs.values():
        results.extend(cross_check(entry["topology"], entry["requirements"],
                                   planned.sizing, planned.throttle_k))
    return results


def test_every_motion_phase_integrates_without_diverging(all_results) -> None:
    assert len(all_results) >= 20
    diverged = [item.phase_id for item in all_results if item.diverged]
    assert not diverged, diverged


def test_every_phase_reaches_a_steady_velocity(all_results) -> None:
    """If a phase never settles, its reported velocity is not a settled one.

    Reporting it as though it were - which the first version of this module did,
    by stopping when the integration got close to the quasi-static answer - would
    manufacture the agreement the study exists to test.
    """
    unsettled = [item.phase_id for item in all_results if item.notes]
    assert not unsettled, unsettled


def test_the_settled_velocity_agrees_with_the_quasi_static_solve(all_results) -> None:
    """The headline number the paper quotes for this study."""
    summary = summarise_cross_check(all_results)
    assert summary["max_abs_velocity_error"] < 0.05
    assert summary["median_abs_velocity_error"] < 0.01


def test_settling_is_detected_from_the_dynamics_not_from_the_answer() -> None:
    """The property that makes the agreement above mean anything.

    Settling must be found from the acceleration alone. A criterion phrased as
    "close to the quasi-static velocity" would stop the integration at the answer
    it is checking, and every problem would agree perfectly by construction.
    """
    import inspect

    from hydraulic_mas.sizing import transient

    source = inspect.getsource(transient._integrate)
    # The settling test itself: from where the slope is measured to where the
    # loop ends. What happens after the loop may of course mention the steady
    # solve, because that is what gets compared.
    settling_block = source[source.index("slope = abs("):source.index("stroke = _stroke_of")]
    assert "target" not in settling_block
    assert "steady" not in settling_block
    # And the loop must not carry a target to compare against at all.
    loop = source[source.index("while time < max_time_s"):source.index("stroke = _stroke_of")]
    assert "steady." not in loop


def test_the_study_names_the_phases_its_assumption_does_not_cover(all_results) -> None:
    """Where the acceleration spans the stroke, a steady criterion is the wrong test."""
    summary = summarise_cross_check(all_results)
    assert "clamp_extend_apply_force" in summary["phases_never_reaching_steady_state"]
    assert summary["median_stroke_fraction_settling"] < 0.10


def test_the_settled_velocity_is_robust_to_the_invented_line_length(designs) -> None:
    entry, planned = designs["P7-07"]
    rows = sensitivity_to_line_length(
        entry["topology"], entry["requirements"], "clamp_extend_apply_force",
        planned.sizing, planned.throttle_k)
    assert len(rows) == 3
    assert max(abs(row["velocity_error"]) for row in rows) < 0.02


def test_the_settled_velocity_is_robust_to_the_invented_mass(designs) -> None:
    """The number the paper quotes must not depend on the parameter it had to guess.

    The briefs give loads, not masses. The settling *time* scales with whatever
    mass is assumed and is therefore quoted only with this sweep beside it; the
    settled *velocity* does not move at all, which is what makes the agreement
    claim safe to state on its own.
    """
    entry, planned = designs["P7-07"]
    rows = sensitivity_to_moving_mass(
        entry["topology"], entry["requirements"], "clamp_extend_apply_force",
        planned.sizing, planned.throttle_k)
    assert len(rows) == 5
    assert max(abs(row["velocity_error"]) for row in rows) < 0.02
    # ... while the settling time is essentially proportional to it, which is why
    # "mostly transient" is a hedged finding rather than a hard one.
    times = [row["settling_time_s"] for row in rows]
    assert times == sorted(times)
    assert times[-1] > 5 * times[0]


def test_a_deliberately_slow_circuit_shows_up_as_disagreement(designs) -> None:
    """The comparison must be able to fail, or the agreement is not evidence.

    Halving the pump halves the steady velocity. If the integration did not
    follow, the two models would not be looking at the same circuit.
    """
    entry, planned = designs["P7-03"]
    sizing = json.loads(json.dumps(planned.sizing))
    baseline = integrate_phase(entry["topology"], entry["requirements"], "carriage_advance",
                               sizing, planned.throttle_k)
    sizing["__supply__"]["flow_m3s"] *= 0.5
    halved = integrate_phase(entry["topology"], entry["requirements"], "carriage_advance",
                             sizing, planned.throttle_k)
    assert baseline is not None and halved is not None
    assert halved.settled_velocity_m_s < 0.6 * baseline.settled_velocity_m_s
    # And it still tracks its own quasi-static solve, which is the actual claim.
    assert abs(halved.velocity_error) < 0.05


def test_the_transient_layer_is_off_by_default(designs) -> None:
    """It integrates. The quasi-static battery exists precisely so it need not."""
    _entry, planned = designs["P7-06"]
    codes = {finding.code for finding in planned.certificate.findings}
    assert "PHASE_MOSTLY_TRANSIENT" not in codes


def test_the_transient_layer_reports_when_asked(designs) -> None:
    from hydraulic_mas.sizing.certify import certify
    from hydraulic_mas.sizing.contract import compile_contract
    from hydraulic_mas.sizing.intervals import standard_envelope
    from hydraulic_mas.sizing.planner import _load_tolerance

    entry, planned = designs["P7-06"]
    contract = compile_contract(entry["requirements"], "P7-06")
    certificate = certify(
        "P7-06", entry["topology"], entry["requirements"], contract, planned.sizing,
        planned.throttle_k, standard_envelope(_load_tolerance(entry["requirements"])),
        transient_check=True)
    codes = {finding.code for finding in certificate.findings}
    assert "PHASE_MOSTLY_TRANSIENT" in codes
    # A warning, not an error: the design may be entirely sound.
    assert all(finding.severity == "warning" for finding in certificate.findings
               if finding.code == "PHASE_MOSTLY_TRANSIENT")
    assert certificate.verdict == planned.certificate.verdict
