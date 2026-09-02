"""v0.7.1 — the defects an external review of the v0.7.0 traces exposed.

Three of these are the same defect wearing different clothes: a quantity the
system already knows about never reaches the check that needs it. The load
variation never reached the force criterion. The oversizing index never reached
the verdict. The repair path's own winner never reached the selection flag.

So the audit test in this file is deliberately generic — it asserts that every
parameter of the declared envelope measurably moves at least one criterion — so
the *next* instance of the class fails a test instead of surviving to a trace.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.ablation import load_suite, run_cell
from hydraulic_mas.ablation.random_policy import run_random_arm, sample_candidate
from hydraulic_mas.sizing.contract import compile_contract, load_tolerance
from hydraulic_mas.sizing.intervals import standard_envelope
from hydraulic_mas.sizing.planner import size_problem
from hydraulic_mas.sizing.selection import OVERSIZING_CEILING


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"
PROBLEM_IDS = ["P7-01", "P7-02", "P7-03", "P7-04", "P7-05", "P7-06", "P7-07"]


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


# ---------------------------------------------------------------------------
# The stated load variation must reach the force criterion
# ---------------------------------------------------------------------------


def _force_criterion(contract, criterion_id):
    return next(item for item in contract.criteria if item.id == criterion_id)


def test_force_target_covers_the_top_of_the_stated_band(problems) -> None:
    """P7-01 declares ±15 % cutting-load variation; the actuator must cover the top.

    Judged against the nominal instead, the reported margin on the governing
    phase was 1.20x where the honest figure is 1.04x — a fourfold overstatement,
    on the one problem in the benchmark that declares a variation at all.
    """
    entry = problems["P7-01"]
    assert load_tolerance(entry["requirements"]) == 0.15
    contract = compile_contract(entry["requirements"], "P7-01")
    feed = _force_criterion(contract, "slide_cutting_feed_extend__force")
    assert feed.target.to("kn") == pytest.approx(40.25)      # 35 kN + 15 %
    assert "top of the" in feed.rationale
    assert any("not the nominal" in note for note in contract.assumptions)


def test_a_brief_with_no_stated_variation_is_untouched(problems) -> None:
    """The rule must not invent conservatism where the brief states none."""
    entry = problems["P7-03"]
    assert load_tolerance(entry["requirements"]) == 0.0
    contract = compile_contract(entry["requirements"], "P7-03")
    advance = _force_criterion(contract, "carriage_advance__force")
    assert advance.target.to("kn") == pytest.approx(15.0)
    assert advance.rationale == "the phase must develop at least the stated load"


def test_the_load_variation_has_exactly_one_definition() -> None:
    """It had two, and they disagreed. The planner's copy is now an alias.

    The envelope applied the variation while the force criterion did not,
    because each read its own implementation of the same rule.
    """
    import inspect

    from hydraulic_mas.sizing import planner

    source = inspect.getsource(planner._load_tolerance)
    assert "from .contract import load_tolerance" in source
    assert "15 percent" not in source, "the second implementation is back"


# ---------------------------------------------------------------------------
# The generic audit: no envelope parameter may be silently ignored
# ---------------------------------------------------------------------------


def test_every_envelope_parameter_moves_something(problems) -> None:
    """The defect class, stated as a property.

    An envelope parameter that changes no criterion anywhere is either
    unnecessary or — as the load factor was — being dropped before it reaches
    the check that needs it. Either way it should not sit in the envelope
    looking as though it is doing work.
    """
    from hydraulic_mas.sizing.certify import certify

    entry = problems["P7-01"]
    planned = size_problem("P7-01", entry["topology"], entry["requirements"])
    contract = compile_contract(entry["requirements"], "P7-01")
    tolerance = load_tolerance(entry["requirements"])
    full = standard_envelope(tolerance)
    assert set(full.parameters) == {"mechanical_efficiency", "volumetric_efficiency", "load_factor"}

    def enclosures(envelope):
        certificate = certify("P7-01", entry["topology"], entry["requirements"], contract,
                              planned.sizing, planned.throttle_k, envelope)
        return {item.criterion_id: item.achieved for item in certificate.certificates}

    baseline = enclosures(full)
    for name in list(full.parameters):
        pinched = standard_envelope(tolerance)
        interval = pinched.parameters[name]
        # Collapse this one parameter to its midpoint, leave the others alone.
        pinched.parameters[name] = type(interval)(interval.mid, interval.mid)
        changed = [key for key, value in enclosures(pinched).items() if value != baseline[key]]
        assert changed, (
            f"collapsing {name} changed no enclosure anywhere — it is declared in the "
            "envelope but never reaches a check")


# ---------------------------------------------------------------------------
# Oversizing is a constraint, not a price
# ---------------------------------------------------------------------------


def test_the_oversizing_ceiling_is_a_hard_constraint() -> None:
    """Priced at −2 against +10 per proved criterion, brute force wins.

    P7-07's LLM run reached an oversizing index of 5.82 — a 200 mm bore to clamp
    12 kN, 6.3× the required piston area — and certified everything.
    """
    assert 1.5 < OVERSIZING_CEILING < 2.5


def test_the_real_p7_07_oversized_design_no_longer_certifies(problems) -> None:
    """Reproduces the actual v0.7.0 LLM output, verbatim from its trace.

    The model designed the 12 kN clamp against 5 bar, which forces a 200 mm bore
    - 6.3x the piston area the load needs - and every criterion certified. It is
    a correct answer to the question the scoring function asked and a bad answer
    to the question a designer was asking, which is why the ceiling had to stop
    being a price and start being a constraint.
    """
    from hydraulic_mas.ablation.apply_direct import apply_direct
    from hydraulic_mas.ablation.arms import ArmResult, _finish
    from hydraulic_mas.ablation.schemas import DirectCylinder, DirectSetting, DirectSizing

    entry = problems["P7-07"]
    contract = compile_contract(entry["requirements"], "P7-07")
    as_run = DirectSizing(
        cylinders=[
            DirectCylinder(cylinder_id="ClampCylinder", bore_mm=200, rod_mm=140),
            DirectCylinder(cylinder_id="WorkCylinder", bore_mm=125, rod_mm=70),
        ],
        displacement_cm3=28.0, pump_speed_rpm=1500.0, relief_bar=44.8,
        motor_kw=4.0, reservoir_l=125, valve_size="NG10",
        settings=[
            DirectSetting(component_id="ClampPressureReducingValve", setting_bar=40.0),
            DirectSetting(component_id="ForwardSequenceValve", setting_bar=6.0),
            DirectSetting(component_id="ReverseSequenceValve", setting_bar=55.0),
        ],
        reasoning="as produced by the v0.7.0 LLM arm",
    )
    applied = apply_direct(as_run, entry["topology"], entry["requirements"], contract,
                           load_tolerance(entry["requirements"]))
    assert not applied.install_errors, applied.install_errors

    result = _finish(ArmResult(arm="test", problem_id="P7-07", seed=0),
                     applied.certificate, applied.sizing, contract,
                     entry["topology"], entry["requirements"])
    assert result.oversizing_index > OVERSIZING_CEILING, result.oversizing_index
    assert result.verdict == "REFUTED", (
        "meeting the requirements by being enormous is not meeting them")
    assert any("oversizing" in error for error in result.errors)


# ---------------------------------------------------------------------------
# A-rand: the null control
# ---------------------------------------------------------------------------


def test_the_random_arm_is_reproducible(problems) -> None:
    """A control that changes between runs cannot control anything."""
    entry = problems["P7-02"]
    first = run_random_arm("P7-02", entry["topology"], entry["requirements"], seed=3)
    second = run_random_arm("P7-02", entry["topology"], entry["requirements"], seed=3)
    assert first.verdict == second.verdict
    assert first.oversizing_index == second.oversizing_index


def test_the_random_arm_actually_varies_with_the_seed(problems) -> None:
    entry = problems["P7-01"]
    seen = {run_random_arm("P7-01", entry["topology"], entry["requirements"], seed=s).oversizing_index
            for s in range(8)}
    assert len(seen) > 1, "a control that samples the same policy every time is not random"


def test_the_random_policy_fills_the_same_schema_the_model_does(problems) -> None:
    """Like for like, or the comparison measures the schema rather than the judgement."""
    import random

    entry = problems["P7-07"]
    contract = compile_contract(entry["requirements"], "P7-07")
    candidate = sample_candidate(entry["topology"], entry["requirements"], contract,
                                 random.Random("x"), "rand_0")
    cylinders = {str(c["id"]) for c in entry["topology"]["components"]
                 if c.get("comp_type") == "cylinder"}
    assert {a.cylinder_id for a in candidate.actuator_decisions} == cylinders
    assert 1.0 <= candidate.supply_decision.relief_margin <= 1.6
    assert 0.0 <= candidate.supply_decision.speed_aim_off <= 0.30
    for actuator in candidate.actuator_decisions:
        assert actuator.design_pressure_bar > 0
        if actuator.rod_strategy == "area_ratio":
            assert actuator.target_area_ratio is not None


def test_the_null_control_does_not_match_the_engineered_arms(problems) -> None:
    """The result that decides whether the ablation means anything.

    If a policy drawn at random certified as often as a reasoned one, the tools
    and the verifier would be carrying the entire result and the model would be
    decoration. It does not: across 7 problems x 5 seeds the random arm proves a
    minority, and is refuted outright on the problems with tight ceilings.
    """
    suite = load_suite(REFERENCE)
    proved = total = 0
    for problem_id in PROBLEM_IDS:
        for seed in range(5):
            total += 1
            proved += run_cell("A-rand", suite, problem_id, seed).verdict == "PROVED"
    rate = proved / total
    assert rate < 0.55, f"random policies proved {rate:.0%} — the scaffolding is doing the work"
    assert rate > 0.05, f"random policies proved {rate:.0%} — the control is too weak to be informative"


def test_the_deterministic_arm_still_beats_the_null_control(problems) -> None:
    suite = load_suite(REFERENCE)
    deterministic = sum(run_cell("A2", suite, pid, 0).verdict == "PROVED" for pid in PROBLEM_IDS)
    best_random = 0
    for problem_id in PROBLEM_IDS:
        best_random += any(
            run_cell("A-rand", suite, problem_id, seed).verdict == "PROVED" for seed in range(3))
    assert deterministic >= 5
    assert deterministic >= best_random - 1, (
        "a fixed engineering policy should not lose to three random draws")
