"""The evidence generator: every table in the paper, regenerated from data.

The failure mode being guarded against is a summary that no longer matches the
run it came from - a number copied into a draft, the code changed, and the two
quietly disagreeing for a year. So the tables are generated, the generator is
tested, and the tests assert the properties the paper's claims rest on rather
than the values themselves, which are free to move as the system improves.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.ablation.report import (
    arm_report,
    build_evidence,
    coverage_report,

    render_markdown,

)
from hydraulic_mas.ablation.runner import Suite, run_sweep
from hydraulic_mas.ablation.oracle import direct_from_sizing, perturb_bore
from hydraulic_mas.sizing.planner import size_problem


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


@pytest.fixture(scope="module")
def coverage(problems) -> dict:
    return coverage_report(problems)


@pytest.fixture(scope="module")
def evidence(problems, tmp_path_factory) -> tuple[dict, pathlib.Path]:
    """Built once. The transient tables integrate, which is not free."""
    directory = tmp_path_factory.mktemp("evidence")
    return build_evidence(problems, directory), directory


def test_the_coverage_partition_closes(coverage) -> None:
    totals = coverage["totals"]
    assert totals["answered"] + totals["unanswered"] == totals["stated"]
    assert totals["stated"] == 73
    assert sum(coverage["uncovered_reasons"].values()) == totals["unanswered"]


def test_every_uncovered_reason_has_a_gloss(coverage) -> None:
    for reason in coverage["uncovered_reasons"]:
        assert coverage["reason_glossary"][reason]


def test_every_check_now_uses_an_interval_method(coverage) -> None:
    """The soundness repair, stated as a number the paper can quote.

    In v0.6.0 this was 34 percent: force, hold and pressure-ceiling criteria were
    nominal point evaluations wearing the same PROVED label as the enclosures.
    """
    assert coverage["interval_fraction"] == 1.0, coverage["methods"]
    assert "worst-phase-nominal" not in coverage["methods"]
    assert "static-force-balance" not in coverage["methods"]


def test_the_coverage_fraction_is_reported_not_assumed(coverage) -> None:
    assert 0.5 < coverage["answered_fraction"] < 0.7
    assert coverage["answered_fraction"] == pytest.approx(
        coverage["totals"]["answered"] / coverage["totals"]["stated"])


def test_the_defect_report_carries_its_false_positive_rate(evidence) -> None:
    report = evidence[0]["T4_T5_seeded_defects"]
    assert report["table"]["null"]["false_positive_rate"] == 0.0
    assert report["curve"], "the detection curve is what characterises the detector"
    assert report["outcomes"]


def test_the_transient_report_states_where_its_assumption_fails(evidence, problems) -> None:
    report = evidence[0]["T6_transient"]
    overall = report["overall"]
    assert overall["max_abs_velocity_error"] < 0.05
    assert overall["phases_never_reaching_steady_state"], (
        "a scope limitation that is never reported is not a scope limitation")
    assert set(report["per_problem"]) == set(problems)


def test_arm_tables_are_built_from_records(problems) -> None:
    from hydraulic_mas.ablation.schemas import DirectSizing

    entry = problems["P7-06"]
    planned = size_problem("P7-06", entry["topology"], entry["requirements"])
    good = direct_from_sizing(planned.sizing, entry["topology"], planned.throttle_k,
                              planned.certificate.to_dict(), entry["requirements"])

    def factory(seed: int):
        proposal = good if seed % 2 == 0 else perturb_bore(good, -1)

        def client(system: str, prompt: str, tools: list) -> DirectSizing:
            return proposal
        return client

    suite = Suite(problems=problems, problem_ids=["P7-06"])
    records = run_sweep(suite, arms=("A0", "A2"), seeds=(0, 1),
                        direct_client_factory=factory)
    report = arm_report(records)
    assert set(report["verdicts"]) == {"A0", "A2"}
    assert report["verdicts"]["A0"]["proved_rate"] == pytest.approx(0.5)
    assert "A0" in report["errors"]["prediction_error"]
    assert "unreachable" in report


def test_build_evidence_writes_every_table(evidence) -> None:
    evidence, tmp_path = evidence
    assert {"T1_coverage", "T4_T5_seeded_defects", "T6_transient"} <= set(evidence)
    written = {path.name for path in tmp_path.iterdir()}
    assert "evidence.md" in written
    for name in evidence:
        assert f"{name}.json" in written
    # Every written table must be loadable: a report that only renders is a
    # report that cannot be re-analysed.
    for name in evidence:
        json.loads((tmp_path / f"{name}.json").read_text())


def test_the_markdown_reports_the_numbers_the_paper_quotes(evidence) -> None:
    text = render_markdown(evidence[0])
    assert "Interval methods account for 100% of checks" in text
    assert "False-positive rate" in text
    assert "Detection against defect magnitude" in text
    assert "never reaching steady state" in text
    assert "**73**" in text


def test_arm_tables_are_omitted_rather_than_faked(evidence) -> None:
    """No model run, no arm table. Not an empty one, and certainly not a default."""
    assert "T2_T3_arms" not in evidence[0]
    assert "## T2" not in render_markdown(evidence[0])
