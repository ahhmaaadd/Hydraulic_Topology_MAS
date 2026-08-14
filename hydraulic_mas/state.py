from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class OverallState(TypedDict, total=False):
    problem_id: str
    user_query: str
    interactive: bool

    # Requirements
    clarification_answers: Annotated[list[str], operator.add]
    requirements: dict[str, Any]
    critique: dict[str, Any]
    requirements_round: int
    max_requirements_rounds: int
    requirements_unresolved: bool
    requirements_repair_attempted: bool

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

    # Topology
    design_brief: dict[str, Any]
    component_plan: dict[str, Any]
    catalog_tool_trace: list[dict[str, Any]]
    topology: dict[str, Any]
    deterministic_validation: dict[str, Any]
    topology_validation: dict[str, Any]
    topology_round: int
    max_topology_rounds: int

    # Final result / controlled failure
    final_output: dict[str, Any]
    failure: dict[str, Any]

