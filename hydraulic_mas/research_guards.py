from __future__ import annotations

from collections import defaultdict
from typing import Any

from .schemas import (
    CoverageItem,
    KnowledgeNeed,
    ResearchCoverage,
    ResearchPlan,
    ResearchTask,
    TaskFinding,
)
from .search import normalize_query


def _need(
    need_id: str,
    decision: str,
    why: str,
    function_ids: list[str] | None = None,
    *,
    critical: bool = True,
) -> KnowledgeNeed:
    return KnowledgeNeed(
        id=need_id,
        decision=decision,
        why_needed=why,
        related_function_ids=function_ids or [],
        critical=critical,
    )


def derive_research_need_hints(requirements: dict[str, Any]) -> list[KnowledgeNeed]:
    """Create a deterministic minimum coverage checklist from the extracted spec."""
    hints: list[KnowledgeNeed] = [
        _need(
            "system_power_and_relief",
            "Complete suction, pump, pressure-relief, return, and reservoir topology",
            "Every open hydraulic circuit needs a safe power and return path.",
        )
    ]
    for function in requirements.get("functions", []):
        function_id = function.get("id", "unknown")
        hints.append(
            _need(
                f"function_{function_id}_directional_control",
                f"Bidirectional actuator and directional-control pattern for {function_id}",
                "The topology must command every stated motion and safe neutral behavior.",
                [function_id],
            )
        )
        phases = function.get("motion_phases") or []
        if len(phases) > 1 or function.get("speeds_adjustable") or function.get("speed_load_independent"):
            hints.append(
                _need(
                    f"function_{function_id}_speed_and_transition",
                    f"Speed regulation and automatic phase-transition circuit for {function_id}",
                    "Multiple speeds, adjustability, or load independence changes valve choice and placement.",
                    [function_id],
                )
            )
        holding = function.get("holding") or {}
        load_type = str(function.get("load_type", ""))
        orientation = str(function.get("orientation", ""))
        if (
            holding.get("must_hold_position")
            or holding.get("hold_on_power_loss")
            or holding.get("retain_on_hose_burst")
            or load_type in {"overrunning", "both"}
            or orientation == "vertical"
        ):
            hints.append(
                _need(
                    f"function_{function_id}_holding",
                    f"Load-holding and controlled-motion pattern for {function_id}",
                    "Unsafe drift or uncontrolled motion must be prevented at the actuator.",
                    [function_id],
                )
            )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    interlocks = (requirements.get("operational_logic") or {}).get("interlocks") or []
    if len(sequence) > 1 or interlocks:
        function_ids = sorted({fid for step in sequence for fid in step.get("function_ids", [])})
        hints.append(
            _need(
                "system_sequence_and_interlocks",
                "Hydraulic sequence and reverse-order interlock topology",
                "The circuit must enforce preconditions and forbidden states without relying on prose.",
                function_ids,
            )
        )

    driver_labels = {
        "synchronization": "Hydraulic flow division/combination or other synchronization topology",
        "pressure_compensation_load_independence": "Pressure-compensated load-independent speed-control topology",
        "two_speed_force_switching": "Automatic fast/slow or low/high-pressure switching topology",
        "counterbalance_overrunning": "Controlled overrunning-load topology",
        "load_holding": "Leak-resistant load-holding topology",
        "flow_priority_sharing": "Multi-function priority and flow-sharing topology",
        "regeneration_fast_approach": "Regenerative fast-approach topology",
        "energy_storage_peak_flow": "Accumulator-supported peak-flow topology",
        "pressure_limiting_stall": "Stall pressure-limiting topology",
    }
    for driver in requirements.get("derived_design_drivers", []):
        capability = driver.get("capability")
        if capability not in driver_labels:
            continue
        need_id = f"driver_{capability}"
        if any(item.id == need_id for item in hints):
            continue
        hints.append(
            _need(
                need_id,
                driver_labels[capability],
                str(driver.get("evidence") or "The extracted requirements imply this capability."),
                list(driver.get("related_function_ids") or []),
            )
        )
    return hints


def fallback_query(need: KnowledgeNeed, round_number: int) -> str:
    suffixes = [
        "manufacturer technical manual circuit schematic",
        "hydraulic application note port connections",
        "engineering textbook circuit diagram safety",
        "technical design guide circuit operation",
    ]
    suffix = suffixes[min(max(round_number - 1, 0), len(suffixes) - 1)]
    return f"hydraulic {need.decision} {suffix}"


def enforce_minimum_plan(
    plan: ResearchPlan,
    requirements: dict[str, Any],
    *,
    max_initial_tasks: int,
) -> ResearchPlan:
    hints = derive_research_need_hints(requirements)
    needs_by_id = {need.id: need for need in plan.knowledge_needs}
    for hint in hints:
        needs_by_id.setdefault(hint.id, hint)

    tasks = list(plan.tasks)
    task_need_ids = {need_id for task in tasks for need_id in task.need_ids}
    existing_queries = {normalize_query(task.query) for task in tasks}
    for hint in hints:
        if hint.id in task_need_ids:
            continue
        query = fallback_query(hint, 1)
        if normalize_query(query) in existing_queries:
            continue
        tasks.append(
            ResearchTask(
                id=f"initial_{hint.id}",
                category="circuit_pattern",
                query=query,
                objective=hint.decision,
                need_ids=[hint.id],
                related_function_ids=hint.related_function_ids,
            )
        )
        existing_queries.add(normalize_query(query))

    # Favor tasks connected to critical needs when a user sets a small budget.
    critical_ids = {need.id for need in needs_by_id.values() if need.critical}
    tasks.sort(key=lambda task: (not bool(critical_ids.intersection(task.need_ids)), task.id))
    return ResearchPlan(
        rationale=plan.rationale,
        knowledge_needs=list(needs_by_id.values()),
        tasks=tasks[:max_initial_tasks],
    )


def _evidence_by_need(findings: list[TaskFinding]) -> dict[str, list[TaskFinding]]:
    output: dict[str, list[TaskFinding]] = defaultdict(list)
    for finding in findings:
        for need_id in finding.need_ids:
            output[need_id].append(finding)
    return output


def enforce_coverage_gate(
    report: ResearchCoverage,
    *,
    needs: list[KnowledgeNeed],
    findings: list[TaskFinding],
    query_history: list[str],
    round_number: int,
    remaining_searches: int,
    rounds_left: bool,
) -> ResearchCoverage:
    """Prevent unsupported sufficiency and sanitize follow-up work."""
    evidence = _evidence_by_need(findings)
    item_by_id = {item.need_id: item for item in report.items}
    forced_items: list[CoverageItem] = []
    unresolved_critical: list[KnowledgeNeed] = []

    for need in needs:
        cited = [
            finding
            for finding in evidence.get(need.id, [])
            if finding.citations and finding.confidence in {"high", "medium"}
        ]
        weak = [finding for finding in evidence.get(need.id, []) if finding.citations]
        proposed = item_by_id.get(need.id)
        if cited:
            status = proposed.status if proposed and proposed.status != "missing" else "covered"
            rationale = proposed.rationale if proposed else "Supported by cited medium/high-confidence findings."
        elif weak:
            status = "partial"
            rationale = "Only low-confidence cited evidence is available."
        else:
            status = "missing"
            rationale = "No cited finding supports this required topology decision."
        task_ids = sorted({finding.task_id for finding in evidence.get(need.id, [])})
        forced_items.append(CoverageItem(need_id=need.id, status=status, rationale=rationale, evidence_task_ids=task_ids))
        if need.critical and status != "covered":
            unresolved_critical.append(need)

    if not unresolved_critical and report.can_proceed:
        return ResearchCoverage(
            status="sufficient",
            can_proceed=True,
            summary=report.summary,
            items=forced_items,
            missing_or_weak_information=report.missing_or_weak_information,
            follow_up_tasks=[],
        )

    known_queries = {normalize_query(query) for query in query_history}
    valid_need_ids = {need.id for need in needs}
    followups: list[ResearchTask] = []
    for task in report.follow_up_tasks:
        if not set(task.need_ids).intersection(valid_need_ids):
            continue
        normalized = normalize_query(task.query)
        if not normalized or normalized in known_queries:
            continue
        followups.append(task)
        known_queries.add(normalized)

    covered_by_followup = {need_id for task in followups for need_id in task.need_ids}
    for need in unresolved_critical:
        if need.id in covered_by_followup:
            continue
        query = fallback_query(need, round_number + 1)
        normalized = normalize_query(query)
        if normalized in known_queries:
            query += f" source round {round_number + 1}"
        followups.append(
            ResearchTask(
                id=f"followup_r{round_number + 1}_{need.id}",
                category="circuit_pattern",
                query=query,
                objective=need.decision,
                need_ids=[need.id],
                related_function_ids=need.related_function_ids,
            )
        )
        known_queries.add(normalize_query(query))

    followups = followups[: max(remaining_searches, 0)]
    exhausted = remaining_searches <= 0 or not rounds_left or not followups
    return ResearchCoverage(
        status="budget_exhausted" if exhausted else "needs_more",
        can_proceed=False,
        summary=(
            "Research stopped with unresolved critical knowledge needs because the configured budget was exhausted."
            if exhausted
            else report.summary
        ),
        items=forced_items,
        missing_or_weak_information=report.missing_or_weak_information
        + [f"Unresolved critical need: {need.id} — {need.decision}" for need in unresolved_critical],
        follow_up_tasks=[] if exhausted else followups,
    )

