from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from hydraulic_mas.config import Settings
from hydraulic_mas.graph import Workflow
from hydraulic_mas.schemas import CritiqueResult
from test_validation import requirements as requirements_dict


class StaticRunnable:
    def __init__(self, value):
        self.value = value

    def invoke(self, _input):
        return self.value


class NoopSearch:
    def search(self, query: str, *, max_results: int):
        return []


def _settings() -> Settings:
    return Settings(
        model="test",
        fast_model="test",
        openai_api_key=None,
        openai_base_url=None,
        default_headers={},
        azure_api_key=None,
        azure_endpoint=None,
        azure_api_version="test",
        tavily_api_key=None,
    )


def _workflow(critique: CritiqueResult) -> Workflow:
    return Workflow(
        settings=_settings(),
        agents=SimpleNamespace(critic=StaticRunnable(critique)),
        search_client=NoopSearch(),
    )


def _state(requirements: dict) -> dict:
    return {
        "user_query": "Move one horizontal slide through the stated phases.",
        "requirements": requirements,
        "interactive": False,
        "requirements_round": 0,
        "max_requirements_rounds": 1,
        "requirements_repair_attempted": False,
    }


def test_advisory_consistency_issues_do_not_block_topology() -> None:
    requirements = requirements_dict()
    requirements["open_questions"] = [
        {
            "id": "q_tolerance",
            "question": "What verification tolerance should be used?",
            "why_it_matters": "Needed during final sizing and acceptance testing.",
            "blocking": True,
        }
    ]
    critique = CritiqueResult.model_validate(
        {
            "completeness_status": "proceed_with_assumptions",
            "rationale": "Topology is unambiguous; tolerance is deferred to sizing.",
            "consistency_issues": ["The exact speed criterion has no verification tolerance."],
            "proposed_assumptions": [
                {
                    "id": "a_tolerance",
                    "statement": "Use a provisional tolerance during later sizing.",
                    "rationale": "It does not change the generic circuit topology.",
                    "affects": ["q_tolerance"],
                }
            ],
        }
    )
    workflow = _workflow(critique)
    state = _state(requirements)
    state.update(workflow.critique_requirements(state))

    assert state["requirements_structural_issues"] == []
    assert workflow.route_after_requirements_critique(state) == "finalize_requirements"

    result = workflow.finalize_requirements(state)
    assert result["requirements_unresolved"] is False
    assert result["requirements_gate"]["decision"] == "proceed"
    assert result["requirements_gate"]["advisory_issues"] == critique.consistency_issues
    assert result["requirements"]["open_questions"][0]["blocking"] is False
    assert result["requirements_gate"]["merged_assumption_ids"] == ["a_tolerance"]


def test_true_blocking_question_still_blocks_noninteractive_run() -> None:
    question = {
        "id": "q_load_direction",
        "question": "Can the vertical load overrun the actuator?",
        "why_it_matters": "It changes dynamic load control and metering topology.",
        "blocking": True,
    }
    critique = CritiqueResult.model_validate(
        {
            "completeness_status": "needs_clarification",
            "rationale": "Load behavior is unsafe to infer.",
            "blocking_questions": [question],
        }
    )
    workflow = _workflow(critique)
    state = _state(requirements_dict())
    state.update(workflow.critique_requirements(state))

    result = workflow.finalize_requirements(state)
    assert result["requirements_unresolved"] is True
    assert result["requirements_gate"]["decision"] == "blocked"
    assert result["requirements_gate"]["blocking_question_ids"] == ["q_load_direction"]


def test_deterministic_structural_issue_routes_to_repair_and_remains_blocking() -> None:
    requirements = deepcopy(requirements_dict())
    requirements["operational_logic"]["sequence"][0]["phase_id"] = "missing_phase"
    critique = CritiqueResult(completeness_status="sufficient", rationale="No semantic gaps.")
    workflow = _workflow(critique)
    state = _state(requirements)
    state.update(workflow.critique_requirements(state))

    assert any("unknown phase" in issue for issue in state["requirements_structural_issues"])
    assert workflow.route_after_requirements_critique(state) == "repair_requirements"

    state["requirements_repair_attempted"] = True
    result = workflow.finalize_requirements(state)
    assert result["requirements_unresolved"] is True
    assert result["requirements_gate"]["decision"] == "blocked"


def test_active_forbidden_self_reference_is_normalized_losslessly() -> None:
    requirements = deepcopy(requirements_dict())
    requirements["operational_logic"]["sequence"][0]["forbidden_overlap_function_ids"] = ["slide"]
    critique = CritiqueResult(completeness_status="sufficient", rationale="No semantic gaps.")
    workflow = _workflow(critique)
    state = _state(requirements)
    state.update(workflow.critique_requirements(state))

    sequence_step = state["requirements"]["operational_logic"]["sequence"][0]
    assert sequence_step["forbidden_overlap_function_ids"] == []
    assert state["requirements_structural_issues"] == []
    assert state["requirements_normalizations"]
    assert workflow.route_after_requirements_critique(state) == "finalize_requirements"
