"""The ablation harness, exercised without a model.

An experiment harness that can only be tested by running the experiment is one
nobody tests. Every arm here is driven by a stub whose answer is known in
advance, so three things can be checked that a live run never could:

* the direct path is *faithful* - a certified design fed back through it
  reproduces its own certificate, so a difference measured between arms is a
  difference in the designs and not an artefact of how they are installed;
* the harness can record failure - a deliberately undersized design must be
  refuted, or a pass rate means nothing;
* the metrics detect what they claim to - a design whose numbers are right and
  whose stated arithmetic is wrong must show up in the prediction error and
  nowhere else.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.ablation import (
    ARM_DESCRIPTION,
    ARMS,
    catalog_violation_rate,
    error_table,
    load_records,
    prediction_errors,
    requirement_errors,
    run_cell,
    run_sweep,
    verdict_rates,
)
from hydraulic_mas.ablation.apply_direct import apply_direct
from hydraulic_mas.ablation.oracle import direct_from_sizing, mispredict, perturb_bore
from hydraulic_mas.ablation.runner import Suite, load_suite
from hydraulic_mas.ablation.schemas import DirectCylinder, DirectSizing
from hydraulic_mas.sizing.contract import compile_contract
from hydraulic_mas.sizing.planner import _load_tolerance, size_problem


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"
PROBLEM_IDS = ["P7-01", "P7-02", "P7-03", "P7-04", "P7-05", "P7-06", "P7-07"]


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(REFERENCE)


@pytest.fixture(scope="module")
def oracles(suite) -> dict[str, DirectSizing]:
    """The deterministic arm's own designs, restated as finished numbers."""
    built = {}
    for problem_id in PROBLEM_IDS:
        entry = suite.problems[problem_id]
        planned = size_problem(problem_id, entry["topology"], entry["requirements"])
        built[problem_id] = (
            direct_from_sizing(planned.sizing, entry["topology"], planned.throttle_k,
                               planned.certificate.to_dict(), entry["requirements"]),
            planned,
        )
    return built


def _stub(proposal: DirectSizing):
    def client(system: str, prompt: str, tools: list) -> DirectSizing:
        assert prompt, "the arm must be given the problem"
        return proposal
    return client


# ---------------------------------------------------------------------------
# Faithfulness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem_id", PROBLEM_IDS)
def test_the_direct_path_reproduces_the_certificate_it_was_built_from(
        suite, oracles, problem_id) -> None:
    proposal, planned = oracles[problem_id]
    entry = suite.problems[problem_id]
    contract = compile_contract(entry["requirements"], problem_id)
    applied = apply_direct(proposal, entry["topology"], entry["requirements"], contract,
                           _load_tolerance(entry["requirements"]))
    assert not applied.install_errors, applied.install_errors
    assert not applied.catalog_violations, applied.catalog_violations
    assert applied.certificate.verdict == planned.certificate.verdict
    assert applied.certificate.counts() == planned.certificate.counts()


def test_a_direct_arm_records_the_verdict_the_verifier_gives(suite, oracles) -> None:
    proposal, planned = oracles["P7-03"]
    result = run_cell("A0", suite, "P7-03", 0, direct_client_factory=lambda seed: _stub(proposal))
    assert result.arm == "A0"
    assert result.verdict == planned.certificate.verdict == "PROVED"
    assert result.coverage["stated"] == 9
    assert result.oversizing_index is not None


# ---------------------------------------------------------------------------
# The harness can record failure
# ---------------------------------------------------------------------------


def test_an_undersized_design_is_refuted_not_repaired(suite, oracles) -> None:
    """Nothing in the direct path may quietly fix a bad number.

    This is the property that makes A0 a control at all: if the installer rounded
    a bore back onto the series or lifted a relief to clear a load, the arm would
    be measuring the installer.
    """
    proposal, _ = oracles["P7-06"]
    result = run_cell("A0", suite, "P7-06", 0,
                      direct_client_factory=lambda seed: _stub(perturb_bore(proposal, -2)))
    assert result.verdict == "REFUTED", result.certificate.get("counts")
    assert result.counts["REFUTED"] >= 1


def test_a_bore_off_the_standard_series_is_reported_not_rounded(suite, oracles) -> None:
    proposal, _ = oracles["P7-03"]
    off_series = proposal.model_copy(deep=True)
    off_series.cylinders = [
        DirectCylinder(cylinder_id=item.cylinder_id, bore_mm=item.bore_mm + 3.0,
                       rod_mm=item.rod_mm)
        for item in off_series.cylinders
    ]
    result = run_cell("A0", suite, "P7-03", 0,
                      direct_client_factory=lambda seed: _stub(off_series))
    assert result.catalog_violations
    assert any("not on the standard series" in item for item in result.catalog_violations)
    installed = result.sizing[off_series.cylinders[0].cylinder_id]["bore_mm"]
    assert installed == off_series.cylinders[0].bore_mm


def test_an_uninstallable_design_counts_as_a_result_not_a_crash(suite, oracles) -> None:
    proposal, _ = oracles["P7-03"]
    broken = proposal.model_copy(deep=True)
    broken.cylinders[0].rod_mm = broken.cylinders[0].bore_mm + 10.0
    result = run_cell("A0", suite, "P7-03", 0,
                      direct_client_factory=lambda seed: _stub(broken))
    assert result.verdict == "REFUTED"
    assert result.errors


def test_a_model_that_raises_is_a_data_point(suite) -> None:
    def exploding(seed):
        def client(system, prompt, tools):
            raise RuntimeError("the model refused")
        return client

    result = run_cell("A0", suite, "P7-03", 0, direct_client_factory=exploding)
    assert result.verdict == "ERROR"
    assert "the model refused" in result.errors[0]


# ---------------------------------------------------------------------------
# The metrics measure what they say
# ---------------------------------------------------------------------------


def test_prediction_error_isolates_arithmetic_from_design_quality(suite, oracles) -> None:
    """Same design, wrong stated arithmetic. Only one metric may move."""
    proposal, _ = oracles["P7-02"]
    honest = run_cell("A0", suite, "P7-02", 0,
                      direct_client_factory=lambda seed: _stub(proposal)).to_dict()
    boastful = run_cell("A0", suite, "P7-02", 1,
                        direct_client_factory=lambda seed: _stub(mispredict(proposal, 1.25))
                        ).to_dict()

    assert honest["verdict"] == boastful["verdict"], "the design did not change"
    honest_prediction = [row["relative_error"] for row in prediction_errors(honest)]
    boastful_prediction = [row["relative_error"] for row in prediction_errors(boastful)]
    assert honest_prediction and boastful_prediction
    assert max(abs(value) for value in honest_prediction) < 1e-6
    assert all(abs(value - 0.25) < 1e-6 for value in boastful_prediction)

    # Design quality is untouched, because the design is identical.
    assert [row["relative_error"] for row in requirement_errors(honest)] == pytest.approx(
        [row["relative_error"] for row in requirement_errors(boastful)])


def test_requirement_error_moves_when_the_design_does(suite, oracles) -> None:
    proposal, _ = oracles["P7-06"]
    good = run_cell("A0", suite, "P7-06", 0,
                    direct_client_factory=lambda seed: _stub(proposal)).to_dict()
    # Enlarging rather than shrinking: a design two sizes down stops solving
    # altogether and drops out of the table, which is a different effect measured
    # by ``unreachable_rate`` below.
    large = run_cell("A0", suite, "P7-06", 1,
                     direct_client_factory=lambda seed: _stub(perturb_bore(proposal, +1))
                     ).to_dict()
    good_force = [row for row in requirement_errors(good) if row["quantity"] == "force"]
    large_force = [row for row in requirement_errors(large) if row["quantity"] == "force"]
    assert good_force and large_force
    assert min(row["relative_error"] for row in large_force) > min(
        row["relative_error"] for row in good_force)


def test_a_design_that_never_moves_is_counted_not_silently_dropped(suite, oracles) -> None:
    """The survivorship bias the error table would otherwise carry.

    A design so undersized that no phase solves produces no achieved values, so
    it contributes nothing to either error distribution. Left uncounted, an arm
    that fails catastrophically would look better in those columns than one that
    fails by ten percent.
    """
    from hydraulic_mas.ablation.metrics import unreachable_rate

    proposal, _ = oracles["P7-06"]
    dead = run_cell("A0", suite, "P7-06", 0,
                    direct_client_factory=lambda seed: _stub(perturb_bore(proposal, -2))
                    ).to_dict()
    assert dead["verdict"] == "REFUTED"
    assert requirement_errors(dead) == []
    rates = unreachable_rate([dead])
    assert rates["A0"]["runs_entirely_unreachable"] == 1
    assert rates["A0"]["mean_fraction_unreachable"] == pytest.approx(1.0)


def test_summaries_use_the_median_and_report_the_tail(suite, oracles) -> None:
    from hydraulic_mas.ablation.metrics import summarise

    summary = summarise([0.0, 0.01, 0.02, 5.0])
    assert summary["median"] == pytest.approx(0.015)
    assert summary["worst"] == 5.0
    assert summary["within_5pct"] == 0.75
    assert summary["mean_abs"] > summary["median"]


def test_summarise_handles_an_empty_arm() -> None:
    from hydraulic_mas.ablation.metrics import summarise

    assert summarise([]) == {"n": 0}


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_a_sweep_writes_every_cell_and_reloads_it(suite, oracles, tmp_path) -> None:
    small = Suite(problems=suite.problems, load_tolerance=suite.load_tolerance,
                  problem_ids=["P7-03", "P7-06"])
    out = tmp_path / "runs.jsonl"
    records = run_sweep(
        small, arms=("A0", "A2"), seeds=(0, 1),
        direct_client_factory=lambda seed: _stub(oracles["P7-03"][0] if seed == 0
                                                 else oracles["P7-06"][0]),
        out_path=out)
    # A0 runs both seeds on both problems; A2 is deterministic and runs once each.
    assert len(records) == 2 * 2 + 2
    assert load_records(out) == records


def test_the_deterministic_arm_is_not_run_repeatedly(suite, tmp_path) -> None:
    small = Suite(problems=suite.problems, load_tolerance=suite.load_tolerance,
                  problem_ids=["P7-03"])
    records = run_sweep(small, arms=("A2",), seeds=(0, 1, 2, 3))
    assert len(records) == 1, "repeating a deterministic arm measures nothing"


def test_verdict_rates_separate_within_problem_spread(suite, oracles) -> None:
    proposal, _ = oracles["P7-06"]
    small = Suite(problems=suite.problems, load_tolerance=suite.load_tolerance,
                  problem_ids=["P7-06"])
    # Alternate a good and a bad answer so the arm proves exactly half the time.
    def factory(seed):
        return _stub(proposal if seed % 2 == 0 else perturb_bore(proposal, -2))

    records = run_sweep(small, arms=("A0",), seeds=(0, 1, 2, 3),
                        direct_client_factory=factory)
    rates = verdict_rates(records)
    assert rates["A0"]["proved_rate"] == pytest.approx(0.5)
    assert rates["A0"]["mean_within_problem_sd"] == pytest.approx(0.5)


def test_error_and_violation_tables_key_by_arm(suite, oracles) -> None:
    proposal, _ = oracles["P7-03"]
    small = Suite(problems=suite.problems, load_tolerance=suite.load_tolerance,
                  problem_ids=["P7-03"])
    records = run_sweep(small, arms=("A0", "A2"), seeds=(0,),
                        direct_client_factory=lambda seed: _stub(proposal))
    table = error_table(records)
    assert "A0" in table["prediction_error"]
    # A1 and A2 never state a prediction, so they cannot have a prediction error.
    assert "A2" not in table["prediction_error"]
    assert "A2" in table["requirement_error"]
    assert catalog_violation_rate(records)["A0"] == 0.0


def test_every_arm_is_described(suite) -> None:
    assert set(ARMS) == set(ARM_DESCRIPTION)


def test_the_arithmetic_arm_never_gets_the_verifier_tool(suite, oracles) -> None:
    """A0.5 minus the verifier *is* the definition of A0.5.

    If ``evaluate_sizing_policy`` leaked into its tool list the arm would silently
    become A1 without the repair loop, and the ablation would be measuring two
    points that are much closer together than the paper says.
    """
    seen: list[str] = []
    proposal, _ = oracles["P7-03"]

    def recording(seed):
        def client(system, prompt, tools):
            seen.extend(getattr(tool, "name", "") for tool in tools)
            return proposal
        return client

    run_cell("A0.5", suite, "P7-03", 0, direct_client_factory=recording)
    assert seen, "A0.5 must be given arithmetic tools"
    assert "evaluate_sizing_policy" not in seen
    assert "choose_bore" in seen


def test_the_toolless_arm_gets_no_tools_at_all(suite, oracles) -> None:
    seen: list[list] = []
    proposal, _ = oracles["P7-03"]

    def recording(seed):
        def client(system, prompt, tools):
            seen.append(tools)
            return proposal
        return client

    run_cell("A0", suite, "P7-03", 0, direct_client_factory=recording)
    assert seen == [[]]


def test_the_catalog_is_in_the_prompt_so_a_failure_is_not_missing_information(suite) -> None:
    """A0 must fail for lack of calculation, never for lack of the series."""
    from hydraulic_mas.ablation.prompts import build_direct_prompt

    entry = suite.problems["P7-03"]
    contract = compile_contract(entry["requirements"], "P7-03")
    prompt = build_direct_prompt("design it", entry["topology"], entry["requirements"], contract)
    assert "ISO 3320" in prompt and "ISO 4395" in prompt
    assert "eta_m = 0.90" in prompt
    assert "p_cap * A_cap" in prompt
