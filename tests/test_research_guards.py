from __future__ import annotations

from hydraulic_mas.research_guards import enforce_coverage_gate, enforce_minimum_plan
from hydraulic_mas.schemas import (
    Citation,
    CoverageItem,
    KnowledgeNeed,
    ResearchCoverage,
    ResearchPlan,
    TaskFinding,
)


def _requirements() -> dict:
    return {
        "functions": [
            {
                "id": "slide",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "load_type": "resistive",
                "motion_phases": [{"name": "advance"}, {"name": "work"}],
                "holding": {},
            }
        ],
        "operational_logic": {"sequence": [], "interlocks": []},
        "derived_design_drivers": [],
    }


def test_minimum_plan_fills_required_topology_needs() -> None:
    plan = ResearchPlan(rationale="empty model plan", knowledge_needs=[], tasks=[])
    guarded = enforce_minimum_plan(plan, _requirements(), max_initial_tasks=10)
    ids = {need.id for need in guarded.knowledge_needs}
    assert "system_power_and_relief" in ids
    assert "function_slide_directional_control" in ids
    assert "function_slide_speed_and_transition" in ids
    assert all(task.need_ids for task in guarded.tasks)


def test_coverage_gate_rejects_unsupported_sufficiency() -> None:
    need = KnowledgeNeed(id="n1", decision="safe circuit", why_needed="safety", critical=True)
    proposed = ResearchCoverage(
        status="sufficient",
        can_proceed=True,
        summary="claimed sufficient",
        items=[CoverageItem(need_id="n1", status="covered", rationale="claimed")],
    )
    guarded = enforce_coverage_gate(
        proposed,
        needs=[need],
        findings=[],
        query_history=[],
        round_number=1,
        remaining_searches=3,
        rounds_left=True,
    )
    assert guarded.status == "needs_more"
    assert not guarded.can_proceed
    assert guarded.follow_up_tasks


def test_coverage_gate_accepts_cited_medium_confidence_evidence() -> None:
    need = KnowledgeNeed(id="n1", decision="safe circuit", why_needed="safety", critical=True)
    finding = TaskFinding(
        task_id="t1",
        need_ids=["n1"],
        category="circuit_pattern",
        summary="supported",
        citations=[Citation(title="Manual", url="https://example.com/manual", source_kind="manufacturer")],
        confidence="medium",
    )
    proposed = ResearchCoverage(
        status="sufficient",
        can_proceed=True,
        summary="supported",
        items=[CoverageItem(need_id="n1", status="covered", rationale="manual", evidence_task_ids=["t1"])],
    )
    guarded = enforce_coverage_gate(
        proposed,
        needs=[need],
        findings=[finding],
        query_history=["hydraulic query"],
        round_number=1,
        remaining_searches=3,
        rounds_left=True,
    )
    assert guarded.status == "sufficient"
    assert guarded.can_proceed
    assert guarded.follow_up_tasks == []


