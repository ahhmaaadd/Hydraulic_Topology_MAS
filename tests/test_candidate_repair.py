from __future__ import annotations

from copy import deepcopy

from hydraulic_mas.candidate_selection import apply_repair_actions, evaluate_and_select_candidates
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

