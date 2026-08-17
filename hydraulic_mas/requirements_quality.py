from __future__ import annotations

import re

from .schemas import RequirementsSpec
from .decision_flow import motion_decision_issues


_PHYSICAL_MOTION_NOUNS = ("slide", "ram", "platen", "carriage", "table", "cylinder", "actuator")


def normalize_requirements(
    requirements: RequirementsSpec | dict,
) -> tuple[RequirementsSpec, list[str]]:
    """Apply only unambiguous structural normalizations before critique.

    A sequence step cannot simultaneously declare a function active and forbid
    that same function. Removing that self-reference is lossless: overlap
    between phases of one function belongs in phase/state logic, not in a list
    of forbidden *function* ids.
    """
    source = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    spec = source.model_copy(deep=True)
    notes: list[str] = []
    for step in spec.operational_logic.sequence:
        active = set(step.function_ids)
        overlap = sorted(active.intersection(step.forbidden_overlap_function_ids))
        if not overlap:
            continue
        step.forbidden_overlap_function_ids = [
            function_id
            for function_id in step.forbidden_overlap_function_ids
            if function_id not in active
        ]
        notes.append(
            f"Sequence step {step.order} removed active function ids {overlap} from "
            "forbidden_overlap_function_ids."
        )
    return spec, notes


def requirements_quality_issues(requirements: RequirementsSpec | dict, user_query: str) -> list[str]:
    """Return structural extraction errors that would multiply downstream research."""
    spec = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    issues: list[str] = []

    actuator_groups: dict[str, list[str]] = {}
    for function in spec.functions:
        if function.physical_actuator_id:
            actuator_groups.setdefault(function.physical_actuator_id, []).append(function.id)
    for actuator_id, function_ids in actuator_groups.items():
        if len(function_ids) > 1:
            issues.append(
                f"Physical actuator {actuator_id!r} is split across functions {function_ids}; "
                "merge them into one function with ordered motion_phases."
            )

    query = user_query.casefold()
    if len(spec.functions) > 1:
        for noun in _PHYSICAL_MOTION_NOUNS:
            if not re.search(rf"\b(?:the|one|same)\s+{noun}\b", query):
                continue
            matching = [
                function.id
                for function in spec.functions
                if noun in f"{function.name} {function.description} {function.notes or ''}".casefold()
            ]
            if len(matching) > 1:
                issues.append(
                    f"The problem describes one {noun}, but phases were split across functions {matching}; "
                    "use one function and preserve the phase order."
                )
                break

    independent = [function.id for function in spec.functions if function.speed_load_independent]
    if len(independent) > 1 and ("load variation" in query or "load-independent" in query):
        issues.append(
            "Load-independent speed was applied to multiple functions. Scope it only to the motion phase "
            "whose speed must remain stable under load variation."
        )
    issues.extend(motion_decision_issues(spec))

    function_ids = {function.id for function in spec.functions}
    phase_owner = {
        phase.id: function.id
        for function in spec.functions
        for phase in function.motion_phases
    }
    seen_orders: set[int] = set()
    for step in spec.operational_logic.sequence:
        if step.order in seen_orders:
            issues.append(f"Operational sequence order {step.order} is duplicated.")
        seen_orders.add(step.order)

        unknown_active = sorted(set(step.function_ids).difference(function_ids))
        if unknown_active:
            issues.append(
                f"Sequence step {step.order} references unknown active function ids {unknown_active}."
            )
        unknown_forbidden = sorted(set(step.forbidden_overlap_function_ids).difference(function_ids))
        if unknown_forbidden:
            issues.append(
                f"Sequence step {step.order} references unknown forbidden function ids {unknown_forbidden}."
            )
        self_conflict = sorted(set(step.function_ids).intersection(step.forbidden_overlap_function_ids))
        if self_conflict:
            issues.append(
                f"Sequence step {step.order} marks function ids {self_conflict} as both active and forbidden."
            )
        if step.phase_id and step.phase_id not in phase_owner:
            issues.append(f"Sequence step {step.order} references unknown phase {step.phase_id!r}.")
        elif step.phase_id and step.function_ids and phase_owner[step.phase_id] not in step.function_ids:
            issues.append(
                f"Sequence step {step.order} phase {step.phase_id!r} belongs to function "
                f"{phase_owner[step.phase_id]!r}, which is absent from function_ids."
            )
        if step.predecessor_phase_id and step.predecessor_phase_id not in phase_owner:
            issues.append(
                f"Sequence step {step.order} references unknown predecessor phase "
                f"{step.predecessor_phase_id!r}."
            )

    rigid_words = "rigid" in query and any(word in query for word in ("platen", "platform", "crosshead"))
    if rigid_words:
        synchronized = [
            function.id
            for function in spec.functions
            if function.synchronization.strategy.value == "rigid_platen_parallel"
            and function.synchronization.actuator_count >= 2
        ]
        if not synchronized:
            issues.append(
                "The problem explicitly describes a rigid shared platen/platform, but no function carries the "
                "rigid_platen_parallel synchronization decision with at least two actuators."
            )
    return issues
