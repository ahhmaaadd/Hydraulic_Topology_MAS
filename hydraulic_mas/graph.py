from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain.agents.structured_output import StructuredOutputValidationError
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .agents import AgentSuite, PROMPT_BY_AGENT, build_agent_suite
from .candidate_selection import evaluate_and_select_candidates
from .catalog import CATALOG, catalog_type_reference, selected_catalog_reference
from .config import Settings
from .decision_flow import inject_design_brief_decisions, inject_topology_decisions
from .models import build_chat_model
from .gap_policy import blocking_catalog_gaps
from .patterns import format_pattern_hints
from .requirements_quality import normalize_requirements, requirements_quality_issues
from .sizing.agent import build_sizing_planner, build_sizing_repairer
from .sizing.contract import compile_contract
from .sizing.planner import _load_tolerance as sizing_load_tolerance, size_problem
from .sizing.schemas_sizing import SizingPlanSet, SizingRepairPlan
from .sizing.selection import evaluate_and_select_sizing, score_candidate
from .research_guards import calibrate_finding, derive_research_need_hints, enforce_coverage_gate, enforce_minimum_plan
from .schemas import (
    CombinedTopologyValidation,
    ComponentPlan,
    ComponentPlanSet,
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
    ResearchTask,
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


def _clarification_key(question: dict[str, Any]) -> str:
    """Stable semantic key that survives model-generated question-id churn."""
    topic = str(question.get("topic") or "other")
    if topic == "other":
        text = f"{question.get('question', '')} {question.get('why_it_matters', '')}".casefold()
        semantic_words = {
            "load_character": ("overrun", "gravity", "resistive load", "load direction"),
            "orientation": ("horizontal", "vertical", "inclined", "orientation"),
            "motion_direction": ("extend", "retract", "cap end", "rod end", "direction"),
            "holding_safety": ("hold", "drift", "hose burst", "power loss"),
            "sequence_trigger": ("trigger", "sequence", "automatically", "after pressure"),
            "synchronization": ("synchron", "platen", "multiple cylinder"),
        }
        topic = next(
            (name for name, words in semantic_words.items() if any(word in text for word in words)),
            "other",
        )
    related = sorted(str(value) for value in question.get("related", []) if value)
    if topic != "other":
        return f"{topic}|{'|'.join(related)}"
    return f"id|{question.get('id')}"


def _clarification_context(state: dict[str, Any]) -> str:
    records = state.get("clarification_records") or []
    if records:
        return _json(records)
    return _json(state.get("clarification_answers") or [])


# One step outward for each repair scope. Wiring problems that survive a
# rewiring round are really component-choice problems; component-choice problems
# that survive a reselection round are really requirement problems. "research"
# escalates to "selection" because an evidence complaint that outlives one
# targeted search is a design decision, not a missing document.
_ESCALATED_SCOPE = {
    "wiring": "selection",
    "research": "selection",
    "selection": "requirements",
}


def _topology_error_subjects(issues: list[dict[str, Any]], topology: dict[str, Any]) -> frozenset[str]:
    """The component ids an unresolved error set is actually about.

    The code-based signature assumes the reviewer names the same fault the same
    way twice. It does not. Across four P7-05 rounds the identical finding
    arrived as FWD_SEQ_PILOT_LOST_IN_CLAMP_RELEASE,
    FWD_SEQ_NOT_CLAMP_PRESSURE_INTERLOCKED, FWD_SEQ_PILOT_IS_ISOLATED_BY_LOAD_LOCK
    and FWD_SEQUENCE_NOT_CLAMP_PRESSURE_REFERENCED - four names, one problem,
    four fresh signatures, so no-progress never triggered and the run spent its
    whole budget re-describing the same three components.

    Component ids are stable where free-form codes are not, so track those too.
    """
    component_ids = {str(item.get("id")) for item in topology.get("components", [])}
    return frozenset(
        str(value)
        for issue in issues
        if issue.get("severity") == "error"
        for value in issue.get("related", [])
        if str(value) in component_ids
    )


def _subjects_overlap(current: frozenset[str], previous: frozenset[str]) -> float:
    """Jaccard overlap between two rounds' error subjects."""
    if not current or not previous:
        return 0.0
    return len(current & previous) / len(current | previous)


# Two consecutive rounds blaming substantially the same components have not made
# progress, whatever the error text says.
_SUBJECT_STALL_OVERLAP = 0.6


def _topology_error_signature(issues: list[dict[str, Any]]) -> str:
    """Hash only the unresolved error codes and their subjects.

    Deliberately excludes components, connections and prose so that cosmetic
    churn between repair rounds does not look like progress.
    """
    codes = sorted(
        {
            (
                str(issue.get("code")),
                tuple(sorted(str(value) for value in issue.get("related", []) if value)),
            )
            for issue in issues
            if issue.get("severity") == "error"
        }
    )
    if not codes:
        return ""
    return hashlib.sha256(json.dumps(codes, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _topology_validation_fingerprint(
    topology: dict[str, Any],
    issues: list[dict[str, Any]],
) -> str:
    """Hash the repairable error set and structural topology, ignoring prose."""
    payload = {
        "errors": sorted(
            (
                str(issue.get("code")),
                str(issue.get("scope")),
                tuple(sorted(str(value) for value in issue.get("related", []) if value)),
            )
            for issue in issues
            if issue.get("severity") == "error"
        ),
        "components": sorted(
            (str(item.get("id")), str(item.get("catalog_key")))
            for item in topology.get("components", [])
        ),
        "connections": sorted(
            (
                str(item.get("from_component")),
                str(item.get("from_port")),
                str(item.get("to_component")),
                str(item.get("to_port")),
            )
            for item in topology.get("connections", [])
        ),
        "phase_states": sorted(
            (
                str(config.get("phase_id")),
                tuple(
                    sorted(
                        (str(item.get("component_id")), str(item.get("state")))
                        for item in config.get("component_states", [])
                    )
                ),
            )
            for config in topology.get("phase_configurations", [])
        ),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


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
            prompt += (
                "\n\nAUTHORITATIVE CLARIFICATIONS (these are user-supplied facts; preserve them):\n"
                + _clarification_context(state)
            )
        result: RequirementsSpec = self.agents.extractor.invoke(
            [SystemMessage(PROMPT_BY_AGENT["extractor"]), HumanMessage(prompt)]
        )
        return {"requirements": result.model_dump(mode="json")}

    def critique_requirements(self, state: OverallState) -> dict[str, Any]:
        requirements, normalization_notes = normalize_requirements(
            state["requirements"], state["user_query"]
        )
        payload = (
            "ORIGINAL PROBLEM:\n"
            + state["user_query"]
            + "\n\nAUTHORITATIVE USER CLARIFICATIONS:\n"
            + _clarification_context(state)
            + "\n\nEXTRACTED REQUIREMENTS:\n"
            + _json(requirements)
            + "\n\nDo not question or contradict an authoritative clarification. A differently "
            "worded question about an already answered semantic topic is resolved."
        )
        result: CritiqueResult = self.agents.critic.invoke(
            [SystemMessage(PROMPT_BY_AGENT["critic"]), HumanMessage(payload)]
        )
        advisory_issues = list(result.consistency_issues)
        deterministic_issues = requirements_quality_issues(requirements, state["user_query"])
        for issue in deterministic_issues:
            if issue not in result.consistency_issues:
                result.consistency_issues.append(issue)
        return {
            "requirements": requirements.model_dump(mode="json"),
            "critique": result.model_dump(mode="json"),
            "requirements_structural_issues": deterministic_issues,
            "requirements_advisories": advisory_issues,
            "requirements_normalizations": normalization_notes,
        }

    def route_after_requirements_critique(
        self, state: OverallState
    ) -> Literal["repair_requirements", "clarify_requirements", "finalize_requirements"]:
        critique = state["critique"]
        structural_issues = state.get("requirements_structural_issues", [])
        if structural_issues and not state.get("requirements_repair_attempted", False):
            return "repair_requirements"
        has_blocking = critique.get("completeness_status") == "needs_clarification"
        has_questions = bool(self._blocking_questions(state))
        rounds_left = state.get("requirements_round", 0) < state.get("max_requirements_rounds", self.settings.max_requirements_rounds)
        if has_blocking and has_questions and rounds_left and state.get("interactive", True):
            return "clarify_requirements"
        return "finalize_requirements"

    @staticmethod
    def _blocking_questions(state: OverallState) -> list[dict[str, Any]]:
        resolved = set(state.get("resolved_clarification_keys") or [])

        def unresolved(question: dict[str, Any]) -> bool:
            return (
                _clarification_key(question) not in resolved
                and f"id|{question.get('id')}" not in resolved
            )

        questions = [
            question
            for question in state.get("critique", {}).get("blocking_questions", [])
            if question.get("blocking", True) and unresolved(question)
        ]
        if questions:
            return questions
        if state.get("critique", {}).get("completeness_status") != "needs_clarification":
            return []
        return [
            question
            for question in state.get("requirements", {}).get("open_questions", [])
            if question.get("blocking", False) and unresolved(question)
        ]

    def repair_requirements(self, state: OverallState) -> dict[str, Any]:
        payload = (
            "ORIGINAL PROBLEM:\n"
            + state["user_query"]
            + "\n\nAUTHORITATIVE USER CLARIFICATIONS:\n"
            + _clarification_context(state)
            + "\n\nCURRENT EXTRACTION:\n"
            + _json(state["requirements"])
            + "\n\nSTRUCTURAL ISSUES TO REPAIR:\n"
            + _json(state.get("requirements_structural_issues", []))
            + "\n\nReturn a complete corrected RequirementsSpec. Preserve explicit values and do not invent "
            "requirements. Do not change topology-neutral tolerances merely because the critic listed them as "
            "advisories."
        )
        result: RequirementsSpec = self.agents.extractor.invoke(
            [SystemMessage(PROMPT_BY_AGENT["extractor"]), HumanMessage(payload)]
        )
        # Normalize the repaired spec before it reaches the second critique.
        # Deterministic corrections should not have to survive another LLM round
        # to take effect, and the critic reasons better about a spec whose typed
        # decisions are already resolved.
        normalized, notes = normalize_requirements(result, state["user_query"])
        return {
            "requirements": normalized.model_dump(mode="json"),
            "requirements_structural_issues": requirements_quality_issues(
                normalized, state["user_query"]
            ),
            "requirements_normalizations": [
                *state.get("requirements_normalizations", []),
                *notes,
            ],
            "requirements_repair_attempted": True,
        }

    def clarify_requirements(self, state: OverallState) -> dict[str, Any]:
        questions = [
            {
                "id": question.get("id"),
                "topic": question.get("topic", "other"),
                "question": question.get("question"),
                "why_it_matters": question.get("why_it_matters"),
                "related": list(question.get("related") or []),
            }
            for question in self._blocking_questions(state)
        ]
        response = interrupt({"kind": "requirements_clarification", "questions": questions})
        if isinstance(response, dict):
            raw_answers = response.get("answers") or []
            if response.get("use_assumptions"):
                raw_answers = [
                    {
                        "id": question.get("id"),
                        "answer": "Proceed with a conservative, explicitly labeled assumption for this question.",
                    }
                    for question in questions
                ]
        elif isinstance(response, list):
            raw_answers = response
        else:
            raw_answers = [str(response)]
        answers: list[str] = []
        records: list[dict[str, Any]] = []
        resolved_keys: list[str] = []
        for index, value in enumerate(raw_answers):
            question = questions[min(index, len(questions) - 1)] if questions else {}
            if isinstance(value, dict):
                answer = str(value.get("answer") or value.get("value") or "").strip()
                question_id = str(value.get("id") or question.get("id") or "question")
            else:
                rendered = str(value).strip()
                question_id = str(question.get("id") or "question")
                prefix = f"{question_id}:"
                answer = rendered[len(prefix):].strip() if rendered.startswith(prefix) else rendered
            if not answer:
                continue
            record = {
                "id": question_id,
                "topic": question.get("topic", "other"),
                "related": list(question.get("related") or []),
                "question": question.get("question"),
                "answer": answer,
            }
            answers.append(f"{question_id}: {answer}")
            records.append(record)
            resolved_keys.extend([_clarification_key(record), f"id|{question_id}"])
        return {
            "clarification_answers": answers,
            "clarification_records": records,
            "resolved_clarification_keys": resolved_keys,
            "requirements_round": state.get("requirements_round", 0) + 1,
            "requirements_repair_attempted": False,
        }

    def finalize_requirements(self, state: OverallState) -> dict[str, Any]:
        requirements, normalization_notes = normalize_requirements(
            state["requirements"], state["user_query"]
        )
        critique = CritiqueResult.model_validate(state["critique"])
        existing = {assumption.id for assumption in requirements.assumptions}
        merged_assumption_ids: list[str] = []
        for assumption in critique.proposed_assumptions:
            if assumption.id not in existing:
                requirements.assumptions.append(assumption)
                existing.add(assumption.id)
                merged_assumption_ids.append(assumption.id)

        unresolved_questions = self._blocking_questions(state)
        unresolved_keys = {_clarification_key(question) for question in unresolved_questions}
        downgraded_question_ids: list[str] = []
        for question in requirements.open_questions:
            if question.blocking and (
                critique.completeness_status != "needs_clarification"
                or _clarification_key(question.model_dump(mode="json")) not in unresolved_keys
            ):
                question.blocking = False
                downgraded_question_ids.append(question.id)

        structural_issues = requirements_quality_issues(requirements, state["user_query"])
        needs_clarification = (
            critique.completeness_status == "needs_clarification"
            and bool(unresolved_questions)
        )
        unresolved = needs_clarification or bool(structural_issues)
        advisory_issues = [
            issue
            for issue in critique.consistency_issues
            if issue not in structural_issues
        ]
        gate = {
            "decision": "blocked" if unresolved else "proceed",
            "completeness_status": (
                critique.completeness_status if needs_clarification else
                "proceed_with_assumptions" if critique.completeness_status == "needs_clarification" else
                critique.completeness_status
            ),
            "critic_completeness_status": critique.completeness_status,
            "blocking_question_ids": [question.get("id") for question in unresolved_questions],
            "resolved_clarification_keys": sorted(set(state.get("resolved_clarification_keys") or [])),
            "structural_issues": structural_issues,
            "advisory_issues": advisory_issues,
            "merged_assumption_ids": merged_assumption_ids,
            "downgraded_nonblocking_question_ids": downgraded_question_ids,
            "normalizations": [*state.get("requirements_normalizations", []), *normalization_notes],
        }
        return {
            "requirements": requirements.model_dump(mode="json"),
            "requirements_unresolved": unresolved,
            "requirements_structural_issues": structural_issues,
            "requirements_advisories": advisory_issues,
            "requirements_gate": gate,
        }

    @staticmethod
    def route_after_finalize_requirements(state: OverallState) -> Literal["requirements_failure", "plan_research"]:
        return "requirements_failure" if state.get("requirements_unresolved") else "plan_research"

    def requirements_failure(self, state: OverallState) -> dict[str, Any]:
        return {
            "failure": {
                "stage": "requirements",
                "message": "Circuit-changing requirements remain unresolved after clarification/repair limits.",
                "critique": state.get("critique"),
                "requirements_gate": state.get("requirements_gate"),
            }
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
            + "\n\nDETERMINISTIC GENERIC PATTERN HINTS:\n"
            + format_pattern_hints(state["requirements"])
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
            + "\n\nDETERMINISTIC GENERIC PATTERN HINTS (compose these; they are not solved examples):\n"
            + format_pattern_hints(state["requirements"])
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
                HumanMessage(
                    "REQUIREMENTS:\n"
                    + _json(state["requirements"])
                    + "\n\nDETERMINISTIC GENERIC PATTERN HINTS:\n"
                    + format_pattern_hints(state["requirements"])
                ),
            ]
        )
        brief = inject_design_brief_decisions(result.model_dump(mode="json"), state["requirements"])
        return {"design_brief": DesignBrief.model_validate(brief).model_dump(mode="json")}

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
            + "\n\nDETERMINISTIC GENERIC PATTERN HINTS:\n"
            + format_pattern_hints(state["requirements"])
            + "\n\nRESEARCH SYNTHESIS:\n"
            + _json(state["research_synthesis"])
            + "\n\nSelect generic functional class keys with tools, compare two or three viable "
            "topology candidates, and return the candidate set."
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
        recovery_events: list[dict[str, Any]] = []
        catalog_retry_needed = False
        for attempt in range(3):
            attempt_payload = payload
            if catalog_retry_needed:
                attempt_payload += (
                    "\n\nYour previous pass did not demonstrate catalog inspection. You MUST call "
                    "list_component_types plus search/list/detail tools before returning."
                )
            if recovery_events:
                attempt_payload += (
                    "\n\nSTRUCTURED OUTPUT CORRECTION. The previous ComponentPlanSet was rejected. "
                    "For add_connection put the new ConnectionIntent in replacement_connection; "
                    "for delete_connection use target_connection; for replace_connection provide both. "
                    "Never emit an action whose required payload is null. Re-run the required catalog "
                    "tools and return a complete corrected candidate set. Last validation error:\n"
                    + recovery_events[-1]["error"]
                )
            try:
                result = self.agents.component_planner.invoke({"messages": [HumanMessage(attempt_payload)]})
            except StructuredOutputValidationError as exc:
                recovery_events.append(
                    {
                        "attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:4000],
                        "outcome": "retry",
                    }
                )
                result = None
                trace = []
                catalog_retry_needed = True
                continue
            trace = self._catalog_tool_trace(result.get("messages", []))
            tool_names = {item.get("tool") for item in trace if item.get("tool")}
            if "list_component_types" in tool_names and tool_names.intersection(
                {
                    "list_components",
                    "search_catalog",
                    "get_component_details",
                    "compare_components",
                    "get_port_reference",
                }
            ):
                break
            catalog_retry_needed = True
        if result is None or "structured_response" not in result:
            detail = recovery_events[-1]["error"] if recovery_events else "no structured_response"
            raise RuntimeError(
                "Catalog-aware component planner did not return a valid ComponentPlanSet after "
                f"three bounded attempts: {detail}"
            )
        structured = result["structured_response"]
        if isinstance(structured, ComponentPlanSet):
            plan_set = structured
        elif isinstance(structured, ComponentPlan):
            # Backward-compatible support for injected/offline agents. Live agents
            # are constrained to ComponentPlanSet by their response schema.
            second = structured.model_copy(
                deep=True,
                update={
                    "candidate_id": f"{structured.candidate_id}_alternative",
                    "approach": f"Alternative review of {structured.approach}",
                },
            )
            plan_set = ComponentPlanSet(
                candidates=[structured, second],
                preferred_candidate_id=structured.candidate_id,
                comparison_summary="Offline single-plan compatibility wrapper.",
            )
        else:
            plan_set = ComponentPlanSet.model_validate(structured)
        selected, candidates, evaluations = evaluate_and_select_candidates(
            plan_set,
            state["requirements"],
        )
        repair_actions = [item.model_dump(mode="json") for item in selected.repair_actions]
        return {
            "component_candidates": [item.model_dump(mode="json") for item in candidates],
            "candidate_evaluations": [item.model_dump(mode="json") for item in evaluations],
            "component_plan": selected.model_dump(mode="json"),
            "catalog_tool_trace": trace,
            "component_planner_recovery": recovery_events,
            "repair_history": repair_actions,
        }

    def build_netlist(self, state: OverallState) -> dict[str, Any]:
        plan = ComponentPlan.model_validate(state["component_plan"])
        keys = [component.catalog_key for component in plan.components]
        payload = (
            "COMPONENT PLAN:\n"
            + plan.model_dump_json(indent=2)
            + "\n\nSELECTED GENERIC CLASS METADATA, STATES AND PORTS:\n"
            + selected_catalog_reference(keys)
            + "\n\nDETERMINISTIC GENERIC PATTERN HINTS:\n"
            + format_pattern_hints(state["requirements"])
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
        topology = inject_topology_decisions(result.model_dump(mode="json"), plan.model_dump(mode="json"))
        return {
            "topology": TopologyDesign.model_validate(topology).model_dump(mode="json"),
            "topology_round": state.get("topology_round", 0) + 1,
        }

    def validate_and_review_topology(self, state: OverallState) -> dict[str, Any]:
        deterministic = validate_topology(state["topology"], state["requirements"])
        payload = (
            "DESIGN BRIEF:\n"
            + _json(state["design_brief"])
            + "\n\nDETERMINISTIC GENERIC PATTERN HINTS:\n"
            + format_pattern_hints(state["requirements"])
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
        blocking_gap = bool(blocking_catalog_gaps(state["topology"].get("catalog_gaps", [])))
        verdict = "invalid" if deterministic.verdict == "invalid" or review.verdict == "invalid" or blocking_gap else "valid"
        scope = repair_scope(combined_issues)
        fingerprint = _topology_validation_fingerprint(state["topology"], combined_issues)
        previous_fingerprints = state.get("topology_validation_fingerprints") or []
        no_progress = verdict == "invalid" and fingerprint in previous_fingerprints

        # v0.4.0 scope escalation.
        #
        # The exact-fingerprint stop only catches a design that is byte-identical
        # to a previous round. In practice a repair round would move a component,
        # rename a role or add one more valve, produce a *new* fingerprint and the
        # *same* error codes, and the loop would spend its whole budget inside one
        # scope that cannot fix the fault. Rewiring cannot repair a wrong component
        # choice, and reselecting components cannot repair a contradictory
        # requirement.
        #
        # So track the error *codes* separately from the full fingerprint. When the
        # same unresolved code set survives a repair round, escalate one level
        # outward instead of retrying the scope that just failed.
        error_signature = _topology_error_signature(combined_issues)
        previous_signatures = state.get("topology_error_signatures") or []
        subjects = _topology_error_subjects(combined_issues, state["topology"])
        previous_subjects = state.get("topology_error_subjects") or []
        repeated_subjects = any(
            _subjects_overlap(subjects, frozenset(earlier)) >= _SUBJECT_STALL_OVERLAP
            for earlier in previous_subjects
        )
        escalate = (
            verdict == "invalid"
            and not no_progress
            and (
                (bool(error_signature) and error_signature in previous_signatures)
                or repeated_subjects
            )
        )
        if escalate:
            escalated_scope = _ESCALATED_SCOPE.get(scope, scope)
            if escalated_scope != scope:
                reason = (
                    f"the error set {error_signature}"
                    if error_signature in previous_signatures
                    else f"errors about {sorted(subjects)}"
                )
                summary_escalation = (
                    f" Repair scope escalated from {scope!r} to {escalated_scope!r}: "
                    f"{reason} survived a {scope} repair round."
                )
                scope = escalated_scope
            else:
                summary_escalation = ""
        else:
            summary_escalation = ""

        stop_reason = (
            f"No progress: topology and repairable error fingerprint {fingerprint} repeated."
            if no_progress
            else ""
        )
        summary = (
            f"Deterministic validation: {deterministic.verdict}. Engineering review: {review.verdict}. "
            f"{review.summary}"
        )
        summary += summary_escalation
        if stop_reason:
            summary += " " + stop_reason
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
            "topology_validation_fingerprints": [fingerprint],
            "topology_error_signatures": [error_signature] if error_signature else [],
            "topology_error_subjects": [sorted(subjects)] if subjects else [],
            "topology_repair_escalated": bool(summary_escalation),
            "topology_no_progress": no_progress,
            "repair_stop_reason": stop_reason,
        }

    def repair_topology_requirements(self, state: OverallState) -> dict[str, Any]:
        """Repair an upstream contract once instead of asking the designer to work around it."""
        validation = state["topology_validation"]
        issues = validation.get("deterministic", {}).get("issues", []) + validation.get(
            "design_review", {}
        ).get("design_issues", [])
        requirements_issues = [
            issue
            for issue in issues
            if issue.get("severity") == "error" and issue.get("scope") == "requirements"
        ]
        payload = (
            "ORIGINAL PROBLEM:\n"
            + state["user_query"]
            + "\n\nAUTHORITATIVE USER CLARIFICATIONS:\n"
            + _clarification_context(state)
            + "\n\nCURRENT REQUIREMENTS:\n"
            + _json(state["requirements"])
            + "\n\nTOPOLOGY VALIDATION FOUND THESE UPSTREAM REQUIREMENT ERRORS:\n"
            + _json(requirements_issues)
            + "\n\nReturn one complete corrected RequirementsSpec. Preserve explicit facts and answered "
            "clarifications. Do not invent a hydraulic sequence for an operator-commanded mode."
        )
        result: RequirementsSpec = self.agents.extractor.invoke(
            [SystemMessage(PROMPT_BY_AGENT["extractor"]), HumanMessage(payload)]
        )
        normalized, notes = normalize_requirements(result, state["user_query"])
        remaining = requirements_quality_issues(normalized, state["user_query"])
        return {
            "requirements": normalized.model_dump(mode="json"),
            "requirements_structural_issues": remaining,
            "requirements_normalizations": [*state.get("requirements_normalizations", []), *notes],
            "topology_requirements_repair_count": state.get("topology_requirements_repair_count", 0) + 1,
            "topology_requirements_repair_failed": bool(remaining),
            "topology_no_progress": False,
        }

    @staticmethod
    def route_after_topology_requirements_repair(
        state: OverallState,
    ) -> Literal["curate_design_brief", "finalize_topology"]:
        return (
            "finalize_topology"
            if state.get("topology_requirements_repair_failed")
            else "curate_design_brief"
        )

    def plan_targeted_research(self, state: OverallState) -> dict[str, Any]:
        """Turn an unsupported design-pattern issue into one bounded evidence need."""
        validation = state["topology_validation"]
        issues = validation.get("deterministic", {}).get("issues", []) + validation.get(
            "design_review", {}
        ).get("design_issues", [])
        research_issues = [
            item
            for item in issues
            if item.get("severity") == "error" and item.get("scope") == "research"
        ]
        existing = list(state.get("knowledge_needs", []))
        existing_ids = {item.get("id") for item in existing}
        round_number = state.get("research_round", 0) + 1
        remaining = max(
            state.get("max_searches", self.settings.max_searches) - state.get("searches_used", 0),
            0,
        )
        needs: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        for index, issue in enumerate(research_issues[:remaining], start=1):
            raw_code = str(issue.get("code") or f"issue_{index}").casefold()
            safe_code = "".join(character if character.isalnum() else "_" for character in raw_code).strip("_")
            need_id = f"topology_r{state.get('topology_round', 0)}_{safe_code}"
            if need_id in existing_ids:
                continue
            related = list(issue.get("related") or [])
            need = KnowledgeNeed(
                id=need_id,
                decision=f"Resolve topology review issue: {issue.get('description')}",
                why_needed="The proposed topology uses a pattern whose functional behavior is not yet supported.",
                related_function_ids=related,
                critical=True,
                critical_reason="Validation cannot establish correctness without targeted technical evidence.",
            )
            task = ResearchTask(
                id=f"targeted_{need_id}",
                category="circuit_pattern",
                query=(
                    "hydraulic "
                    + str(issue.get("description") or issue.get("code") or "circuit pattern")
                    + " manufacturer technical manual port connections operating principle"
                ),
                objective=need.decision,
                need_ids=[need.id],
                related_function_ids=related,
                source_preference="manufacturer manual, engineering textbook, university notes, or paper",
                round_number=round_number,
            )
            needs.append(need.model_dump(mode="json"))
            tasks.append(task.model_dump(mode="json"))
            existing_ids.add(need_id)
        return {
            "knowledge_needs": existing + needs,
            "pending_research_tasks": tasks,
            "research_stalled_rounds": 0,
            "targeted_research_attempts": state.get("targeted_research_attempts", 0) + 1,
            "targeted_research_fingerprints": [
                (state.get("topology_validation_fingerprints") or [""])[-1]
            ],
        }

    def route_after_topology_validation(
        self, state: OverallState
    ) -> Literal["repair_topology_requirements", "targeted_research", "plan_components", "build_netlist", "finalize_topology"]:
        validation = state["topology_validation"]
        if validation.get("verdict") == "valid":
            return "finalize_topology"
        rounds_left = state.get("topology_round", 0) < state.get("max_topology_rounds", self.settings.max_topology_rounds)
        if not rounds_left:
            return "finalize_topology"
        if validation.get("repair_scope") == "requirements":
            if state.get("topology_requirements_repair_count", 0) < 1:
                return "repair_topology_requirements"
            return "finalize_topology"
        current_fingerprint = (state.get("topology_validation_fingerprints") or [""])[-1]
        if state.get("topology_no_progress"):
            return "finalize_topology"
        if validation.get("repair_scope") == "research":
            if current_fingerprint in set(state.get("targeted_research_fingerprints") or []):
                return "finalize_topology"
            research_budget = state.get("searches_used", 0) < state.get(
                "max_searches", self.settings.max_searches
            )
            research_rounds = state.get("research_round", 0) < state.get(
                "max_research_rounds", self.settings.max_research_rounds
            )
            if research_budget and research_rounds:
                return "targeted_research"
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
            selected.append(
                FinalComponent(
                    id=component.id,
                    catalog_key=component.catalog_key,
                    name=str(entry.get("name") or component.catalog_key),
                    comp_type=component.comp_type,
                    role=component.role,
                    function_id=component.function_id,
                    ports=list(entry.get("ports", [])),
                    configuration=component.configuration,
                    selection_basis=component.selection_basis,
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
                "Validated scope: generic hydraulic component classes, functional valve behavior, and direct "
                "port-to-port connectivity only. Pump/cylinder/reservoir sizing, pressure and flow ratings, line "
                "selection, filtration, cooling, prime mover selection, simulation and fabrication approval are "
                "explicitly deferred to the later sizing and engineering phases."
            ),
            design_narrative=topology.design_narrative,
            selected_components=selected,
            connections=topology.connections,
            motion_control_decisions=topology.motion_control_decisions,
            synchronization_decisions=topology.synchronization_decisions,
            phase_configurations=topology.phase_configurations,
            candidate_evaluations=state.get("candidate_evaluations", []),
            component_planner_recovery=state.get("component_planner_recovery", []),
            repair_history=state.get("repair_history", []),
            external_interfaces=topology.external_interfaces,
            port_terminations=topology.port_terminations,
            function_implementations=topology.function_implementations,
            design_decisions=topology.design_decisions,
            catalog_gaps=topology.catalog_gaps,
            evidence_gaps=topology.evidence_gaps,
            assumptions=topology.assumptions,
            open_issues=topology.open_issues + unresolved_descriptions,
            research_audit=summarize_research_audit(state),
            research_coverage=coverage,
            validation=validation,
            repair_stop_reason=state.get("repair_stop_reason") or None,
            validation_fingerprints=state.get("topology_validation_fingerprints", []),
        )
        return {"final_output": output.model_dump(mode="json")}

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _sizing_context(self, state: OverallState):
        """Contract and load tolerance for the problem being sized."""
        requirements = state["requirements"]
        contract = compile_contract(requirements, str(state.get("problem_id") or ""))
        return contract, sizing_load_tolerance(requirements)

    def plan_sizing(self, state: OverallState) -> dict[str, Any]:
        """Ask the planner for two or three strategies, then score them.

        Falls back to the deterministic planner when no sizing-capable model is
        configured. That is not only graceful degradation: the deterministic arm
        is the control the LLM arm has to beat, so having both reachable through
        the same graph is what makes the comparison a controlled one.
        """
        contract, tolerance = self._sizing_context(state)
        topology = state["topology"]
        requirements = state["requirements"]
        model = getattr(self.agents, "sizing_model", None)
        if model is None:
            return self._plan_sizing_deterministically(state, contract, tolerance)
        planner = build_sizing_planner(
            model, topology, requirements, contract, tolerance)
        payload = (
            "PROBLEM:\n" + state["user_query"]
            + "\n\nVALIDATED TOPOLOGY:\n" + _json(topology)
            + "\n\nACCEPTANCE CONTRACT:\n"
            + _json([
                {
                    "id": item.id, "kind": item.kind, "phase_id": item.phase_id,
                    "function_id": item.function_id, "required": item.describe_band(),
                    "rationale": item.rationale,
                }
                for item in contract.criteria
            ])
            + "\n\nTOLERANCE ASSUMPTIONS:\n" + _json(contract.assumptions)
            + "\n\nSize this circuit. Use the tools for every number, and evaluate each "
            "candidate with evaluate_sizing_policy before returning it."
        )
        result: SizingPlanSet = planner.invoke(
            {"messages": [HumanMessage(payload)]}
        )["structured_response"]
        candidate, sizing, throttles, certificate, scores = evaluate_and_select_sizing(
            result, topology, requirements, contract, tolerance)
        return {
            "sizing_contract": {
                "criteria": [item.id for item in contract.criteria],
                "assumptions": contract.assumptions,
            },
            "sizing_candidates": [item.model_dump(mode="json") for item in result.candidates],
            "sizing_scores": [item.model_dump(mode="json") for item in scores],
            "sizing_plan": candidate.model_dump(mode="json"),
            "sizing": sizing,
            "sizing_throttles": throttles,
            "sizing_certificate": certificate.to_dict() if certificate else {},
            "sizing_mode": "llm",
            "sizing_round": state.get("sizing_round", 0) + 1,
        }

    def _plan_sizing_deterministically(self, state, contract, tolerance) -> dict[str, Any]:
        result = size_problem(
            str(state.get("problem_id") or ""), state["topology"], state["requirements"])
        certificate = result.certificate
        return {
            "sizing_contract": {
                "criteria": [item.id for item in contract.criteria],
                "assumptions": contract.assumptions,
            },
            "sizing_candidates": [],
            "sizing_scores": [],
            "sizing_plan": {"candidate_id": "deterministic", "approach": "deterministic planner"},
            "sizing": result.sizing,
            "sizing_throttles": result.throttle_k,
            "sizing_certificate": certificate.to_dict() if certificate else {},
            "sizing_mode": "deterministic",
            "sizing_round": state.get("sizing_round", 0) + 1,
            "sizing_repairs": list(result.repairs),
        }

    def repair_sizing(self, state: OverallState) -> dict[str, Any]:
        """Revise the policy against the certificate that refuted it."""
        contract, tolerance = self._sizing_context(state)
        topology = state["topology"]
        requirements = state["requirements"]
        certificate = state.get("sizing_certificate") or {}
        if getattr(self.agents, "sizing_model", None) is None:
            # The deterministic planner already repaired in closed form; there is
            # no second opinion to ask for.
            return {"sizing_round": state.get("sizing_round", 0) + 1}
        repairer = build_sizing_repairer(
            self.agents.sizing_model, topology, requirements, contract, tolerance)
        unresolved = [
            item for item in certificate.get("criteria", [])
            if item.get("verdict") != "PROVED"
        ]
        payload = (
            "PROBLEM:\n" + state["user_query"]
            + "\n\nCURRENT SIZING POLICY:\n" + _json(state.get("sizing_plan"))
            + "\n\nWHAT DID NOT HOLD:\n" + _json(unresolved)
            + "\n\nFINDINGS:\n" + _json(certificate.get("findings", []))
            + "\n\nOPERATING POINTS:\n" + _json(certificate.get("operating_points", {}))
            + "\n\nRevise the policy and confirm it with evaluate_sizing_policy."
        )
        result: SizingRepairPlan = repairer.invoke(
            {"messages": [HumanMessage(payload)]}
        )["structured_response"]
        score, sizing, throttles, revised = score_candidate(
            result.candidate, topology, requirements, contract, tolerance)
        return {
            "sizing_plan": result.candidate.model_dump(mode="json"),
            "sizing_scores": [score.model_dump(mode="json")],
            "sizing": sizing,
            "sizing_throttles": throttles,
            "sizing_certificate": revised.to_dict() if revised else {},
            "sizing_round": state.get("sizing_round", 0) + 1,
            "sizing_repairs": [result.rationale],
        }

    @staticmethod
    def route_after_topology_to_sizing(state: OverallState) -> Literal["plan_sizing", "end"]:
        status = (state.get("final_output") or {}).get("status")
        return "plan_sizing" if status in {"validated", "validated_with_warnings"} else "end"

    def route_after_sizing(
        self, state: OverallState
    ) -> Literal["repair_sizing", "finalize_sizing"]:
        certificate = state.get("sizing_certificate") or {}
        if certificate.get("verdict") == "PROVED":
            return "finalize_sizing"
        rounds = state.get("sizing_round", 0)
        if rounds >= state.get("max_sizing_rounds", self.settings.max_sizing_rounds):
            return "finalize_sizing"
        return "repair_sizing"

    def finalize_sizing(self, state: OverallState) -> dict[str, Any]:
        certificate = state.get("sizing_certificate") or {}
        verdict = certificate.get("verdict", "UNKNOWN")
        stop = ""
        if verdict != "PROVED":
            stop = (
                f"sizing stopped after {state.get('sizing_round', 0)} round(s) with verdict "
                f"{verdict}; unresolved criteria are listed in the certificate"
            )
        return {
            "sizing_stop_reason": stop,
            "final_sized_output": {
                "problem_id": state.get("problem_id"),
                "verdict": verdict,
                "mode": state.get("sizing_mode", "deterministic"),
                "policy": state.get("sizing_plan"),
                "sizing": state.get("sizing"),
                "throttle_settings": state.get("sizing_throttles"),
                "certificate": certificate,
                "candidate_scores": state.get("sizing_scores", []),
                "repairs": state.get("sizing_repairs", []),
                "rounds": state.get("sizing_round", 0),
                "stop_reason": stop or None,
            },
        }


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
    graph.add_node("requirements_failure", workflow.requirements_failure)
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
    graph.add_node("repair_topology_requirements", workflow.repair_topology_requirements)
    graph.add_node("targeted_research", workflow.plan_targeted_research)
    graph.add_node("finalize_topology", workflow.finalize_topology)
    if settings.enable_sizing:
        graph.add_node("plan_sizing", workflow.plan_sizing)
        graph.add_node("repair_sizing", workflow.repair_sizing)
        graph.add_node("finalize_sizing", workflow.finalize_sizing)

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
    graph.add_conditional_edges(
        "finalize_requirements",
        workflow.route_after_finalize_requirements,
        {
            "requirements_failure": "requirements_failure",
            "plan_research": "plan_research",
        },
    )
    graph.add_edge("requirements_failure", END)
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
            "repair_topology_requirements": "repair_topology_requirements",
            "targeted_research": "targeted_research",
            "plan_components": "plan_components",
            "build_netlist": "build_netlist",
            "finalize_topology": "finalize_topology",
        },
    )
    graph.add_conditional_edges(
        "repair_topology_requirements",
        workflow.route_after_topology_requirements_repair,
        {
            "curate_design_brief": "curate_design_brief",
            "finalize_topology": "finalize_topology",
        },
    )
    graph.add_edge("targeted_research", "research_dispatch")
    if settings.enable_sizing:
        # Sizing runs only on a topology that validated. An unresolved topology
        # has no settled valve states, and without those every phase model is a
        # guess - so there is nothing here worth sizing.
        graph.add_conditional_edges(
            "finalize_topology",
            workflow.route_after_topology_to_sizing,
            {"plan_sizing": "plan_sizing", "end": END},
        )
        graph.add_conditional_edges(
            "plan_sizing",
            workflow.route_after_sizing,
            {"repair_sizing": "repair_sizing", "finalize_sizing": "finalize_sizing"},
        )
        graph.add_conditional_edges(
            "repair_sizing",
            workflow.route_after_sizing,
            {"repair_sizing": "repair_sizing", "finalize_sizing": "finalize_sizing"},
        )
        graph.add_edge("finalize_sizing", END)
    else:
        graph.add_edge("finalize_topology", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
