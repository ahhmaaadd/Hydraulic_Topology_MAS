from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage

from hydraulic_mas.candidate_selection import apply_repair_actions, evaluate_and_select_candidates
from hydraulic_mas.config import Settings
from hydraulic_mas.graph import Workflow
from hydraulic_mas.schemas import ComponentPlan, ComponentPlanSet, PlannedComponent, RepairAction
from test_validation import requirements, valid_topology


def _plan(candidate_id: str = "minimal") -> ComponentPlan:
    topology = valid_topology()
    return ComponentPlan(
        candidate_id=candidate_id,
        approach="minimal conventional topology",
        planning_summary="Catalog-backed direct circuit.",
        components=[
            PlannedComponent(
                id=item["id"],
                catalog_key=item["catalog_key"],
                comp_type=item["comp_type"],
                role=item["role"],
                function_id=item.get("function_id"),
                selection_basis=item["selection_basis"],
            )
            for item in topology["components"]
        ],
        connection_intent=[],
    )


def test_delete_component_repair_is_executable() -> None:
    plan = _plan()
    plan.components.append(
        PlannedComponent(
            id="UnjustifiedSequence",
            catalog_key="GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK",
            comp_type="sequence_valve",
            role="Unjustified complexity",
            function_id="slide",
            selection_basis="Deliberately removable regression fixture.",
        )
    )
    plan.repair_actions = [
        RepairAction(
            action="delete_component",
            issue_codes=["UNJUSTIFIED_SEQUENCE_VALVE"],
            rationale="No pressure-triggered sequence exists.",
            target_component_id="UnjustifiedSequence",
        )
    ]
    repaired = apply_repair_actions(plan)
    assert "UnjustifiedSequence" not in {item.id for item in repaired.components}


def test_add_connection_accepts_common_target_field_mixup() -> None:
    connection = {
        "description": "Add pump supply branch.",
        "from_hint": "Pump",
        "to_hint": "DCV",
        "line": "pressure",
    }
    action = RepairAction.model_validate(
        {
            "action": "add_connection",
            "rationale": "Restore missing supply path.",
            "target_connection": connection,
            "replacement_connection": None,
        }
    )
    assert action.target_connection is None
    assert action.replacement_connection is not None
    assert action.replacement_connection.from_hint == "Pump"


def test_add_connection_without_any_connection_payload_remains_invalid() -> None:
    with pytest.raises(ValueError, match="add_connection requires replacement_connection"):
        RepairAction.model_validate(
            {
                "action": "add_connection",
                "rationale": "Malformed model output.",
                "target_connection": None,
                "replacement_connection": None,
            }
        )


class RecoveringPlanner:
    def __init__(self, plan_set: ComponentPlanSet):
        self.plan_set = plan_set
        self.prompts: list[str] = []

    def invoke(self, payload):
        self.prompts.append(str(payload["messages"][0].content))
        if len(self.prompts) == 1:
            raise StructuredOutputValidationError(
                "ComponentPlanSet",
                ValueError("add_connection requires replacement_connection"),
                AIMessage(content=""),
            )
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "list_component_types", "args": {}, "id": "types", "type": "tool_call"},
                        {
                            "name": "list_components",
                            "args": {"comp_type": "pump"},
                            "id": "components",
                            "type": "tool_call",
                        },
                    ],
                )
            ],
            "structured_response": self.plan_set,
        }


class NoopSearch:
    def search(self, query: str, *, max_results: int):
        return []


def test_component_planner_retries_structured_output_validation_error() -> None:
    first = _plan("minimal")
    second = _plan("alternative")
    plan_set = ComponentPlanSet(
        candidates=[first, second],
        preferred_candidate_id="minimal",
        comparison_summary="Two catalog-backed regression candidates.",
    )
    planner = RecoveringPlanner(plan_set)
    settings = Settings(
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
    workflow = Workflow(
        settings=settings,
        agents=SimpleNamespace(component_planner=planner),
        search_client=NoopSearch(),
    )

    result = workflow.plan_components(
        {
            "design_brief": {"application": "test fixture"},
            "research_synthesis": {"summary": "test evidence"},
            "requirements": requirements(),
        }
    )

    assert len(planner.prompts) == 2
    assert "STRUCTURED OUTPUT CORRECTION" in planner.prompts[1]
    assert len(result["component_planner_recovery"]) == 1
    assert result["component_planner_recovery"][0]["outcome"] == "retry"
    assert result["component_plan"]["candidate_id"] == "minimal"


def test_candidate_selector_rejects_fake_hi_lo_workaround() -> None:
    minimal = _plan("minimal")
    bad = deepcopy(minimal)
    bad.candidate_id = "bad_hi_lo"
    bad.approach = "hi-lo two-pump workaround"
    bad.components.extend(
        [
            PlannedComponent(
                id="Pump2",
                catalog_key="GENERIC_FIXED_DISPLACEMENT_PUMP",
                comp_type="pump",
                role="Low-pressure high-flow pump",
                selection_basis="Candidate hi-lo supply.",
            ),
            PlannedComponent(
                id="SequenceAsUnloader",
                catalog_key="GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK",
                comp_type="sequence_valve",
                role="Incorrect unloading workaround",
                selection_basis="Deliberately invalid candidate.",
            ),
        ]
    )
    candidates = ComponentPlanSet(
        candidates=[bad, minimal],
        preferred_candidate_id="bad_hi_lo",
        comparison_summary="The deterministic scorer must override an invalid model preference.",
    )
    selected, _plans, evaluations = evaluate_and_select_candidates(candidates, requirements())
    assert selected.candidate_id == "minimal"
    bad_evaluation = next(item for item in evaluations if item.candidate_id == "bad_hi_lo")
    assert not bad_evaluation.eligible
    assert any("unloading valve" in error for error in bad_evaluation.errors)
    assert any("plain check valve" in error for error in bad_evaluation.errors)
