"""Deterministic propagation and checking of circuit-changing decisions.

The language-model stages may summarize requirements differently, but they are
not allowed to reinterpret metering or synchronization after requirements have
been finalized.  This module creates the canonical records once and copies them
through the brief, selected component plan, netlist, validation, and final
output.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schemas import (
    ActuatorChamber,
    FlowCompensation,
    FunctionBrief,
    FunctionSynchronizationDecision,
    LoadControlStrategy,
    LoadType,
    MeteredFlow,
    MeteringSide,
    MotionControlDecision,
    MotionDirection,
    RequirementsSpec,
    SynchronizationStrategy,
)


def motion_decisions_from_requirements(
    requirements: RequirementsSpec | dict[str, Any],
) -> list[MotionControlDecision]:
    spec = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    decisions: list[MotionControlDecision] = []
    for function in spec.functions:
        for phase in function.motion_phases:
            decisions.append(
                MotionControlDecision(
                    function_id=function.id,
                    phase_id=phase.id,
                    phase_name=phase.name,
                    motion=phase.motion,
                    load_type=phase.load_type,
                    metering_side=phase.metering_side,
                    metered_chamber=phase.metered_chamber,
                    metered_flow=phase.metered_flow,
                    flow_compensation=phase.flow_compensation,
                    load_control=phase.load_control,
                    justification=phase.motion_control_justification,
                    source=phase.motion_control_source,
                )
            )
    return decisions


def synchronization_decisions_from_requirements(
    requirements: RequirementsSpec | dict[str, Any],
) -> list[FunctionSynchronizationDecision]:
    spec = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    return [
        FunctionSynchronizationDecision(
            function_id=function.id,
            **function.synchronization.model_dump(mode="python"),
        )
        for function in spec.functions
        if function.synchronization.required or function.synchronization.actuator_count > 1
    ]


def expected_metered_chamber(
    motion: MotionDirection | str,
    metering_side: MeteringSide | str,
) -> ActuatorChamber:
    motion_value = MotionDirection(motion)
    side_value = MeteringSide(metering_side)
    if side_value in {MeteringSide.none, MeteringSide.undecided}:
        return ActuatorChamber.none if side_value == MeteringSide.none else ActuatorChamber.unspecified
    if motion_value == MotionDirection.extend:
        return ActuatorChamber.cap if side_value == MeteringSide.meter_in else ActuatorChamber.rod
    if motion_value == MotionDirection.retract:
        return ActuatorChamber.rod if side_value == MeteringSide.meter_in else ActuatorChamber.cap
    return ActuatorChamber.none


def motion_decision_issues(requirements: RequirementsSpec | dict[str, Any]) -> list[str]:
    """Return semantic errors in typed motion and synchronization decisions."""
    spec = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    issues: list[str] = []
    phase_ids = [phase.id for function in spec.functions for phase in function.motion_phases]
    duplicates = sorted({phase_id for phase_id in phase_ids if phase_ids.count(phase_id) > 1})
    if duplicates:
        issues.append(f"Motion phase ids must be globally unique; duplicates: {duplicates}.")

    for function in spec.functions:
        if function.actuator_type.value == "linear" and not function.motion_phases:
            issues.append(f"Linear function {function.id!r} has no typed motion phases.")
        for phase in function.motion_phases:
            prefix = f"Motion phase {phase.id!r}"
            if phase.motion == MotionDirection.unspecified:
                issues.append(f"{prefix} has unspecified motion direction.")
            if phase.metering_side == MeteringSide.undecided:
                issues.append(f"{prefix} has an undecided metering_side.")
            if phase.metering_side not in {MeteringSide.none, MeteringSide.undecided}:
                expected = expected_metered_chamber(phase.motion, phase.metering_side)
                if phase.metered_chamber != expected:
                    issues.append(
                        f"{prefix} declares {phase.metering_side.value} but meters the "
                        f"{phase.metered_chamber.value} chamber; expected {expected.value}."
                    )
                expected_flow = (
                    MeteredFlow.supply if phase.metering_side == MeteringSide.meter_in else MeteredFlow.exhaust
                )
                if phase.metered_flow != expected_flow:
                    issues.append(
                        f"{prefix} has metered_flow={phase.metered_flow.value}; expected {expected_flow.value}."
                    )
            if phase.speed_load_independent and phase.flow_compensation != FlowCompensation.pressure_compensated:
                issues.append(
                    f"{prefix} requires load-independent speed but does not select pressure_compensated flow control."
                )
            if (phase.speed_adjustable or phase.speed_load_independent) and phase.metering_side == MeteringSide.none:
                issues.append(f"{prefix} requires speed regulation but metering_side is none.")
            if (
                phase.load_type in {LoadType.overrunning, LoadType.both}
                and phase.metering_side == MeteringSide.meter_in
                and phase.load_control != LoadControlStrategy.counterbalance
            ):
                issues.append(
                    f"{prefix} has an overrunning load with meter-in-only control; use meter-out or counterbalance control."
                )
            if phase.load_control == LoadControlStrategy.unspecified:
                issues.append(f"{prefix} has an unspecified load_control decision.")

        synchronization = function.synchronization
        if synchronization.required and synchronization.strategy in {
            SynchronizationStrategy.none,
            SynchronizationStrategy.unspecified,
        }:
            issues.append(
                f"Function {function.id!r} requires synchronization but has no explicit synchronization strategy."
            )
        if synchronization.strategy == SynchronizationStrategy.rigid_platen_parallel and synchronization.actuator_count < 2:
            issues.append(
                f"Function {function.id!r} selects rigid_platen_parallel but actuator_count is less than two."
            )
        if (
            synchronization.strategy == SynchronizationStrategy.hydraulic_series
            and synchronization.series_displacement_compatibility == "unknown"
        ):
            issues.append(
                f"Function {function.id!r} selects hydraulic_series without representing displacement compatibility."
            )
    return issues


def inject_design_brief_decisions(
    brief: dict[str, Any],
    requirements: RequirementsSpec | dict[str, Any],
) -> dict[str, Any]:
    """Overwrite brief decisions with the finalized requirements source of truth."""
    output = deepcopy(brief)
    spec = requirements if isinstance(requirements, RequirementsSpec) else RequirementsSpec.model_validate(requirements)
    by_function: dict[str, list[MotionControlDecision]] = {}
    for decision in motion_decisions_from_requirements(spec):
        by_function.setdefault(decision.function_id, []).append(decision)

    existing = {item.get("function_id"): item for item in output.get("functions", [])}
    normalized: list[dict[str, Any]] = []
    for function in spec.functions:
        item = existing.get(function.id)
        if item is None:
            item = FunctionBrief(
                function_id=function.id,
                summary=function.description,
                actuator=function.actuator_type.value,
                load_type=function.load_type.value,
                holding=function.holding.notes or ("required" if function.holding.must_hold_position else "none"),
                speed_control="See typed motion_control_decisions.",
            ).model_dump(mode="json")
        else:
            item = deepcopy(item)
        item["motion_control_decisions"] = [
            decision.model_dump(mode="json") for decision in by_function.get(function.id, [])
        ]
        item["synchronization"] = function.synchronization.model_dump(mode="json")
        normalized.append(item)
    output["functions"] = normalized
    return output


def canonical_decision_payload(requirements: RequirementsSpec | dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    motion = [item.model_dump(mode="json") for item in motion_decisions_from_requirements(requirements)]
    synchronization = [
        item.model_dump(mode="json") for item in synchronization_decisions_from_requirements(requirements)
    ]
    return motion, synchronization


def inject_plan_decisions(plan: dict[str, Any], requirements: RequirementsSpec | dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(plan)
    motion, synchronization = canonical_decision_payload(requirements)
    output["motion_control_decisions"] = motion
    output["synchronization_decisions"] = synchronization
    return output


def inject_topology_decisions(topology: dict[str, Any], component_plan: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(topology)
    output["motion_control_decisions"] = deepcopy(component_plan.get("motion_control_decisions", []))
    output["synchronization_decisions"] = deepcopy(component_plan.get("synchronization_decisions", []))
    return output

