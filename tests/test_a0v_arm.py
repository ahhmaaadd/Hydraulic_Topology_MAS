"""A0-V — the cell that turns the ablation ladder into a factorial.

Every test here runs against a scripted stub client. Not for speed: an
experiment whose harness can only be checked by spending API budget is an
experiment whose harness stops being checked, and the properties that matter
most - that the budget is honoured, that no tool is ever bound, that a failed
revision does not lose the design that prompted it - are exactly the ones a live
run would hide behind sampling noise.

The stub also lets a *known* trajectory be asserted: a design that gets worse,
then better, then clean, tests the selection rule in a way ten real seeds could
not be relied on to produce.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hydraulic_mas.ablation import arms as arms_module
from hydraulic_mas.ablation.arms import (
    DEFAULT_CANDIDATES,
    DEFAULT_REPAIR_ROUNDS,
    _direct_score,
    run_direct_verified_arm,
)
from hydraulic_mas.ablation.schemas import (
    DirectCylinder,
    DirectSetting,
    DirectSizing,
    DirectSizingSet,
)
from hydraulic_mas.sizing.contract import compile_contract, load_tolerance


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


# ---------------------------------------------------------------------------
# A scripted client
# ---------------------------------------------------------------------------


class ScriptedClient:
    """Returns pre-built answers in order, and records what it was asked.

    ``calls`` keeps the (system, prompt, tools, schema) of every invocation so a
    test can assert on what the arm actually sent - which is the only way to
    check that the repair prompt carried the certificate and that the tool list
    stayed empty.
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[tuple] = []

    def __call__(self, system, prompt, tools, schema):
        self.calls.append((system, prompt, tools, schema))
        if not self.script:
            raise AssertionError("the arm asked for more rounds than the script allows")
        answer = self.script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _sizing(bore: float, rod: float, *, displacement: float = 28.0,
            relief: float = 70.0, cylinder_id: str = "SlideCylinder",
            settings: list[DirectSetting] | None = None) -> DirectSizing:
    return DirectSizing(
        cylinders=[DirectCylinder(cylinder_id=cylinder_id, bore_mm=bore, rod_mm=rod)],
        displacement_cm3=displacement, pump_speed_rpm=1500.0, relief_bar=relief,
        motor_kw=5.5, reservoir_l=100, valve_size="NG10",
        settings=settings or [], reasoning="scripted",
    )


# Verified against the certifier at the time of writing. If the verifier changes
# and these stop behaving as named, the tests that use them fail loudly - which
# is the point of naming them rather than inlining the numbers.
PROVES_P7_01 = _sizing(100, 56)          # proves all 7 criteria
REFUTED_P7_01 = _sizing(200, 140)        # installs, proves 5, refutes 2
UNINSTALLABLE = _sizing(50, 60)          # rod wider than bore: cannot be built


def _run(problems, client, problem_id="P7-01", **kwargs):
    entry = problems[problem_id]
    return run_direct_verified_arm(
        client, problem_id, problem_id, entry["topology"], entry["requirements"],
        load_tolerance=load_tolerance(entry["requirements"]), **kwargs)


# ---------------------------------------------------------------------------
# The factorial only holds if this arm never touches a tool
# ---------------------------------------------------------------------------


def test_no_tool_is_ever_bound(problems) -> None:
    """A0-V is the *no tools* cell. If a tool leaks in, it is A1 with extra steps."""
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(100, 56)]),
        *[_sizing(125, 70) for _ in range(DEFAULT_REPAIR_ROUNDS)],
    ])
    _run(problems, client)
    assert client.calls, "the arm made no call at all"
    for _system, _prompt, tools, _schema in client.calls:
        assert tools == [], "A0-V was given tools; the tools factor is no longer manipulated"


def test_the_opening_call_asks_for_a_set_and_repairs_ask_for_one(problems) -> None:
    """Matched to A1: several candidates scored deterministically, then revisions."""
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(80, 45)]),
        *[_sizing(100, 56) for _ in range(DEFAULT_REPAIR_ROUNDS)],
    ])
    _run(problems, client)
    assert client.calls[0][3] is DirectSizingSet
    for call in client.calls[1:]:
        assert call[3] is DirectSizing


# ---------------------------------------------------------------------------
# The repair budget is the thing being held constant, so it must be exact
# ---------------------------------------------------------------------------


def test_the_repair_budget_is_never_exceeded(problems) -> None:
    """A budget that drifts turns 'does the verifier help' into 'who got more shots'."""
    rounds = 2
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(40, 22)]),
        *[_sizing(40, 22) for _ in range(rounds + 3)],
    ])
    result = _run(problems, client, rounds=rounds)
    assert result.proposal["repair_rounds_used"] <= rounds
    assert len(client.calls) <= rounds + 1
    assert result.proposal["repair_rounds_allowed"] == rounds


def test_a_clean_certificate_stops_the_loop_early(problems) -> None:
    """Nothing is gained by revising a design that already proved everything."""
    client = ScriptedClient([DirectSizingSet(candidates=[PROVES_P7_01])])
    result = _run(problems, client)
    assert result.verdict == "PROVED", "the fixture design stopped proving; pick another"
    assert len(client.calls) == 1, "a proved design was sent back for repair"
    assert result.proposal["repair_rounds_used"] == 0
    assert result.proposal["converged"] is True


# ---------------------------------------------------------------------------
# What the arm is scored on
# ---------------------------------------------------------------------------


def test_the_best_attempt_wins_not_the_last_one(problems) -> None:
    """A repair that makes things worse must not become the arm's reported answer.

    Scripted so the second attempt is plainly worse than the first: a rod wider
    than the bore cannot be installed at all, which floors the score. If the arm
    reported the final round, this design would be the result.
    """
    client = ScriptedClient([
        DirectSizingSet(candidates=[REFUTED_P7_01]),
        UNINSTALLABLE, UNINSTALLABLE, UNINSTALLABLE,
    ])
    result = _run(problems, client)
    assert len(client.calls) > 1, "no repair ran, so the selection rule was not exercised"
    kept = result.proposal["selected"]
    assert kept["cylinders"][0]["bore_mm"] == 200, "the worse revision was reported"
    # The kept design does trip the oversizing ceiling under the default, which
    # is correct. What must not appear is the *discarded* attempt's install
    # failure: errors are collected from the design that was kept, not from every
    # design that was ever tried.
    assert not any("not smaller than bore" in message for message in result.errors), \
        "an install failure from a discarded attempt leaked into the result"


def test_every_round_is_recorded(problems) -> None:
    """The convergence curve and the H5 mechanism test both read this trajectory."""
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(40, 22), _sizing(50, 28)]),
        _sizing(63, 36), _sizing(80, 45), _sizing(100, 56),
    ])
    result = _run(problems, client)
    trajectory = result.proposal["trajectory"]
    assert trajectory[0]["kind"] == "candidates"
    assert trajectory[0]["n"] == 2
    assert [row["round"] for row in trajectory] == list(range(len(trajectory)))
    for row in trajectory:
        assert "verdict" in row and "score" in row


def test_the_score_matches_the_one_a1_is_ranked_by() -> None:
    """Two arms ranked by two different objectives are not being compared.

    ``_direct_score`` is a copy of the weights in ``sizing/selection.py`` because
    the original takes a policy and applies it with the tools this arm does not
    have. Copies drift, so the drift is what is asserted.
    """
    import inspect

    from hydraulic_mas.sizing import selection

    source = inspect.getsource(selection.score_candidate)
    for weight in ("10.0 *", "25.0 *", "8.0 *", "20.0 *", "2.0 *"):
        assert weight in source, f"selection.py no longer uses {weight}; _direct_score is stale"

    class _Cert:
        def counts(self):
            return {"PROVED": 4, "UNDECIDED": 1, "REFUTED": 1}

    # 10*4 - 25*1 - 8*1 - 20*0 - 2*(1.5-1) = 40 - 25 - 8 - 1 = 6
    assert _direct_score(_Cert(), 1.5, 0) == pytest.approx(6.0)
    assert _direct_score(None, 1.0, 0) == -1e6


# ---------------------------------------------------------------------------
# Failure is a data point, not a lost run
# ---------------------------------------------------------------------------


def test_a_failed_opening_call_is_recorded_as_an_error(problems) -> None:
    client = ScriptedClient([RuntimeError("model refused")])
    result = _run(problems, client)
    assert result.verdict == "ERROR"
    assert any("model refused" in message for message in result.errors)


def test_a_failed_revision_keeps_the_design_that_prompted_it(problems) -> None:
    """The arm is scored on what it produced, not punished for a transport failure."""
    client = ScriptedClient([
        DirectSizingSet(candidates=[REFUTED_P7_01]),
        RuntimeError("rate limited"),
    ])
    result = _run(problems, client)
    assert result.verdict != "ERROR", "a failed repair discarded a usable design"
    assert result.proposal["selected"]["cylinders"][0]["bore_mm"] == 200
    assert any("rate limited" in message for message in result.errors)


def test_an_empty_candidate_set_is_an_error_not_a_crash(problems) -> None:
    class _Empty:
        candidates: list = []

    client = ScriptedClient([_Empty()])
    result = _run(problems, client)
    assert result.verdict == "ERROR"
    assert any("no candidates" in message for message in result.errors)


# ---------------------------------------------------------------------------
# The feedback channel
# ---------------------------------------------------------------------------


def test_the_repair_prompt_carries_the_certificate(problems) -> None:
    """§4.3 of the pre-registration: the leak is total and deliberate.

    A0-V is handed the full certificate, including tool-computed enclosures, so
    that it is an explicit *upper bound* on toolless performance. If this ever
    stops being true the arm silently becomes something else, and the paper's
    stated design no longer describes the code.
    """
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(40, 22)]),
        _sizing(50, 28), _sizing(63, 36), _sizing(80, 45),
    ])
    _run(problems, client)
    assert len(client.calls) > 1, "no repair round ran, so there is no feedback to check"
    repair_prompt = client.calls[1][1]
    assert "WHAT THE VERIFIER FOUND" in repair_prompt
    assert "CRITERION BY CRITERION" in repair_prompt
    assert "YOUR PREVIOUS ATTEMPT" in repair_prompt
    assert "revision(s) left" in repair_prompt


def test_the_arm_is_registered_in_the_grid() -> None:
    from hydraulic_mas.ablation import ARM_DESCRIPTION, ARMS

    assert "A0-V" in ARMS
    assert "A0-V" in ARM_DESCRIPTION
    assert DEFAULT_CANDIDATES >= 2 and DEFAULT_REPAIR_ROUNDS >= 1


def test_the_oversizing_ceiling_can_be_taken_out_of_the_path(problems) -> None:
    """PREREGISTRATION_E1 §4.5. E1 runs with the ceiling off, on every arm.

    Switchable rather than deleted, so the v0.7.1 records stay reproducible.
    """
    assert arms_module.APPLY_OVERSIZING_CEILING is True, "the default must not change silently"
    client = ScriptedClient([
        DirectSizingSet(candidates=[_sizing(200, 140)]),
        *[_sizing(200, 140) for _ in range(DEFAULT_REPAIR_ROUNDS)],
    ])
    without = _run(problems, client, apply_ceiling=False)
    assert not any("oversizing" in message for message in without.errors)


def test_the_loop_keeps_working_on_a_design_the_ceiling_would_reject(problems) -> None:
    """The defect A1 shipped with, caught here before this arm inherits it.

    In v0.7.1 the repair loop terminated on ``certificate.verdict == "PROVED"``
    while the reported verdict was computed afterwards, with the oversizing
    ceiling applied on top. A design that proved every criterion and was then
    rejected for bulk therefore stopped the loop dead: all ten P7-07 seeds
    produced the identical refused design, because the rejection never reached
    the only thing that could act on it.

    So ``clean`` has to mean "would be reported as proved", not "the certificate
    says proved". The observable consequence is that ``converged`` and the final
    verdict can never disagree.
    """
    # 200/140 proves five of seven on P7-01 and lands at 5.66x oversized.
    client = ScriptedClient([
        DirectSizingSet(candidates=[REFUTED_P7_01]),
        *[REFUTED_P7_01 for _ in range(DEFAULT_REPAIR_ROUNDS)],
    ])
    result = _run(problems, client)
    assert result.proposal["repair_rounds_used"] == DEFAULT_REPAIR_ROUNDS, \
        "the loop stopped early on a design the ceiling rejects"
    assert result.proposal["converged"] is (result.verdict == "PROVED"), \
        "converged and the reported verdict disagree"


def test_the_feedback_says_out_loud_that_an_oversized_design_is_rejected(problems) -> None:
    """A number is not a rejection. The v0.7.1 loop was shown one and did nothing."""
    from hydraulic_mas.ablation.feedback import render_certificate_feedback

    class _Cert:
        certificates: list = []
        findings: list = []
        operating_points: dict = {}

        def counts(self):
            return {"PROVED": 5, "UNDECIDED": 0, "REFUTED": 0}

        @property
        def verdict(self):
            return "PROVED"

    quiet = render_certificate_feedback(_Cert(), oversizing_index=5.66, over_ceiling=False)
    loud = render_certificate_feedback(_Cert(), oversizing_index=5.66, over_ceiling=True)
    assert "5.66" in quiet and "REJECTED" not in quiet
    assert "REJECTED" in loud, "an over-ceiling design was reported as a bare number"
