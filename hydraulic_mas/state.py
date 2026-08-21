from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class OverallState(TypedDict, total=False):
    problem_id: str
    user_query: str
    interactive: bool

    # Requirements
    clarification_answers: Annotated[list[str], operator.add]
    clarification_records: Annotated[list[dict[str, Any]], operator.add]
    resolved_clarification_keys: Annotated[list[str], operator.add]
    requirements: dict[str, Any]
    critique: dict[str, Any]
    requirements_round: int
    max_requirements_rounds: int
    requirements_unresolved: bool
    requirements_repair_attempted: bool
    requirements_structural_issues: list[str]
    requirements_advisories: list[str]
    requirements_normalizations: list[str]
    requirements_gate: dict[str, Any]

    # Adaptive research
    research_plan: dict[str, Any]
    knowledge_needs: list[dict[str, Any]]
    pending_research_tasks: list[dict[str, Any]]
    task: dict[str, Any]
    research_findings: Annotated[list[dict[str, Any]], operator.add]
    research_search_log: Annotated[list[dict[str, Any]], operator.add]
    research_query_history: Annotated[list[str], operator.add]
    searches_used: Annotated[int, operator.add]
    research_round: int
    max_research_rounds: int
    max_searches: int
    research_coverage: dict[str, Any]
    research_synthesis: dict[str, Any]
    research_blocked: bool
    research_stalled_rounds: int
    targeted_research_attempts: int

    # Topology
    design_brief: dict[str, Any]
    component_candidates: list[dict[str, Any]]
    candidate_evaluations: list[dict[str, Any]]
    component_plan: dict[str, Any]
    catalog_tool_trace: list[dict[str, Any]]
    component_planner_recovery: list[dict[str, Any]]
    repair_history: Annotated[list[dict[str, Any]], operator.add]
    topology: dict[str, Any]
    deterministic_validation: dict[str, Any]
    topology_validation: dict[str, Any]
    topology_round: int
    max_topology_rounds: int
    topology_validation_fingerprints: Annotated[list[str], operator.add]
    topology_error_signatures: Annotated[list[str], operator.add]
    topology_error_subjects: Annotated[list[list[str]], operator.add]
    topology_repair_escalated: bool
    topology_no_progress: bool
    repair_stop_reason: str
    targeted_research_fingerprints: Annotated[list[str], operator.add]
    topology_requirements_repair_count: int
    topology_requirements_repair_failed: bool

    # Final result / controlled failure
    final_output: dict[str, Any]
    failure: dict[str, Any]
