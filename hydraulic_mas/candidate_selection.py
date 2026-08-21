"""Deterministic scoring and structural repair for component-plan candidates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .catalog import CATALOG
from .decision_flow import inject_plan_decisions
from .gap_policy import blocking_catalog_gaps, requires_pressure_sequence_valve
from .schemas import CandidateEvaluation, ComponentPlan, ComponentPlanSet, RequirementsSpec


_THROTTLED_REALIZATIONS = {
    "load_sensitive_throttled",
    "adjustable_throttled",
    "load_independent_throttled",
}


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
        "regeneration_fast_approach": {"check_valve"},
        "load_holding": {"pilot_check_valve", "single_pilot_check_valve", "counterbalance_valve"},
        "counterbalance_overrunning": {"counterbalance_valve"},
        "pressure_limiting_stall": {"relief_valve"},
        "synchronization": {"flow_divider_combiner", "cylinder"},
        "branch_pressure_reduction": {"pressure_reducing_valve"},
    }
    for capability, accepted in capability_types.items():
        if capability in drivers and not type_set.intersection(accepted):
            errors.append(f"driver {capability} lacks one of {sorted(accepted)}")

    for function in requirements.get("functions", []):
        function_id = str(function.get("id"))
        holding = function.get("holding") or {}
        phases = function.get("motion_phases") or []
        needs_lock = any(
            holding.get(field)
            for field in ("must_hold_position", "no_creep", "hold_on_power_loss", "retain_on_hose_burst")
        ) or any(phase.get("load_control") == "pilot_check" for phase in phases)
        needs_counterbalance = any(phase.get("load_control") == "counterbalance" for phase in phases)
        assigned_types = {
            component.comp_type
            for component in plan.components
            if component.function_id in {None, function_id}
        }
        if needs_counterbalance and "counterbalance_valve" not in assigned_types:
            errors.append(f"{function_id}: typed counterbalance control has no assigned counterbalance valve")
        elif needs_lock and not assigned_types.intersection(
            {"pilot_check_valve", "single_pilot_check_valve", "counterbalance_valve"}
        ):
            errors.append(f"{function_id}: holding/no-drift requirement has no assigned load-lock valve")

    # blocking_catalog_gaps takes and returns plain dicts, so read the key
    # rather than an attribute. This path only executes when the planner emits a
    # genuinely blocking CatalogGap, which is why it survived until a live P7-01
    # run produced one.
    topology_gaps = blocking_catalog_gaps(
        [gap.model_dump(mode="json") for gap in plan.catalog_gaps]
    )
    if topology_gaps:
        errors.append(
            "topology catalog gaps: "
            + ", ".join(sorted({str(gap.get("capability")) for gap in topology_gaps}))
        )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    # A within-actuator load-triggered speed change does not justify a sequence
    # valve; only a trigger that gates a *different* function does.
    has_pressure_sequence = requires_pressure_sequence_valve(requirements)
    has_position_sequence = any(step.get("trigger") == "position" for step in sequence)
    if "sequence_valve" in type_set and not has_pressure_sequence:
        errors.append("sequence valve selected without a pressure-triggered sequence requirement")
    if "position_valve" in type_set and not has_position_sequence and "two_speed_force_switching" not in drivers:
        errors.append("position valve selected without a position-triggered phase requirement")
    # A hydraulic pressure interlock needs one command, not two.
    #
    # P7-05 oscillated for four rounds between piloting its forward sequence
    # valve from the pump line (which proves nothing about clamp force) and from
    # the clamp line (which goes dead the moment the clamp valve is not
    # commanded). Both are unavoidable once the clamp and the drill have
    # *separate* directional valves: the clamp branch can be de-commanded
    # independently, so no clamp-referenced pilot survives every phase where the
    # sequenced branch must stay open.
    #
    # Sharing one directional valve removes the problem. The clamp line stays
    # live for the whole forward command, so the pilot is both clamp-referenced
    # and alive. It is also the only way the ordering is genuinely hydraulic:
    # two independently switched valves make the sequence a property of the
    # control wiring, which is exactly what these briefs forbid.
    dcv_ids = [component.id for component in plan.components if component.comp_type == "dcv"]
    if has_pressure_sequence and len(dcv_ids) > 1:
        errors.append(
            "pressure-sequenced functions are commanded by "
            + str(len(dcv_ids))
            + " separate directional valves ("
            + ", ".join(sorted(dcv_ids))
            + "); a hydraulic pressure interlock requires one common command valve so the "
            "prerequisite branch stays supplied while the sequenced branch is open"
        )

    if "flow_divider_combiner" in type_set and not any(
        (function.get("synchronization") or {}).get("strategy") == "flow_divider_parallel"
        for function in requirements.get("functions", [])
    ):
        warnings.append("flow divider selected without a flow_divider_parallel decision")

    regulated_paths: set[tuple[str, str, str]] = set()
    required_flow_types: set[str] = set()
    for function in requirements.get("functions", []):
        function_id = str(function.get("id"))
        for phase in function.get("motion_phases") or []:
            realization = str(phase.get("speed_realization") or "sizing_only")
            if realization not in _THROTTLED_REALIZATIONS:
                continue
            required_type = (
                "pressure_comp_flow_control"
                if realization == "load_independent_throttled"
                else "one_way_flow_control"
            )
            required_flow_types.add(required_type)
            regulated_paths.add((function_id, str(phase.get("metered_chamber")), required_type))

    selected_flow_controls = [
        component.id
        for component in plan.components
        if component.comp_type in {"one_way_flow_control", "pressure_comp_flow_control"}
    ]
    if selected_flow_controls and not regulated_paths:
        errors.append(
            "unjustified flow-control component(s) for sizing-only/unrestricted phases: "
            + ", ".join(selected_flow_controls)
        )
    for required_type in sorted(required_flow_types):
        if required_type not in type_set:
            errors.append(f"typed speed realization requires {required_type}")
    if len(selected_flow_controls) > len(regulated_paths):
        warnings.append(
            f"{len(selected_flow_controls)} flow controls selected for only {len(regulated_paths)} distinct typed metering path(s)"
        )

    global_limit = ((requirements.get("global_constraints") or {}).get("max_system_pressure") or {}).get("value")
    lower_pressure_functions = []
    if isinstance(global_limit, (int, float)):
        lower_pressure_functions = [
            str(function.get("id"))
            for function in requirements.get("functions", [])
            if function.get("branch_pressure_limit_required")
            and isinstance((function.get("max_working_pressure") or {}).get("value"), (int, float))
            and float(function["max_working_pressure"]["value"]) < float(global_limit)
        ]
    if lower_pressure_functions and "pressure_reducing_valve" not in type_set:
        errors.append(
            "lower-pressure function branch(es) require a pressure-reducing valve: "
            + ", ".join(lower_pressure_functions)
        )
    if "pressure_reducing_valve" in type_set and not lower_pressure_functions and "branch_pressure_reduction" not in drivers:
        warnings.append("pressure-reducing valve selected without a lower branch-pressure requirement")

    for gap in plan.evidence_gaps:
        warnings.append(f"evidence gap ({gap.capability}): {gap.reason}")

    pump_count = sum(value == "pump" for value in types)
    hi_lo_wording = any(word in plan.approach.casefold() for word in ("hi-lo", "high-low", "two-pump"))
    if pump_count >= 2 and hi_lo_wording:
        if "unloading_valve" not in type_set:
            errors.append("hi-lo/two-pump approach lacks a true unloading valve")
        if "check_valve" not in type_set:
            errors.append("hi-lo/two-pump approach lacks a plain check valve")

    # v0.4.0 minimality. A hi-lo supply is an energy-saving pattern: it belongs
    # in a circuit only when the requirements ask for one. Selecting it anyway
    # adds a pump, an unloading valve and two checks that later trip the
    # unjustified-component rules, and the repair loop then spends its rounds
    # relocating hardware that should never have been chosen.
    energy_driver = bool(
        {"energy_saving_hi_lo", "high_low_supply", "standby_unloading"}.intersection(drivers)
    )
    if pump_count >= 2 and not energy_driver:
        errors.append(
            f"{pump_count} pumps selected without an energy-saving/hi-lo requirement; "
            "a single supply satisfies this brief"
        )

    # Plain check valves are justified by pump combining, regeneration or an
    # explicitly required one-way isolation - not as generic "protection".
    check_ids = [component.id for component in plan.components if component.comp_type == "check_valve"]
    regenerative_required = any(
        phase.get("speed_realization") == "regenerative"
        for function in requirements.get("functions", [])
        for phase in function.get("motion_phases") or []
    )
    if check_ids and not (regenerative_required or (pump_count >= 2 and energy_driver)):
        errors.append(
            "plain check valve(s) selected without a pump-combining or regenerative justification: "
            + ", ".join(check_ids)
            + "; sequence, reducing and one-way flow-control classes already carry integral reverse checks"
        )

    # A pilot-operated load lock can only reseat if the neutral vents its pilot.
    # Catch the pairing at selection time so the netlist builder is never asked
    # to wire a hold that cannot be proven.
    # Only an *unpowered* hold needs a venting centre.
    #
    # An earlier revision of this rule keyed off "the function has a hold phase",
    # which is too broad. A clamp held at reduced pressure while the directional
    # valve stays commanded forward - P7-07's clamp during the working stroke -
    # is a hold phase, but its lock pilot is fed from a live line, not trapped by
    # a centred spool. Forcing a float centre there would be a false constraint.
    #
    # The distinguishing requirement is whether the load must stay put with the
    # command released: a stated hold duration, a drift tolerance, a no-creep
    # clause or a power-loss/hose-burst retention clause. Those are the cases
    # where the spool really does centre over the load.
    unpowered_hold_functions = {
        str(function.get("id"))
        for function in requirements.get("functions", [])
        if any(str(phase.get("motion")) == "hold" for phase in function.get("motion_phases") or [])
        and (
            any(
                (function.get("holding") or {}).get(field)
                for field in ("no_creep", "hold_on_power_loss", "retain_on_hose_burst")
            )
            or (function.get("holding") or {}).get("hold_duration") is not None
            or (function.get("holding") or {}).get("drift_tolerance") is not None
        )
    }
    lock_ids = [
        component.id
        for component in plan.components
        if component.comp_type in {"pilot_check_valve", "single_pilot_check_valve"}
        and component.function_id in unpowered_hold_functions
    ]
    if lock_ids:
        venting_neutral = any(
            any(
                path.get("kind") == "neutral_vent"
                for path in ((CATALOG.get(component.catalog_key) or {}).get("state_paths") or {}).get(
                    "neutral", []
                )
            )
            for component in plan.components
            if component.comp_type == "dcv"
        )
        dcv_present = "dcv" in type_set
        if dcv_present and not venting_neutral:
            errors.append(
                "load lock(s) "
                + ", ".join(lock_ids)
                + " are paired with a directional valve whose neutral blocks the work ports; "
                "select a float-centre or open-centre neutral so the pilot can decay and the poppet reseats"
            )

    score = 100.0 - 35.0 * len(errors) - 6.0 * len(warnings) - 1.5 * len(plan.components)
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
    requirements = RequirementsSpec.model_validate(requirements).model_dump(mode="json")
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
