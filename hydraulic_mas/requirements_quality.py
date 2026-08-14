from __future__ import annotations

import re

from .schemas import RequirementsSpec


_PHYSICAL_MOTION_NOUNS = ("slide", "ram", "platen", "carriage", "table", "cylinder", "actuator")


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
    return issues
