"""Deterministic scoring and structural repair for component-plan candidates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .catalog import CATALOG
from .decision_flow import inject_plan_decisions
from .schemas import CandidateEvaluation, ComponentPlan, ComponentPlanSet


def _intent_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("from_hint", "")),
        str(value.get("to_hint", "")),
        str(value.get("line", "unspecified")),
        str(value.get("description", "")),
    )


def apply_repair_actions(plan: ComponentPlan) -> ComponentPlan:
    """Apply declared add/delete/replace actions to the returned full plan.

    The LLM still returns a complete candidate, but actions are executable rather
    than commentary.  In particular, a delete action removes the target even if
    the model accidentally left it in ``components``.
    """
    data = plan.model_dump(mode="json")
    components = {item["id"]: item for item in data.get("components", [])}
    intents = list(data.get("connection_intent", []))
    for action in data.get("repair_actions", []):
        kind = action["action"]
        target_id = action.get("target_component_id")
        component = action.get("component")
        if kind == "delete_component" and target_id:
            components.pop(target_id, None)
            intents = [
                item
                for item in intents
                if target_id not in {item.get("from_hint"), item.get("to_hint")}
            ]
        elif kind == "add_component" and component:
            components[component["id"]] = component
        elif kind == "replace_component" and target_id and component:
            components.pop(target_id, None)
            components[component["id"]] = component
            if component["id"] != target_id:
                for intent in intents:
                    if intent.get("from_hint") == target_id:
                        intent["from_hint"] = component["id"]
                    if intent.get("to_hint") == target_id:
                        intent["to_hint"] = component["id"]
        elif kind == "add_connection" and action.get("replacement_connection"):
            replacement = action["replacement_connection"]
            if _intent_key(replacement) not in {_intent_key(item) for item in intents}:
                intents.append(replacement)
        elif kind == "delete_connection" and action.get("target_connection"):
            target = _intent_key(action["target_connection"])
            intents = [item for item in intents if _intent_key(item) != target]
        elif (
            kind == "replace_connection"
            and action.get("target_connection")
            and action.get("replacement_connection")
        ):
            target = _intent_key(action["target_connection"])
            replacement = action["replacement_connection"]
            intents = [replacement if _intent_key(item) == target else item for item in intents]
    data["components"] = list(components.values())
    data["connection_intent"] = intents
    return ComponentPlan.model_validate(data)


def _candidate_evaluation(plan: ComponentPlan, requirements: dict[str, Any]) -> CandidateEvaluation:
    errors: list[str] = []
    warnings: list[str] = []
    ids = [item.id for item in plan.components]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        errors.append(f"duplicate component ids: {duplicate_ids}")

    types: list[str] = []
    for component in plan.components:
        entry = CATALOG.get(component.catalog_key)
        if entry is None:
            errors.append(f"{component.id}: unknown catalog key {component.catalog_key}")
            continue
        types.append(str(entry["type"]))
        if component.comp_type != entry["type"]:
            errors.append(
                f"{component.id}: declared type {component.comp_type} does not match {entry['type']}"
            )

    type_set = set(types)
    for required in ("tank", "pump", "relief_valve"):
        if required not in type_set:
            errors.append(f"missing required system class {required}")
    linear_functions = [
        item for item in requirements.get("functions", []) if item.get("actuator_type") == "linear"
    ]
    if linear_functions and "dcv" not in type_set:
        errors.append("linear functions exist but no DCV is selected")

    cylinders_by_function: dict[str, int] = {}
    for component in plan.components:
        if component.comp_type == "cylinder" and component.function_id:
            cylinders_by_function[component.function_id] = cylinders_by_function.get(component.function_id, 0) + 1
    for function in linear_functions:
        function_id = str(function.get("id"))
        synchronization = function.get("synchronization") or {}
        count = int(synchronization.get("actuator_count", 1) or 1)
        if cylinders_by_function.get(function_id, 0) < count:
            errors.append(
                f"{function_id}: requires {count} cylinder(s), candidate has {cylinders_by_function.get(function_id, 0)}"
            )
        strategy = synchronization.get("strategy", "none")
        if strategy == "flow_divider_parallel" and "flow_divider_combiner" not in type_set:
            errors.append(f"{function_id}: flow_divider_parallel requires a flow-divider/combiner")
        if strategy == "rigid_platen_parallel" and "flow_divider_combiner" in type_set:
            warnings.append(
                f"{function_id}: a divider is unnecessary unless the explicit rigid-platen requirement also asks for hydraulic division"
            )

    drivers = {item.get("capability") for item in requirements.get("derived_design_drivers", [])}
    capability_types = {
        "pressure_compensation_load_independence": {"pressure_comp_flow_control"},
        "load_holding": {"pilot_check_valve", "single_pilot_check_valve", "counterbalance_valve"},
        "counterbalance_overrunning": {"counterbalance_valve"},
        "pressure_limiting_stall": {"relief_valve"},
        "synchronization": {"flow_divider_combiner", "cylinder"},
    }
    for capability, accepted in capability_types.items():
        if capability in drivers and not type_set.intersection(accepted):
            errors.append(f"driver {capability} lacks one of {sorted(accepted)}")

    topology_gaps = [gap for gap in plan.catalog_gaps if gap.scope == "topology"]
    if topology_gaps:
        errors.append(
            "topology catalog gaps: " + ", ".join(sorted({gap.capability for gap in topology_gaps}))
        )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    has_pressure_sequence = any(step.get("trigger") == "pressure" for step in sequence)
    has_position_sequence = any(step.get("trigger") == "position" for step in sequence)
    if "sequence_valve" in type_set and not has_pressure_sequence:
        warnings.append("sequence valve selected without a pressure-triggered sequence requirement")
    if "position_valve" in type_set and not has_position_sequence and "two_speed_force_switching" not in drivers:
        warnings.append("position valve selected without a position-triggered phase requirement")
    if "flow_divider_combiner" in type_set and not any(
        (function.get("synchronization") or {}).get("strategy") == "flow_divider_parallel"
        for function in requirements.get("functions", [])
    ):
        warnings.append("flow divider selected without a flow_divider_parallel decision")

    pump_count = sum(value == "pump" for value in types)
    if pump_count >= 2 and any(word in plan.approach.casefold() for word in ("hi-lo", "high-low", "two-pump")):
        if "unloading_valve" not in type_set:
            errors.append("hi-lo/two-pump approach lacks a true unloading valve")
        if "check_valve" not in type_set:
            errors.append("hi-lo/two-pump approach lacks a plain check valve")

    score = 100.0 - 35.0 * len(errors) - 6.0 * len(warnings) - 0.5 * len(plan.components)
    return CandidateEvaluation(
        candidate_id=plan.candidate_id,
        eligible=not errors,
        score=score,
        component_count=len(plan.components),
        errors=errors,
        warnings=warnings,
    )


def evaluate_and_select_candidates(
    value: ComponentPlanSet,
    requirements: dict[str, Any],
) -> tuple[ComponentPlan, list[ComponentPlan], list[CandidateEvaluation]]:
    candidates: list[ComponentPlan] = []
    evaluations: list[CandidateEvaluation] = []
    seen_ids: set[str] = set()
    for index, candidate in enumerate(value.candidates, start=1):
        data = candidate.model_dump(mode="json")
        candidate_id = data.get("candidate_id") or f"candidate_{index}"
        if candidate_id in seen_ids:
            candidate_id = f"{candidate_id}_{index}"
        seen_ids.add(candidate_id)
        data["candidate_id"] = candidate_id
        normalized = ComponentPlan.model_validate(inject_plan_decisions(data, requirements))
        normalized = apply_repair_actions(normalized)
        candidates.append(normalized)
        evaluations.append(_candidate_evaluation(normalized, requirements))

    preferred = value.preferred_candidate_id
    ranked = sorted(
        zip(candidates, evaluations, strict=True),
        key=lambda pair: (
            pair[1].eligible,
            pair[1].score,
            pair[0].candidate_id == preferred,
            -pair[1].component_count,
        ),
        reverse=True,
    )
    selected = ranked[0][0]
    for evaluation in evaluations:
        evaluation.selected = evaluation.candidate_id == selected.candidate_id
    return selected, candidates, evaluations

