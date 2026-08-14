from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .agents import AgentSuite, PROMPT_BY_AGENT, build_agent_suite
from .catalog import CATALOG, catalog_type_reference, selected_catalog_reference, source_for_entry
from .config import Settings
from .models import build_chat_model
from .requirements_quality import requirements_quality_issues
from .research_guards import calibrate_finding, derive_research_need_hints, enforce_coverage_gate, enforce_minimum_plan
from .schemas import (
    CombinedTopologyValidation,
    ComponentPlan,
    CritiqueResult,
    DesignBrief,
    DeterministicValidation,
    FinalComponent,
    FinalTopologyOutput,
    KnowledgeNeed,
    RequirementsSpec,
    ResearchCoverage,
    ResearchPlan,
    ResearchSynthesis,
    SearchRecord,
    TaskFinding,
    TopologyDesign,
    TopologyReview,
)
from .search import SearchBatch, SearchClient, TavilySearchClient
from .state import OverallState
from .validation import repair_scope, validate_topology


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected model or dict, got {type(value).__name__}")


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def summarize_research_audit(state: dict[str, Any]) -> dict[str, Any]:
    records = state.get("research_search_log", [])
    sources = {
        item.get("url")
        for record in records
        for item in record.get("results", [])
        if item.get("url")
    }
    rejected: dict[str, int] = {}
    for record in records:
        for item in record.get("rejected_results", []):
            reason = str(item.get("reason") or "rejected")
            rejected[reason] = rejected.get(reason, 0) + 1
    verified_claims = sum(
        bool(claim.get("verified"))
        for finding in state.get("research_findings", [])
        for claim in finding.get("evidence_claims", [])
    )
    return {
        "searches_used": state.get("searches_used", 0),
        "rounds_used": state.get("research_round", 0),
        "selected_unique_sources": len(sources),
        "extracted_documents": sum(record.get("extracted_count", 0) for record in records),
        "duplicate_urls_skipped": sum(record.get("duplicate_count", 0) for record in records),
        "verified_evidence_claims": verified_claims,
        "rejected_by_reason": rejected,
    }


@dataclass
class Workflow:
    settings: Settings
    agents: AgentSuite
    search_client: SearchClient

    # ------------------------------------------------------------------
    # Requirements
    # ------------------------------------------------------------------
    def extract_requirements(self, state: OverallState) -> dict[str, Any]:
        prompt = state["user_query"]
        answers = state.get("clarification_answers") or []
        if answers:
            prompt += "\n\nAUTHORITATIVE CLARIFICATIONS:\n" + "\n".join(f"- {answer}" for answer in answers)
        result: RequirementsSpec = self.agents.extractor.invoke(
            [SystemMessage(PROMPT_BY_AGENT["extractor"]), HumanMessage(prompt)]
        )
        return {"requirements": result.model_dump(mode="json")}

    def critique_requirements(self, state: OverallState) -> dict[str, Any]:
        payload = (
            "ORIGINAL PROBLEM:\n"
            + state["user_query"]
            + "\n\nEXTRACTED REQUIREMENTS:\n"
            + _json(state["requirements"])
        )
        result: CritiqueResult = self.agents.critic.invoke(
            [SystemMessage(PROMPT_BY_AGENT["critic"]), HumanMessage(payload)]
        )
        deterministic_issues = requirements_quality_issues(state["requirements"], state["user_query"])
        for issue in deterministic_issues:
            if issue not in result.consistency_issues:
                result.consistency_issues.append(issue)
        return {"critique": result.model_dump(mode="json")}

    def route_after_requirements_critique(
        self, state: OverallState
    ) -> Literal["repair_requirements", "clarify_requirements", "finalize_requirements"]:
        critique = state["critique"]
        if critique.get("consistency_issues") and not state.get("requirements_repair_attempted", False):
            return "repair_requirements"
        has_blocking = critique.get("completeness_status") == "needs_clarification" and critique.get("blocking_questions")
        rounds_left = state.get("requirements_round", 0) < state.get("max_requirements_rounds", self.settings.max_requirements_rounds)
        if has_blocking and rounds_left and state.get("interactive", True):
            return "clarify_requirements"
        return "finalize_requirements"

    def repair_requirements(self, state: OverallState) -> dict[str, Any]:
        payload = (
            "ORIGINAL PROBLEM:\n"
            + state["user_query"]
            + "\n\nCURRENT EXTRACTION:\n"
            + _json(state["requirements"])
            + "\n\nSTRUCTURAL ISSUES TO REPAIR:\n"
            + _json(state["critique"].get("consistency_issues", []))
            + "\n\nReturn a complete corrected RequirementsSpec. Preserve explicit values and do not invent requirements."
        )
        result: RequirementsSpec = self.agents.extractor.invoke(
            [SystemMessage(PROMPT_BY_AGENT["extractor"]), HumanMessage(payload)]
        )
        return {
            "requirements": result.model_dump(mode="json"),
            "requirements_repair_attempted": True,
        }

    def clarify_requirements(self, state: OverallState) -> dict[str, Any]:
        questions = [
            {
                "id": question.get("id"),
                "question": question.get("question"),
                "why_it_matters": question.get("why_it_matters"),
            }
            for question in state["critique"].get("blocking_questions", [])
        ]
        response = interrupt({"kind": "requirements_clarification", "questions": questions})
        if isinstance(response, dict):
            raw_answers = response.get("answers") or []
            if response.get("use_assumptions"):
                raw_answers = ["Proceed with conservative, explicitly labeled assumptions for the listed questions."]
        elif isinstance(response, list):
            raw_answers = response
        else:
            raw_answers = [str(response)]
        answers = [str(value).strip() for value in raw_answers if str(value).strip()]
        return {
            "clarification_answers": answers,
            "requirements_round": state.get("requirements_round", 0) + 1,
        }

    def finalize_requirements(self, state: OverallState) -> dict[str, Any]:
        requirements = RequirementsSpec.model_validate(state["requirements"])
        critique = CritiqueResult.model_validate(state["critique"])
        existing = {assumption.id for assumption in requirements.assumptions}
        for assumption in critique.proposed_assumptions:
            if assumption.id not in existing:
                requirements.assumptions.append(assumption)
                existing.add(assumption.id)
        unresolved = critique.completeness_status == "needs_clarification"
        return {
            "requirements": requirements.model_dump(mode="json"),
            "requirements_unresolved": unresolved,
        }

    # ------------------------------------------------------------------
    # Adaptive research
    # ------------------------------------------------------------------
    def plan_research(self, state: OverallState) -> dict[str, Any]:
        begin_run = getattr(self.search_client, "begin_run", None)
        if callable(begin_run):
            begin_run()
        hints = derive_research_need_hints(state["requirements"])
        payload = (
            "REQUIREMENTS:\n"
            + _json(state["requirements"])
            + "\n\nDETERMINISTIC MINIMUM KNOWLEDGE-NEED HINTS (preserve these ids):\n"
            + _json([hint.model_dump(mode="json") for hint in hints])
        )
        result: ResearchPlan = self.agents.research_planner.invoke(
            [SystemMessage(PROMPT_BY_AGENT["research_planner"]), HumanMessage(payload)]
        )
        max_initial = min(state.get("max_searches", self.settings.max_searches), 6)
        guarded = enforce_minimum_plan(result, state["requirements"], max_initial_tasks=max_initial)
        return {
            "research_plan": guarded.model_dump(mode="json"),
            "knowledge_needs": [need.model_dump(mode="json") for need in guarded.knowledge_needs],
            "pending_research_tasks": [task.model_dump(mode="json") for task in guarded.tasks],
            "research_round": 0,
            "research_stalled_rounds": 0,
        }

    def research_dispatch(self, state: OverallState) -> dict[str, Any]:
        remaining = max(state.get("max_searches", self.settings.max_searches) - state.get("searches_used", 0), 0)
        tasks = (state.get("pending_research_tasks") or [])[:remaining]
        return {
            "pending_research_tasks": tasks,
            "research_round": state.get("research_round", 0) + 1,
        }

    def fan_out_research(self, state: OverallState):
        tasks = state.get("pending_research_tasks") or []
        if not tasks:
            return "assess_research_coverage"
        return [Send("web_research_worker", {"task": task}) for task in tasks]

    def web_research_worker(self, state: OverallState) -> dict[str, Any]:
        task = state["task"]
        error: str | None = None
        try:
            if task.get("action") == "extract" and task.get("target_urls") and hasattr(self.search_client, "extract"):
                raw_batch = self.search_client.extract(
                    task["target_urls"],
                    query=task["query"],
                    max_results=self.settings.max_documents_per_search,
                )
            else:
                raw_batch = self.search_client.search(
                    task["query"], max_results=self.settings.max_results_per_search
                )
            batch = raw_batch if isinstance(raw_batch, SearchBatch) else SearchBatch(
                results=raw_batch,
                candidate_count=len(raw_batch),
                extracted_count=sum(bool(item.get("is_full_content")) for item in raw_batch),
            )
            results = batch.results
        except Exception as exc:  # A failed query is evidence for the coverage gate, not a silent crash.
            results = []
            batch = SearchBatch(results=[])
            error = f"{type(exc).__name__}: {exc}"

        record = SearchRecord(
            task_id=task["id"],
            query=task["query"],
            round_number=task.get("round_number", state.get("research_round", 1)),
            results=results,
            error=error,
            candidate_count=batch.candidate_count,
            duplicate_count=batch.duplicate_count,
            selected_count=len(results),
            extracted_count=batch.extracted_count,
            rejected_results=batch.rejected,
        )
        if error:
            finding = TaskFinding(
                task_id=task["id"],
                need_ids=task.get("need_ids", []),
                category=task["category"],
                summary=f"Search failed: {error}",
                confidence="low",
                source_quality_notes="No pages were available to distill.",
            )
        else:
            payload = "RESEARCH TASK:\n" + _json(task) + "\n\nSELECTED SOURCE DOCUMENTS:\n" + _json(results)
            finding = self.agents.research_distiller.invoke(
                [SystemMessage(PROMPT_BY_AGENT["research_distiller"]), HumanMessage(payload)]
            )
            finding = finding.model_copy(deep=True)
            # Preserve routing identifiers even if a model tries to rewrite them.
            finding.task_id = task["id"]
            finding.need_ids = list(task.get("need_ids", []))
            finding.category = task["category"]
            finding = calibrate_finding(finding, results)
        return {
            "research_findings": [finding.model_dump(mode="json")],
            "research_search_log": [record.model_dump(mode="json")],
            "research_query_history": [task["query"]],
            "searches_used": 1,
        }

    def assess_research_coverage(self, state: OverallState) -> dict[str, Any]:
        needs = [KnowledgeNeed.model_validate(item) for item in state.get("knowledge_needs", [])]
        findings = [TaskFinding.model_validate(item) for item in state.get("research_findings", [])]
        payload = (
            "KNOWLEDGE NEEDS:\n"
            + _json([item.model_dump(mode="json") for item in needs])
            + "\n\nACCUMULATED FINDINGS:\n"
            + _json([item.model_dump(mode="json") for item in findings])
            + "\n\nSEARCH QUERIES ALREADY USED (do not duplicate):\n"
            + _json(state.get("research_query_history", []))
        )
        result: ResearchCoverage = self.agents.research_coverage.invoke(
            [SystemMessage(PROMPT_BY_AGENT["research_coverage"]), HumanMessage(payload)]
        )
        used = state.get("searches_used", 0)
        max_searches = state.get("max_searches", self.settings.max_searches)
        current_round = state.get("research_round", 1)
        max_rounds = state.get("max_research_rounds", self.settings.max_research_rounds)
        current_records = [
            item for item in state.get("research_search_log", []) if item.get("round_number", 1) == current_round
        ]
        new_sources = sum(item.get("selected_count", len(item.get("results", []))) for item in current_records)
        stalled_rounds = state.get("research_stalled_rounds", 0) + 1 if new_sources == 0 else 0
        guarded = enforce_coverage_gate(
            result,
            needs=needs,
            findings=findings,
            query_history=state.get("research_query_history", []),
            round_number=current_round,
            remaining_searches=max(max_searches - used, 0),
            rounds_left=current_round < max_rounds,
            stalled_rounds=stalled_rounds,
        )
        return {
            "research_coverage": guarded.model_dump(mode="json"),
            "pending_research_tasks": [task.model_dump(mode="json") for task in guarded.follow_up_tasks],
            "research_stalled_rounds": stalled_rounds,
        }

    def route_after_research_coverage(self, state: OverallState) -> Literal["research_dispatch", "synthesize_research", "research_failure"]:
        coverage = state["research_coverage"]
        if coverage.get("status") == "sufficient" and coverage.get("can_proceed"):
            return "synthesize_research"
        if coverage.get("status") == "needs_more" and coverage.get("follow_up_tasks"):
            return "research_dispatch"
        return "research_failure"

    def research_failure(self, state: OverallState) -> dict[str, Any]:
        return {
            "research_blocked": True,
            "failure": {
                "stage": "research_coverage",
                "message": "Critical topology knowledge remains unsupported after the configured research budget.",
                "coverage": state.get("research_coverage"),
                "searches_used": state.get("searches_used", 0),
            },
        }

    def synthesize_research(self, state: OverallState) -> dict[str, Any]:
        payload = (
            "REQUIREMENTS:\n"
            + _json(state["requirements"])
            + "\n\nCATALOG TYPES (recommend only these):\n"
            + catalog_type_reference()
            + "\n\nKNOWLEDGE NEEDS:\n"
            + _json(state.get("knowledge_needs", []))
            + "\n\nFINDINGS:\n"
            + _json(state.get("research_findings", []))
            + "\n\nFINAL COVERAGE REPORT:\n"
            + _json(state["research_coverage"])
        )
        result: ResearchSynthesis = self.agents.research_synthesizer.invoke(
            [SystemMessage(PROMPT_BY_AGENT["research_synthesizer"]), HumanMessage(payload)]
        )
        return {"research_synthesis": result.model_dump(mode="json")}

    # ------------------------------------------------------------------
    # Topology design and repair
    # ------------------------------------------------------------------
    def curate_design_brief(self, state: OverallState) -> dict[str, Any]:
        result: DesignBrief = self.agents.design_brief.invoke(
            [
                SystemMessage(PROMPT_BY_AGENT["design_brief"]),
                HumanMessage("REQUIREMENTS:\n" + _json(state["requirements"])),
            ]
        )
        return {"design_brief": result.model_dump(mode="json")}

    @staticmethod
    def _catalog_tool_trace(messages: list[Any]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        calls: dict[str, dict[str, Any]] = {}
        for message in messages:
            for call in getattr(message, "tool_calls", []) or []:
                item = {
                    "call_id": call.get("id"),
                    "tool": call.get("name"),
                    "arguments": call.get("args", {}),
                }
                calls[str(call.get("id"))] = item
                trace.append(item)
            if isinstance(message, ToolMessage):
                content = message.content
                if isinstance(content, list):
                    content = _json(content)
                trace.append(
                    {
                        "call_id": message.tool_call_id,
                        "tool": getattr(message, "name", None),
                        "result": str(content)[:12000],
                    }
                )
        return trace

    def plan_components(self, state: OverallState) -> dict[str, Any]:
        payload = (
            "DESIGN BRIEF:\n"
            + _json(state["design_brief"])
            + "\n\nRESEARCH SYNTHESIS:\n"
            + _json(state["research_synthesis"])
            + "\n\nSelect exact catalog keys with tools, then produce the component plan."
        )
        previous_validation = state.get("topology_validation")
        if previous_validation and previous_validation.get("verdict") == "invalid":
            payload += (
                "\n\nREPAIR PASS. CURRENT COMPONENT PLAN:\n"
                + _json(state.get("component_plan", {}))
                + "\n\nCURRENT TOPOLOGY:\n"
                + _json(state.get("topology", {}))
                + "\n\nVALIDATION ISSUES:\n"
                + _json(previous_validation)
                + "\nFix selection/safety/requirement issues without regressing valid parts."
            )

        result: dict[str, Any] | None = None
        trace: list[dict[str, Any]] = []
        for attempt in range(2):
            attempt_payload = payload
            if attempt:
                attempt_payload += (
                    "\n\nYour previous pass did not demonstrate catalog inspection. You MUST call "
                    "list_component_types plus search/list/detail tools before returning."
                )
            result = self.agents.component_planner.invoke({"messages": [HumanMessage(attempt_payload)]})
            trace = self._catalog_tool_trace(result.get("messages", []))
            tool_names = {item.get("tool") for item in trace if item.get("tool")}
            if "list_component_types" in tool_names and tool_names.intersection(
                {"list_components", "search_catalog", "get_component_details", "compare_components"}
            ):
                break
        if result is None or "structured_response" not in result:
            raise RuntimeError("Catalog-aware component planner returned no structured_response")
        plan = ComponentPlan.model_validate(result["structured_response"])
        return {
            "component_plan": plan.model_dump(mode="json"),
            "catalog_tool_trace": trace,
        }

    def build_netlist(self, state: OverallState) -> dict[str, Any]:
        plan = ComponentPlan.model_validate(state["component_plan"])
        keys = [component.catalog_key for component in plan.components]
        payload = (
            "COMPONENT PLAN:\n"
            + plan.model_dump_json(indent=2)
            + "\n\nEXACT SELECTED CATALOG METADATA AND PORTS:\n"
            + selected_catalog_reference(keys)
            + "\n\nRESEARCH SYNTHESIS:\n"
            + _json(state["research_synthesis"])
        )
        previous = state.get("topology_validation")
        if previous and previous.get("verdict") == "invalid" and state.get("topology"):
            payload += (
                "\n\nREPAIR MODE — CURRENT TOPOLOGY:\n"
                + _json(state["topology"])
                + "\n\nISSUES TO FIX:\n"
                + _json(previous)
            )
        result: TopologyDesign = self.agents.netlist_builder.invoke(
            [SystemMessage(PROMPT_BY_AGENT["netlist_builder"]), HumanMessage(payload)]
        )
        return {
            "topology": result.model_dump(mode="json"),
            "topology_round": state.get("topology_round", 0) + 1,
        }

    def validate_and_review_topology(self, state: OverallState) -> dict[str, Any]:
        deterministic = validate_topology(state["topology"], state["requirements"])
        payload = (
            "DESIGN BRIEF:\n"
            + _json(state["design_brief"])
            + "\n\nRESEARCH SYNTHESIS:\n"
            + _json(state["research_synthesis"])
            + "\n\nPROPOSED TOPOLOGY:\n"
            + _json(state["topology"])
            + "\n\nDETERMINISTIC VALIDATION:\n"
            + deterministic.model_dump_json(indent=2)
        )
        review: TopologyReview = self.agents.topology_reviewer.invoke(
            [SystemMessage(PROMPT_BY_AGENT["topology_reviewer"]), HumanMessage(payload)]
        )
        combined_issues = [item.model_dump(mode="json") for item in deterministic.issues] + [
            item.model_dump(mode="json") for item in review.design_issues
        ]
        blocking_gap = any(gap.get("blocking") for gap in state["topology"].get("catalog_gaps", []))
        verdict = "invalid" if deterministic.verdict == "invalid" or review.verdict == "invalid" or blocking_gap else "valid"
        scope = repair_scope(combined_issues)
        summary = (
            f"Deterministic validation: {deterministic.verdict}. Engineering review: {review.verdict}. "
            f"{review.summary}"
        )
        combined = CombinedTopologyValidation(
            verdict=verdict,
            deterministic=deterministic,
            design_review=review,
            repair_scope=scope,
            topology_round=state.get("topology_round", 1),
            summary=summary,
        )
        return {
            "deterministic_validation": deterministic.model_dump(mode="json"),
            "topology_validation": combined.model_dump(mode="json"),
        }

    def route_after_topology_validation(self, state: OverallState) -> Literal["plan_components", "build_netlist", "finalize_topology"]:
        validation = state["topology_validation"]
        if validation.get("verdict") == "valid":
            return "finalize_topology"
        rounds_left = state.get("topology_round", 0) < state.get("max_topology_rounds", self.settings.max_topology_rounds)
        if not rounds_left:
            return "finalize_topology"
        if validation.get("repair_scope") == "wiring":
            return "build_netlist"
        return "plan_components"

    def finalize_topology(self, state: OverallState) -> dict[str, Any]:
        topology = TopologyDesign.model_validate(state["topology"])
        validation = CombinedTopologyValidation.model_validate(state["topology_validation"])
        coverage = ResearchCoverage.model_validate(state["research_coverage"])
        selected: list[FinalComponent] = []
        for component in topology.components:
            entry = CATALOG.get(component.catalog_key, {})
            params = entry.get("params", {})
            source = source_for_entry(entry) or {}
            part_number = params.get("part_number")
            selected.append(
                FinalComponent(
                    id=component.id,
                    catalog_key=component.catalog_key,
                    name=str(entry.get("name") or component.catalog_key),
                    comp_type=component.comp_type,
                    role=component.role,
                    function_id=component.function_id,
                    ports=list(entry.get("ports", [])),
                    manufacturer=params.get("manufacturer"),
                    part_number=str(part_number) if part_number is not None else None,
                    source_url=source.get("url"),
                    configuration=component.configuration,
                    selection_basis=component.selection_basis,
                    requires_sizing_verification=component.requires_sizing_verification,
                )
            )
        warnings = validation.deterministic.issues + validation.design_review.design_issues
        has_warning = any(item.severity == "warning" for item in warnings)
        if validation.verdict == "invalid":
            status = "unresolved"
        elif has_warning:
            status = "validated_with_warnings"
        else:
            status = "validated"
        unresolved_descriptions = [
            item.description
            for item in warnings
            if item.severity == "error"
        ]
        output = FinalTopologyOutput(
            problem_id=state.get("problem_id", "CUSTOM"),
            title=state["requirements"].get("title", "Hydraulic topology"),
            status=status,
            scope_statement=(
                "Topology and catalog compatibility were validated. Dynamic simulation, detailed sizing, "
                "thermal analysis, structural checks, risk assessment, and fabrication approval are outside this workflow."
            ),
            design_narrative=topology.design_narrative,
            selected_components=selected,
            connections=topology.connections,
            external_interfaces=topology.external_interfaces,
            port_terminations=topology.port_terminations,
            function_implementations=topology.function_implementations,
            design_decisions=topology.design_decisions,
            catalog_gaps=topology.catalog_gaps,
            assumptions=topology.assumptions,
            open_issues=topology.open_issues + unresolved_descriptions,
            research_audit=summarize_research_audit(state),
            research_coverage=coverage,
            validation=validation,
        )
        return {"final_output": output.model_dump(mode="json")}


def build_graph(
    *,
    settings: Settings | None = None,
    agents: AgentSuite | None = None,
    search_client: SearchClient | None = None,
    checkpointer: Any | None = None,
):
    settings = settings or Settings.from_env()
    if agents is None or search_client is None:
        settings.validate_live_run()
    if agents is None:
        design_model = build_chat_model(settings, fast=False)
        fast_model = build_chat_model(settings, fast=True)
        agents = build_agent_suite(design_model, fast_model)
    if search_client is None:
        assert settings.tavily_api_key is not None
        search_client = TavilySearchClient(
            settings.tavily_api_key,
            max_documents_per_search=settings.max_documents_per_search,
            max_document_chars=settings.max_document_chars,
        )

    workflow = Workflow(settings=settings, agents=agents, search_client=search_client)
    graph = StateGraph(OverallState)

    graph.add_node("extract_requirements", workflow.extract_requirements)
    graph.add_node("critique_requirements", workflow.critique_requirements)
    graph.add_node("repair_requirements", workflow.repair_requirements)
    graph.add_node("clarify_requirements", workflow.clarify_requirements)
    graph.add_node("finalize_requirements", workflow.finalize_requirements)
    graph.add_node("plan_research", workflow.plan_research)
    graph.add_node("research_dispatch", workflow.research_dispatch)
    graph.add_node("web_research_worker", workflow.web_research_worker)
    graph.add_node("assess_research_coverage", workflow.assess_research_coverage)
    graph.add_node("research_failure", workflow.research_failure)
    graph.add_node("synthesize_research", workflow.synthesize_research)
    graph.add_node("curate_design_brief", workflow.curate_design_brief)
    graph.add_node("plan_components", workflow.plan_components)
    graph.add_node("build_netlist", workflow.build_netlist)
    graph.add_node("validate_topology", workflow.validate_and_review_topology)
    graph.add_node("finalize_topology", workflow.finalize_topology)

    graph.add_edge(START, "extract_requirements")
    graph.add_edge("extract_requirements", "critique_requirements")
    graph.add_conditional_edges(
        "critique_requirements",
        workflow.route_after_requirements_critique,
        {
            "repair_requirements": "repair_requirements",
            "clarify_requirements": "clarify_requirements",
            "finalize_requirements": "finalize_requirements",
        },
    )
    graph.add_edge("repair_requirements", "critique_requirements")
    graph.add_edge("clarify_requirements", "extract_requirements")
    graph.add_edge("finalize_requirements", "plan_research")
    graph.add_edge("plan_research", "research_dispatch")
    graph.add_conditional_edges(
        "research_dispatch",
        workflow.fan_out_research,
        ["web_research_worker", "assess_research_coverage"],
    )
    graph.add_edge("web_research_worker", "assess_research_coverage")
    graph.add_conditional_edges(
        "assess_research_coverage",
        workflow.route_after_research_coverage,
        {
            "research_dispatch": "research_dispatch",
            "synthesize_research": "synthesize_research",
            "research_failure": "research_failure",
        },
    )
    graph.add_edge("research_failure", END)
    graph.add_edge("synthesize_research", "curate_design_brief")
    graph.add_edge("curate_design_brief", "plan_components")
    graph.add_edge("plan_components", "build_netlist")
    graph.add_edge("build_netlist", "validate_topology")
    graph.add_conditional_edges(
        "validate_topology",
        workflow.route_after_topology_validation,
        {
            "plan_components": "plan_components",
            "build_netlist": "build_netlist",
            "finalize_topology": "finalize_topology",
        },
    )
    graph.add_edge("finalize_topology", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
