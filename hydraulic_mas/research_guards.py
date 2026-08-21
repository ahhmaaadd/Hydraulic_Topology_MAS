from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from .schemas import (
    Citation,
    CoverageItem,
    KnowledgeNeed,
    ResearchCoverage,
    ResearchPlan,
    ResearchTask,
    SearchResult,
    TaskFinding,
)
from .search import normalize_query, normalize_url, queries_are_similar


def _need(
    need_id: str,
    decision: str,
    why: str,
    function_ids: list[str] | None = None,
    *,
    critical: bool = True,
    critical_reason: str | None = None,
) -> KnowledgeNeed:
    return KnowledgeNeed(
        id=need_id,
        decision=decision,
        why_needed=why,
        related_function_ids=function_ids or [],
        critical=critical,
        critical_reason=critical_reason or ("Required topology behavior" if critical else None),
    )


def derive_research_need_hints(requirements: dict[str, Any]) -> list[KnowledgeNeed]:
    """Build a compact, deterministic decision checklist from explicit requirements."""
    hints: list[KnowledgeNeed] = [
        _need(
            "system_power_and_relief",
            "Complete suction, pump, pressure-relief, return, and reservoir topology",
            "Every open hydraulic circuit needs a safe power and return path.",
            critical_reason="Overpressure protection and a complete flow path are mandatory.",
        )
    ]
    functions = requirements.get("functions", [])
    for function in functions:
        function_id = function.get("id", "unknown")
        hints.append(
            _need(
                f"function_{function_id}_directional_control",
                f"Actuator and directional-control pattern for {function_id}",
                "The topology must command all phases of the physical actuator and define safe neutral behavior.",
                [function_id],
            )
        )
        phases = function.get("motion_phases") or []
        phase_speed_control = any(
            str(phase.get("speed_realization") or "sizing_only")
            not in {"sizing_only"}
            for phase in phases
        )
        same_direction_multiphase = any(
            sum(phase.get("motion") == direction for phase in phases) > 1
            for direction in {phase.get("motion") for phase in phases}
        )
        hydraulic_phase_change = any(
            step.get("hydraulically_enforced") and step.get("phase_id") in {phase.get("id") for phase in phases}
            for step in (requirements.get("operational_logic") or {}).get("sequence", [])
        )
        if phase_speed_control or same_direction_multiphase or hydraulic_phase_change:
            typed_decisions = "; ".join(
                f"{phase.get('id')}: {phase.get('motion')} {phase.get('speed_realization')} "
                f"{phase.get('metering_side')} "
                f"at {phase.get('metered_chamber')} {phase.get('metered_flow')}"
                for phase in phases
            )
            hints.append(
                _need(
                    f"function_{function_id}_motion_profile",
                    f"Multi-speed regulation and automatic phase-transition pattern for {function_id}; "
                    f"typed decisions: {typed_decisions}",
                    "Ordered phases, speed changes, and automatic transitions change valve choice and placement.",
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
                    critical_reason="Explicit or physically entailed load-holding safety behavior.",
                )
            )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    interlocks = (requirements.get("operational_logic") or {}).get("interlocks") or []
    if len(functions) > 1 and (len(sequence) > 1 or interlocks):
        function_ids = sorted({fid for step in sequence for fid in step.get("function_ids", [])})
        hints.append(
            _need(
                "system_sequence_and_interlocks",
                "Multi-actuator hydraulic sequence and interlock topology",
                "The circuit must enforce preconditions and forbidden states between physical actuators.",
                function_ids,
            )
        )

    driver_labels = {
        "synchronization": "Requirement-matched synchronization topology: rigid parallel, hydraulic series, or divider/combiner parallel",
        "pressure_compensation_load_independence": "Pressure-compensated load-independent speed-control topology",
        "two_speed_force_switching": "Automatic fast/slow or low/high-pressure switching topology",
        "counterbalance_overrunning": "Controlled overrunning-load topology",
        "load_holding": "Leak-resistant load-holding topology",
        "flow_priority_sharing": "Multi-function priority and flow-sharing topology",
        "regeneration_fast_approach": "Regenerative fast-approach topology",
        "energy_storage_peak_flow": "Accumulator-supported peak-flow topology",
        "pressure_limiting_stall": "Stall pressure-limiting topology",
        "branch_pressure_reduction": "Lower-pressure actuator branch protection using pressure reduction",
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

    for safety in requirements.get("safety_requirements", []):
        standard = safety.get("standard")
        if safety.get("category") != "standard_compliance" or not standard or safety.get("source") != "explicit":
            continue
        safe_id = re.sub(r"[^a-z0-9]+", "_", str(standard).casefold()).strip("_")
        hints.append(
            _need(
                f"standard_{safe_id}",
                f"Apply explicitly required {standard} provisions to topology decisions",
                str(safety.get("description") or f"The user explicitly requires {standard}."),
                list(safety.get("related_function_ids") or []),
                critical_reason="Explicit named-standard compliance requirement.",
            )
        )
    return hints


def fallback_query(need: KnowledgeNeed, round_number: int) -> str:
    suffixes = [
        "manufacturer application note circuit schematic",
        "university hydraulic circuit port connections",
        "engineering textbook circuit diagram",
        "technical manual circuit operation",
    ]
    suffix = suffixes[min(max(round_number - 1, 0), len(suffixes) - 1)]
    return f"hydraulic {need.decision} {suffix}"


def _query_is_duplicate(query: str, existing: list[str]) -> bool:
    return any(queries_are_similar(query, item) for item in existing)


def enforce_minimum_plan(
    plan: ResearchPlan,
    requirements: dict[str, Any],
    *,
    max_initial_tasks: int,
) -> ResearchPlan:
    """Keep the model focused on deterministic needs and one novel task per decision."""
    hints = derive_research_need_hints(requirements)
    valid_ids = {need.id for need in hints}
    tasks: list[ResearchTask] = []
    queries: list[str] = []
    covered: set[str] = set()

    for proposed in plan.tasks:
        need_ids = [need_id for need_id in proposed.need_ids if need_id in valid_ids]
        if not need_ids or all(need_id in covered for need_id in need_ids):
            continue
        if not proposed.query.strip() or _query_is_duplicate(proposed.query, queries):
            continue
        task = proposed.model_copy(
            update={
                "need_ids": need_ids,
                "round_number": 1,
                "action": "search",
                "target_urls": [],
            }
        )
        tasks.append(task)
        queries.append(task.query)
        covered.update(need_ids)
        if len(tasks) >= max_initial_tasks:
            break

    for hint in hints:
        if hint.id in covered or len(tasks) >= max_initial_tasks:
            continue
        query = fallback_query(hint, 1)
        if _query_is_duplicate(query, queries):
            query += f" {hint.id}"
        tasks.append(
            ResearchTask(
                id=f"initial_{hint.id}",
                category="circuit_pattern",
                query=query,
                objective=hint.decision,
                need_ids=[hint.id],
                related_function_ids=hint.related_function_ids,
                source_preference="manufacturer manual, university notes, or engineering textbook",
            )
        )
        queries.append(query)
        covered.add(hint.id)
    return ResearchPlan(rationale=plan.rationale, knowledge_needs=hints, tasks=tasks)


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def calibrate_finding(finding: TaskFinding, results: list[SearchResult | dict[str, Any]]) -> TaskFinding:
    """Verify excerpts/URLs and derive confidence from retrieved source quality."""
    parsed = [item if isinstance(item, SearchResult) else SearchResult.model_validate(item) for item in results]
    by_url = {normalize_url(item.url): item for item in parsed}
    verified_claims = []
    seen_claim_ids: set[str] = set()
    for claim in finding.evidence_claims:
        source = by_url.get(normalize_url(claim.source_url))
        excerpt = _normalized_text(claim.excerpt)
        if not source or len(excerpt) < 12 or excerpt not in _normalized_text(source.content):
            continue
        claim.id = claim.id if claim.id not in seen_claim_ids else f"{claim.id}_{len(seen_claim_ids) + 1}"
        seen_claim_ids.add(claim.id)
        claim.source_title = source.title
        claim.source_kind = source.source_kind
        claim.verified = True
        verified_claims.append(claim)
    finding.evidence_claims = verified_claims

    citations: list[Citation] = []
    cited_urls: set[str] = set()
    for citation in finding.citations:
        source = by_url.get(normalize_url(citation.url))
        if not source or normalize_url(source.url) in cited_urls:
            continue
        citations.append(Citation(title=source.title, url=source.url, source_kind=source.source_kind))
        cited_urls.add(normalize_url(source.url))
    for claim in verified_claims:
        normalized = normalize_url(claim.source_url)
        if normalized not in cited_urls:
            citations.append(
                Citation(title=claim.source_title, url=claim.source_url, source_kind=claim.source_kind)
            )
            cited_urls.add(normalized)
    finding.citations = citations

    credible_claims = []
    for claim in verified_claims:
        source = by_url[normalize_url(claim.source_url)]
        if source.is_full_content and source.quality_score >= 0.45 and source.source_kind != "other":
            credible_claims.append(claim)
    credible_domains = {urlsplit(claim.source_url).hostname for claim in credible_claims}
    primary = any(claim.source_kind in {"manufacturer", "standard", "textbook", "paper"} for claim in credible_claims)
    if credible_claims and (primary or len(credible_domains) >= 2):
        finding.confidence = "high"
    elif credible_claims:
        finding.confidence = "medium"
    else:
        finding.confidence = "low"
    finding.source_quality_notes = (
        f"{len(verified_claims)} excerpt(s) verified; {len(credible_claims)} from fully extracted credible sources."
    )
    return finding


def _evidence_by_need(findings: list[TaskFinding]) -> dict[str, list[TaskFinding]]:
    output: dict[str, list[TaskFinding]] = defaultdict(list)
    for finding in findings:
        for need_id in finding.need_ids:
            output[need_id].append(finding)
    return output


def _finding_supports_need(need: KnowledgeNeed, finding: TaskFinding) -> bool:
    if finding.confidence not in {"high", "medium"} or not finding.citations:
        return False
    claims = [claim for claim in finding.evidence_claims if claim.verified]
    if not claims:
        return False
    text = f"{need.id} {need.decision}".casefold()
    if "standard" in text or "safety" in text:
        return any(claim.claim_type in {"standard", "safety"} and claim.source_kind != "other" for claim in claims)
    claim_types = {claim.claim_type for claim in claims}
    if any(word in text for word in ("topology", "control", "sequence", "transition", "holding", "synchron", "circuit")):
        # One generic connection sentence is not enough to establish a circuit
        # pattern. Require placement plus component behavior/safety evidence.
        return "connection" in claim_types and bool(
            claim_types.intersection({"component", "operating_principle", "safety"})
        )
    return bool(claim_types.intersection({"component", "operating_principle", "connection"}))


def enforce_coverage_gate(
    report: ResearchCoverage,
    *,
    needs: list[KnowledgeNeed],
    findings: list[TaskFinding],
    query_history: list[str],
    round_number: int,
    remaining_searches: int,
    rounds_left: bool,
    stalled_rounds: int = 0,
) -> ResearchCoverage:
    """Require verified design principles while allowing synthesis across sources."""
    evidence = _evidence_by_need(findings)
    item_by_id = {item.need_id: item for item in report.items}
    forced_items: list[CoverageItem] = []
    unresolved_critical: list[KnowledgeNeed] = []

    for need in needs:
        supporting = [finding for finding in evidence.get(need.id, []) if _finding_supports_need(need, finding)]
        verified = [
            finding for finding in evidence.get(need.id, []) if any(claim.verified for claim in finding.evidence_claims)
        ]
        proposed = item_by_id.get(need.id)
        if supporting:
            status = "covered"
            rationale = proposed.rationale if proposed else "Supported by verified claim-level evidence."
        elif verified:
            status = "partial"
            rationale = proposed.rationale if proposed else "Verified evidence exists but lacks the required connection or safety principle."
        else:
            status = "missing"
            rationale = "No verified claim-level evidence supports this topology decision."
        task_ids = sorted({finding.task_id for finding in evidence.get(need.id, [])})
        forced_items.append(CoverageItem(need_id=need.id, status=status, rationale=rationale, evidence_task_ids=task_ids))
        if need.critical and status != "covered":
            unresolved_critical.append(need)

    if not unresolved_critical:
        return ResearchCoverage(
            status="sufficient",
            can_proceed=True,
            summary=report.summary,
            items=forced_items,
            missing_or_weak_information=report.missing_or_weak_information,
            follow_up_tasks=[],
        )

    known_queries = list(query_history)
    valid_need_ids = {need.id for need in unresolved_critical}
    followups: list[ResearchTask] = []
    for task in report.follow_up_tasks:
        need_ids = [need_id for need_id in task.need_ids if need_id in valid_need_ids]
        if not need_ids:
            continue
        if task.action == "extract" and not task.target_urls:
            continue
        if task.action == "search" and (
            not task.query.strip() or _query_is_duplicate(task.query, known_queries)
        ):
            continue
        followups.append(task.model_copy(update={"need_ids": need_ids, "round_number": round_number + 1}))
        if task.query:
            known_queries.append(task.query)

    covered_by_followup = {need_id for task in followups for need_id in task.need_ids}
    for need in unresolved_critical:
        if need.id in covered_by_followup:
            continue
        query = fallback_query(need, round_number + 1)
        if _query_is_duplicate(query, known_queries):
            query += f" alternative source {round_number + 1}"
        followups.append(
            ResearchTask(
                id=f"followup_r{round_number + 1}_{need.id}",
                category="circuit_pattern",
                query=query,
                objective=f"Find direct evidence for the missing design principle: {need.decision}",
                need_ids=[need.id],
                related_function_ids=need.related_function_ids,
                source_preference="a new manufacturer, university, textbook, paper, or standards source",
                round_number=round_number + 1,
            )
        )
        known_queries.append(query)

    followups = followups[: max(remaining_searches, 0)]
    exhausted = remaining_searches <= 0 or not rounds_left or stalled_rounds >= 2 or not followups
    reason = "search/round budget was exhausted" if stalled_rounds < 2 else "two rounds produced no new sources"
    return ResearchCoverage(
        status="budget_exhausted" if exhausted else "needs_more",
        can_proceed=False,
        summary=(f"Research stopped because {reason}." if exhausted else report.summary),
        items=forced_items,
        missing_or_weak_information=report.missing_or_weak_information
        + [f"Unresolved critical need: {need.id} — {need.decision}" for need in unresolved_critical],
        follow_up_tasks=[] if exhausted else followups,
    )
