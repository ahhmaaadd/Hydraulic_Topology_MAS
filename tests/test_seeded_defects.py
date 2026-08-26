"""E2 - the seeded-defect benchmark.

E1 asks whether the verifier rediscovers defects a person found first. E2 asks
the two questions E1 cannot: what magnitude of defect it starts to catch, and how
often it cries wolf. Both are needed. A detector characterised only by its hit
rate is not characterised.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.ablation.seeded_defects import (
    DEFECTS,
    detection_curve,
    detection_table,
    graded_defects,
    run_seeded_defects,
)


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"

# The one-sided efficiency spread the standard envelope admits. The verifier
# should not be able to see a defect much smaller than this, and it would be a
# bad sign if it could: that would mean the verdict was moving in response to
# something other than the physics it claims to model.
ENVELOPE_HALF_WIDTH = 0.0222


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


@pytest.fixture(scope="module")
def outcomes(problems) -> list:
    return run_seeded_defects(problems)


@pytest.fixture(scope="module")
def curve_outcomes(problems) -> list:
    return run_seeded_defects(problems, defects=graded_defects())


def test_the_benchmark_actually_injects_something(outcomes) -> None:
    assert len(outcomes) >= 30
    assert {outcome.severity for outcome in outcomes} >= {"null", "subtle", "gross"}


def test_only_designs_that_certify_cleanly_are_damaged(outcomes) -> None:
    """Damaging an already-undecided design measures nothing.

    There would be no clean signal to distinguish a detection from the verdict
    the design already had.
    """
    assert all(outcome.baseline_verdict == "PROVED" for outcome in outcomes)


def test_no_false_positives_on_a_null_injection(outcomes) -> None:
    """The number that has to sit beside every detection rate.

    A verifier that refuses to certify anything scores 100 percent on detection
    and is useless. These injections change nothing physical, so every one of
    them must still certify.
    """
    table = detection_table(outcomes)
    assert table["null"]["injections"] >= 8
    assert table["null"]["false_positive_rate"] == 0.0, [
        outcome.to_dict() for outcome in outcomes
        if outcome.severity == "null" and outcome.detected
    ]


def test_gross_defects_are_all_caught(outcomes) -> None:
    table = detection_table(outcomes)
    assert table["gross"]["detection_rate"] == 1.0, table["gross"]["missed"]


def test_subtle_defects_are_caught_too(outcomes) -> None:
    table = detection_table(outcomes)
    assert table["subtle"]["detection_rate"] == 1.0, table["subtle"]["missed"]


def test_every_defect_family_is_exercised_somewhere(outcomes) -> None:
    exercised = {outcome.defect for outcome in outcomes}
    declared = {defect.name for defect in DEFECTS}
    # A defect that never applies to any problem is dead weight in the table and
    # should be removed or given a problem it can act on.
    never_applied = declared - exercised
    assert never_applied <= {"branch_settings_30pct_high"}, never_applied


# ---------------------------------------------------------------------------
# The curve, which is the part that characterises the detector
# ---------------------------------------------------------------------------


def test_detection_rises_monotonically_with_defect_magnitude(curve_outcomes) -> None:
    curve = detection_curve(curve_outcomes)
    assert curve, "no graded injections ran"
    for family, rows in curve.items():
        rates = [row["detection_rate"] for row in rows]
        assert rates == sorted(rates), f"{family}: detection fell as the defect grew: {rates}"


def test_a_defect_far_below_the_envelope_is_invisible(curve_outcomes) -> None:
    """It should be. The enclosure has already absorbed that much variation.

    Firing here would mean the verdict responds to changes smaller than the
    uncertainty it certifies over, which would make the certificates noise.
    """
    curve = detection_curve(curve_outcomes)
    for family, rows in curve.items():
        smallest = rows[0]
        assert smallest["magnitude"] < ENVELOPE_HALF_WIDTH
        assert smallest["detection_rate"] == 0.0, f"{family} fired at {smallest['magnitude']:.1%}"


def test_a_defect_well_above_the_envelope_is_always_caught(curve_outcomes) -> None:
    curve = detection_curve(curve_outcomes)
    for family, rows in curve.items():
        largest = rows[-1]
        assert largest["magnitude"] > 4 * ENVELOPE_HALF_WIDTH
        assert largest["detection_rate"] == 1.0, family


def test_the_threshold_sits_near_the_envelope_width(curve_outcomes) -> None:
    """The result worth reporting: sensitivity is set by the declared envelope.

    Detection turns on within a factor of about three of the efficiency spread,
    in both defect families. That is the behaviour the method predicts - the
    enclosure widens by the envelope, so a defect has to exceed it before the
    interval can clear the bound - and it means the sensitivity is a stated
    modelling choice rather than an accident of implementation.
    """
    curve = detection_curve(curve_outcomes)
    for family, rows in curve.items():
        crossing = next((row["magnitude"] for row in rows if row["detection_rate"] >= 0.5), None)
        assert crossing is not None, family
        assert ENVELOPE_HALF_WIDTH / 3 <= crossing <= ENVELOPE_HALF_WIDTH * 3, (
            f"{family} crosses 50% detection at {crossing:.1%}")


def test_the_table_reports_per_defect_rates(outcomes) -> None:
    table = detection_table(outcomes)
    assert table["by_defect"]["none"]["rate"] == 0.0
    assert table["by_defect"]["actuator_two_sizes_down"]["rate"] == 1.0
    for record in table["by_defect"].values():
        assert 0.0 <= record["rate"] <= 1.0
        assert record["n"] > 0


def test_detections_name_what_they_caught(outcomes) -> None:
    """A detection with nothing attached is not usable as evidence."""
    for outcome in outcomes:
        if not outcome.detected:
            continue
        assert outcome.refuted_criteria or outcome.codes or outcome.verdict == "UNDECIDED"
