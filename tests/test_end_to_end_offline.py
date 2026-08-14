from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from hydraulic_mas.config import Settings
from hydraulic_mas.graph import build_graph
from hydraulic_mas.schemas import (
    Citation,
    ComponentPlan,
    CritiqueResult,
    DesignBrief,
    EvidenceClaim,
    FunctionBrief,
    KnowledgeNeed,
    PlannedComponent,
    RequirementsSpec,
    ResearchCoverage,
    ResearchPlan,
    ResearchSynthesis,
    ResearchTask,
    TaskFinding,
    TopologyDesign,
    TopologyReview,
)
from test_validation import requirements as requirements_dict
from test_validation import valid_topology


class StaticRunnable:
    def __init__(self, value):
        self.value = value

    def invoke(self, _input):
        return self.value


class Search:
    def search(self, query: str, *, max_results: int):
        return [
            {
                "title": "Hydraulic technical manual",
                "url": "https://example.com/manual",
                "content": "A cited hydraulic circuit and connection description.",
                "score": 0.9,
                "source_kind": "manufacturer",
                "quality_score": 0.9,
                "is_full_content": True,
            }
        ]


def _requirements_model() -> RequirementsSpec:
    raw = requirements_dict()
    raw.update(
        {
            "application": "horizontal slide",
            "functions": [
                {
                    **raw["functions"][0],
                    "name": "Slide",
                    "description": "Move the slide in both directions.",
                    "motion_phases": [],
                }
            ],
            "safety_requirements": [],
            "acceptance_criteria": [],
            "assumptions": [],
            "open_questions": [],
            "derived_design_drivers": [],
        }
    )
    return RequirementsSpec.model_validate(raw)


def _component_plan() -> ComponentPlan:
    topo = valid_topology()
    return ComponentPlan(
        planning_summary="Simple open-circuit plan.",
        engineering_decision_log=["Use one fixed pump, relief, tandem DCV, and double-acting cylinder."],
        components=[
            PlannedComponent(
                id=item["id"],
                catalog_key=item["catalog_key"],
                comp_type=item["comp_type"],
                role=item["role"],
                function_id=item.get("function_id"),
                selection_basis="Scripted catalog-backed integration fixture.",
            )
            for item in topo["components"]
        ],
        connection_intent=[],
        design_ledger=[],
    )


class ComponentPlanner:
    def invoke(self, _input):
        messages = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "list_component_types", "args": {}, "id": "call-1", "type": "tool_call"},
                    {"name": "list_components", "args": {"comp_type": "pump"}, "id": "call-2", "type": "tool_call"},
                ],
            )
        ]
        return {"messages": messages, "structured_response": _component_plan()}


def test_full_graph_reaches_validated_output_without_network() -> None:
    requirements = _requirements_model()
    initial_needs = [
        KnowledgeNeed(
            id="system_power_and_relief",
            decision="power and relief circuit",
            why_needed="safety",
        ),
        KnowledgeNeed(
            id="function_slide_directional_control",
            decision="slide control circuit",
            why_needed="controllability",
            related_function_ids=["slide"],
        ),
    ]
    research_plan = ResearchPlan(
        rationale="Cover both minimum needs.",
        knowledge_needs=initial_needs,
        tasks=[
            ResearchTask(
                id=f"task-{index}",
                category="circuit_pattern",
                query=f"hydraulic {need.decision} manufacturer manual",
                objective=need.decision,
                need_ids=[need.id],
                related_function_ids=need.related_function_ids,
            )
            for index, need in enumerate(initial_needs, start=1)
        ],
    )
    finding = TaskFinding(
        task_id="overwritten-by-worker",
        category="circuit_pattern",
        summary="The result describes the required circuit.",
        connection_guidance=["pump.P -> dcv.P", "relief.T -> tank.R"],
        citations=[Citation(title="Manual", url="https://example.com/manual", source_kind="manufacturer")],
        evidence_claims=[
            EvidenceClaim(
                id="claim-1",
                claim="The source gives a hydraulic circuit connection.",
                excerpt="A cited hydraulic circuit and connection description.",
                source_url="https://example.com/manual",
                source_kind="manufacturer",
                claim_type="connection",
            )
        ],
        confidence="medium",
    )
    coverage = ResearchCoverage(
        status="sufficient",
        can_proceed=True,
        summary="Both critical needs are supported.",
        items=[],
    )
    design_brief = DesignBrief(
        application="horizontal slide",
        hard_constraints=["100 bar maximum"],
        functions=[
            FunctionBrief(
                function_id="slide",
                summary="Bidirectional horizontal slide.",
                actuator="double-acting cylinder",
                load_type="resistive",
                holding="none",
                speed_control="meter-in if later required",
            )
        ],
    )
    suite = SimpleNamespace(
        extractor=StaticRunnable(requirements),
        critic=StaticRunnable(CritiqueResult(completeness_status="sufficient", rationale="Complete.")),
        research_planner=StaticRunnable(research_plan),
        research_distiller=StaticRunnable(finding),
        research_coverage=StaticRunnable(coverage),
        research_synthesizer=StaticRunnable(ResearchSynthesis(summary="Grounded circuit pattern.")),
        design_brief=StaticRunnable(design_brief),
        component_planner=ComponentPlanner(),
        netlist_builder=StaticRunnable(TopologyDesign.model_validate(valid_topology())),
        topology_reviewer=StaticRunnable(TopologyReview(verdict="valid", summary="Behavior is coherent.")),
    )
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
        max_research_rounds=2,
        max_searches=4,
        max_topology_rounds=2,
    )
    graph = build_graph(settings=settings, agents=suite, search_client=Search())
    result = graph.invoke(
        {
            "problem_id": "TEST",
            "user_query": "Move a horizontal slide.",
            "interactive": False,
            "clarification_answers": [],
            "requirements_round": 0,
            "max_requirements_rounds": 1,
            "research_findings": [],
            "research_search_log": [],
            "research_query_history": [],
            "searches_used": 0,
            "research_round": 0,
            "max_research_rounds": 2,
            "max_searches": 4,
            "topology_round": 0,
            "max_topology_rounds": 2,
        },
        config={"configurable": {"thread_id": "offline-e2e"}, "recursion_limit": 100},
    )
    assert result["searches_used"] == 2
    assert result["research_coverage"]["status"] == "sufficient"
    assert result["final_output"]["status"] == "validated"
    assert result["final_output"]["research_audit"]["verified_evidence_claims"] == 2
    assert len(result["final_output"]["selected_components"]) == 7
