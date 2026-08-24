"""Tests for the LLM sizing stage and its wiring into the graph.

No network. The planner and repairer are stubbed with objects that return fixed
structured responses, which is enough to exercise everything that matters here:
the tools, the scoring, the repair loop, the routing and the fallback. What the
model *chooses* is not testable offline; what the system does with a choice is,
and that is the part that has to be right.
"""

from __future__ import annotations

import json
import pathlib
import types

import pytest

from hydraulic_mas import graph as graph_module
from hydraulic_mas.config import Settings
from hydraulic_mas.graph import Workflow
from hydraulic_mas.sizing.contract import compile_contract
from hydraulic_mas.sizing.schemas_sizing import (
    SizingCandidate,
    SizingPlanSet,
    SizingRepairPlan,
)
from hydraulic_mas.sizing.selection import evaluate_and_select_sizing, score_candidate
from hydraulic_mas.sizing.tools import build_sizing_tools
from hydraulic_mas.terminal import TerminalReporter


REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "reference" / "validated_topologies.json"


@pytest.fixture(scope="module")
def problems() -> dict:
    return json.loads(REFERENCE.read_text())


def _candidate(candidate_id: str, cylinder: str, pressure: float, phase: str,
               *, ratio: float | None = None, aim_off: float = 0.06) -> SizingCandidate:
    return SizingCandidate.model_validate({
        "candidate_id": candidate_id,
        "approach": f"design {cylinder} against {pressure} bar",
        "actuator_decisions": [{
            "cylinder_id": cylinder,
            "design_pressure_bar": pressure,
            "governing_phase_id": phase,
            "rod_strategy": "area_ratio" if ratio else "force",
            "target_area_ratio": ratio,
            "justification": "test",
        }],
        "supply_decision": {
            "governing_phase_id": phase, "relief_margin": 1.12,
            "speed_aim_off": aim_off, "justification": "test",
        },
        "setting_decisions": [], "engineering_notes": [], "open_issues": [],
    })


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@pytest.fixture()
def p7_02_tools(problems):
    entry = problems["P7-02"]
    contract = compile_contract(entry["requirements"], "P7-02")
    return {
        tool.name: tool
        for tool in build_sizing_tools(entry["topology"], entry["requirements"], contract)
    }


def test_every_tool_returns_parseable_json(p7_02_tools) -> None:
    calls = {
        "list_phases": {}, "list_actuators": {},
        "area_for_force": {"force_n": 50000, "design_pressure_bar": 80},
        "choose_bore": {"required_area_mm2": 6944},
        "choose_rod": {"bore_mm": 100},
        "flow_for_speed": {"area_mm2": 7854, "speed_m_per_min": 5.0},
        "choose_pump": {"flow_l_per_min": 39.27},
        "choose_motor": {"flow_l_per_min": 39.27, "pressure_bar": 85},
        "choose_valve": {"flow_l_per_min": 61.4},
    }
    for name, arguments in calls.items():
        json.loads(p7_02_tools[name].invoke(arguments))


def test_area_tool_applies_mechanical_efficiency(p7_02_tools) -> None:
    """The omission that pushed several reference designs past their ceilings."""
    without = json.loads(p7_02_tools["area_for_force"].invoke(
        {"force_n": 12000, "design_pressure_bar": 40, "mechanical_efficiency": 1.0}))
    with_eta = json.loads(p7_02_tools["area_for_force"].invoke(
        {"force_n": 12000, "design_pressure_bar": 40, "mechanical_efficiency": 0.90}))
    assert with_eta["required_area_mm2"] > without["required_area_mm2"]
    assert with_eta["required_area_mm2"] == pytest.approx(3333.3, abs=1.0)


def test_bore_tool_only_offers_preferred_sizes(p7_02_tools) -> None:
    from hydraulic_mas.sizing.catalog_sizing import BORE_SERIES

    result = json.loads(p7_02_tools["choose_bore"].invoke({"required_area_mm2": 3800}))
    assert result["bore_mm"] in BORE_SERIES


def test_motor_tool_reports_shaft_not_hydraulic_power(p7_02_tools) -> None:
    result = json.loads(p7_02_tools["choose_motor"].invoke(
        {"flow_l_per_min": 19.09, "pressure_bar": 65, "overall_efficiency": 0.85}))
    hydraulic = 19.09 / 60000 * 65e5 / 1000
    assert result["shaft_power_kw"] > hydraulic
    assert result["shaft_power_kw"] == pytest.approx(hydraulic / 0.85, abs=0.01)


def test_evaluate_policy_returns_a_certificate(p7_02_tools) -> None:
    candidate = _candidate("c1", "PressCylinder", 88.0, "ram_final_press_down")
    result = json.loads(p7_02_tools["evaluate_sizing_policy"].invoke(
        {"policy_json": candidate.model_dump_json()}))
    assert result["verdict"] in {"PROVED", "UNDECIDED", "REFUTED"}
    assert result["counts"]["PROVED"] > 0
    assert result["oversizing_index"] >= 1.0
    assert "operating_points" in result


def test_evaluate_policy_rejects_malformed_input_without_crashing(p7_02_tools) -> None:
    """The model will send a bad payload eventually; it must get an error, not a stack trace."""
    result = json.loads(p7_02_tools["evaluate_sizing_policy"].invoke({"policy_json": "{not json"}))
    assert "error" in result


def test_evaluate_policy_reports_an_unknown_phase_as_a_candidate_problem(p7_02_tools) -> None:
    candidate = _candidate("c1", "PressCylinder", 88.0, "no_such_phase")
    result = json.loads(p7_02_tools["evaluate_sizing_policy"].invoke(
        {"policy_json": candidate.model_dump_json()}))
    assert any("does not exist" in problem for problem in result["candidate_problems"])


# ---------------------------------------------------------------------------
# Scoring and selection
# ---------------------------------------------------------------------------


def test_smaller_design_wins_when_both_prove_everything(problems) -> None:
    """Otherwise the winning strategy is always 'go up three bore sizes'."""
    entry = problems["P7-02"]
    contract = compile_contract(entry["requirements"], "P7-02")
    plan = SizingPlanSet(
        candidates=[
            _candidate("small", "PressCylinder", 88.0, "ram_final_press_down"),
            _candidate("roomy", "PressCylinder", 55.0, "ram_final_press_down"),
        ],
        preferred_candidate_id="roomy",
        comparison_summary="test",
    )
    chosen, sizing, _, certificate, scores = evaluate_and_select_sizing(
        plan, entry["topology"], entry["requirements"], contract)
    assert certificate.verdict == "PROVED"
    assert chosen.candidate_id == "small"
    small = next(s for s in scores if s.candidate_id == "small")
    roomy = next(s for s in scores if s.candidate_id == "roomy")
    assert small.oversizing_index < roomy.oversizing_index
    assert small.selected and not roomy.selected


def test_a_candidate_that_cannot_be_applied_scores_without_raising(problems) -> None:
    entry = problems["P7-02"]
    contract = compile_contract(entry["requirements"], "P7-02")
    broken = _candidate("broken", "NoSuchCylinder", 80.0, "ram_final_press_down")
    score, _, _, _ = score_candidate(broken, entry["topology"], entry["requirements"], contract)
    assert not score.eligible
    assert score.errors


def test_verification_outranks_the_models_preference(problems) -> None:
    """A stated preference cannot promote a candidate that does not verify."""
    entry = problems["P7-07"]
    contract = compile_contract(entry["requirements"], "P7-07")
    good = SizingCandidate.model_validate({
        "candidate_id": "good", "approach": "sound",
        "actuator_decisions": [
            {"cylinder_id": "ClampCylinder", "design_pressure_bar": 35.0,
             "governing_phase_id": "clamp_extend_apply_force", "rod_strategy": "force",
             "justification": "t"},
            {"cylinder_id": "WorkCylinder", "design_pressure_bar": 52.0,
             "governing_phase_id": "working_advance", "rod_strategy": "force",
             "justification": "t"},
        ],
        "supply_decision": {"governing_phase_id": "working_advance", "relief_margin": 1.12,
                            "speed_aim_off": 0.06, "justification": "t"},
        "setting_decisions": [], "engineering_notes": [], "open_issues": [],
    })
    bad = SizingCandidate.model_validate({
        "candidate_id": "bad", "approach": "clamp far too small",
        "actuator_decisions": [
            {"cylinder_id": "ClampCylinder", "design_pressure_bar": 400.0,
             "governing_phase_id": "clamp_extend_apply_force", "rod_strategy": "force",
             "justification": "t"},
            {"cylinder_id": "WorkCylinder", "design_pressure_bar": 400.0,
             "governing_phase_id": "working_advance", "rod_strategy": "force",
             "justification": "t"},
        ],
        "supply_decision": {"governing_phase_id": "working_advance", "relief_margin": 1.12,
                            "speed_aim_off": 0.06, "justification": "t"},
        "setting_decisions": [], "engineering_notes": [], "open_issues": [],
    })
    plan = SizingPlanSet(candidates=[bad, good], preferred_candidate_id="bad",
                         comparison_summary="test")
    chosen, _, _, _, _ = evaluate_and_select_sizing(
        plan, entry["topology"], entry["requirements"], contract)
    assert chosen.candidate_id == "good"


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


class _StubAgent:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def invoke(self, _payload):
        self.calls += 1
        return {"structured_response": self.response}


def _workflow(sizing_model: object | None) -> Workflow:
    agents = types.SimpleNamespace(sizing_model=sizing_model)
    return Workflow(settings=Settings.from_env(model="m", fast_model="m"),
                    agents=agents, search_client=None)


def _state(problems, problem_id: str) -> dict:
    entry = problems[problem_id]
    return {
        "problem_id": problem_id,
        "user_query": entry["user_query"],
        "requirements": entry["requirements"],
        "topology": entry["topology"],
        "final_output": {"status": "validated"},
    }


def test_plan_sizing_uses_the_model_and_scores_its_candidates(problems, monkeypatch) -> None:
    entry = problems["P7-02"]
    plan = SizingPlanSet(
        candidates=[
            _candidate("a", "PressCylinder", 88.0, "ram_final_press_down"),
            _candidate("b", "PressCylinder", 60.0, "ram_final_press_down"),
        ],
        preferred_candidate_id="a", comparison_summary="test",
    )
    stub = _StubAgent(plan)
    monkeypatch.setattr(graph_module, "build_sizing_planner", lambda *a, **k: stub)
    result = _workflow(object()).plan_sizing(_state(problems, "P7-02"))
    assert stub.calls == 1
    assert result["sizing_mode"] == "llm"
    assert len(result["sizing_scores"]) == 2
    assert result["sizing_certificate"]["verdict"] == "PROVED"
    assert result["sizing"]["PressCylinder"]["bore_mm"] > 0


def test_plan_sizing_falls_back_when_no_model_is_configured(problems) -> None:
    """The deterministic arm is also the control the LLM arm is compared against."""
    result = _workflow(None).plan_sizing(_state(problems, "P7-02"))
    assert result["sizing_mode"] == "deterministic"
    assert result["sizing_certificate"]["verdict"] == "PROVED"


def test_repair_runs_when_the_certificate_is_not_proved(problems, monkeypatch) -> None:
    entry = problems["P7-07"]
    repaired = SizingRepairPlan(
        candidate=SizingCandidate.model_validate({
            "candidate_id": "fixed", "approach": "larger clamp",
            "actuator_decisions": [
                {"cylinder_id": "ClampCylinder", "design_pressure_bar": 35.0,
                 "governing_phase_id": "clamp_extend_apply_force", "rod_strategy": "force",
                 "justification": "t"},
                {"cylinder_id": "WorkCylinder", "design_pressure_bar": 52.0,
                 "governing_phase_id": "working_advance", "rod_strategy": "force",
                 "justification": "t"},
            ],
            "supply_decision": {"governing_phase_id": "working_advance", "relief_margin": 1.12,
                                "speed_aim_off": 0.10, "justification": "t"},
            "setting_decisions": [], "engineering_notes": [], "open_issues": [],
        }),
        addressed_criteria=["working_advance__velocity"], rationale="more aim-off",
    )
    stub = _StubAgent(repaired)
    monkeypatch.setattr(graph_module, "build_sizing_repairer", lambda *a, **k: stub)
    state = _state(problems, "P7-07")
    state["sizing_certificate"] = {"verdict": "UNDECIDED", "criteria": [], "findings": [],
                                   "operating_points": {}}
    state["sizing_plan"] = {"candidate_id": "before"}
    result = _workflow(object()).repair_sizing(state)
    assert stub.calls == 1
    assert result["sizing_plan"]["candidate_id"] == "fixed"
    assert result["sizing_repairs"] == ["more aim-off"]


@pytest.mark.parametrize(
    "verdict,rounds,expected",
    [
        ("PROVED", 1, "finalize_sizing"),
        ("UNDECIDED", 1, "repair_sizing"),
        ("REFUTED", 1, "repair_sizing"),
        ("REFUTED", 3, "finalize_sizing"),
    ],
)
def test_sizing_routing(verdict, rounds, expected) -> None:
    workflow = _workflow(None)
    state = {"sizing_certificate": {"verdict": verdict}, "sizing_round": rounds,
             "max_sizing_rounds": 3}
    assert workflow.route_after_sizing(state) == expected


@pytest.mark.parametrize(
    "status,expected",
    [("validated", "plan_sizing"), ("validated_with_warnings", "plan_sizing"),
     ("unresolved", "end")],
)
def test_only_a_validated_topology_is_sized(status, expected) -> None:
    """An unresolved topology has no settled valve states, so every phase model
    would be a guess. There is nothing there worth sizing."""
    assert Workflow.route_after_topology_to_sizing({"final_output": {"status": status}}) == expected


def test_finalize_records_a_stop_reason_when_not_proved(problems) -> None:
    workflow = _workflow(None)
    state = {"problem_id": "P7-04", "sizing_certificate": {"verdict": "REFUTED"},
             "sizing_round": 3, "sizing": {}, "sizing_plan": {}}
    result = workflow.finalize_sizing(state)
    assert result["sizing_stop_reason"]
    assert result["final_sized_output"]["verdict"] == "REFUTED"


def test_finalize_is_silent_when_proved(problems) -> None:
    workflow = _workflow(None)
    state = {"problem_id": "P7-02", "sizing_certificate": {"verdict": "PROVED"},
             "sizing_round": 1, "sizing": {}, "sizing_plan": {}}
    result = workflow.finalize_sizing(state)
    assert result["sizing_stop_reason"] == ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_terminal_renders_a_full_sized_output(problems) -> None:
    """Rendering runs last and must never be the thing that fails."""
    result = _workflow(None).plan_sizing(_state(problems, "P7-05"))
    TerminalReporter(compact=False).sized_output({
        "problem_id": "P7-05", "verdict": result["sizing_certificate"]["verdict"],
        "mode": result["sizing_mode"], "policy": result["sizing_plan"],
        "sizing": result["sizing"], "certificate": result["sizing_certificate"],
        "candidate_scores": result["sizing_scores"], "repairs": [], "rounds": 1,
        "stop_reason": None,
    })


def test_terminal_survives_an_empty_sized_output() -> None:
    TerminalReporter(compact=True).sized_output({"verdict": "UNKNOWN"})


def test_an_empty_contract_is_reported_rather_than_silently_proved(problems) -> None:
    """The most dangerous output this stage could produce is an empty PROVED."""
    from hydraulic_mas.sizing.certify import certify
    from hydraulic_mas.sizing.contract import AcceptanceContract
    from hydraulic_mas.sizing.intervals import standard_envelope

    entry = problems["P7-02"]
    empty = AcceptanceContract(problem_id="P7-02")
    certificate = certify("P7-02", entry["topology"], entry["requirements"], empty,
                          {"__supply__": {}}, {}, standard_envelope())
    assert any(f.code == "NO_CHECKABLE_CRITERIA" for f in certificate.findings)
    assert certificate.verdict == "UNDECIDED", "an empty certificate must never read as a pass"
