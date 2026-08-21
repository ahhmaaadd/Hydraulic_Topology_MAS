from __future__ import annotations

import re

from typing import Any

from .schemas import (
    FlowCompensation,
    LoadControlStrategy,
    LoadType,
    MeteredFlow,
    MeteringSide,
    MotionDirection,
    RequirementsSpec,
    SequenceTrigger,
    SpeedRealization,
)
from .decision_flow import expected_metered_chamber, motion_decision_issues


_THROTTLED = {
    SpeedRealization.load_sensitive_throttled,
    SpeedRealization.adjustable_throttled,
    SpeedRealization.load_independent_throttled,
}

# Two phases whose forces differ by less than this are treated as the same
# operating point, so an ordinary throttle can serve both.
_LOAD_CHANGE_RATIO = 1.25


def _magnitude(quantity) -> float | None:
    if quantity is None:
        return None
    value = getattr(quantity, "value", None)
    return float(value) if isinstance(value, (int, float)) else None


_UNMETERED_REALIZATIONS = {
    SpeedRealization.sizing_only,
    SpeedRealization.unrestricted_rapid,
}

# Two actuators on one supply whose commanded speeds differ by less than this can
# plausibly be reconciled by bore choice. Beyond it they cannot.
_SHARED_SUPPLY_SPEED_RATIO = 3.0

_SPEED_TO_MM_PER_S = {
    "mm/s": 1.0,
    "mm/sec": 1.0,
    "m/s": 1000.0,
    "m/min": 1000.0 / 60.0,
    "mm/min": 1.0 / 60.0,
    "cm/s": 10.0,
}


def _speed_mm_per_s(quantity) -> float | None:
    if quantity is None:
        return None
    value = getattr(quantity, "value", None)
    if not isinstance(value, (int, float)):
        return None
    factor = _SPEED_TO_MM_PER_S.get(str(getattr(quantity, "unit", "") or "").strip().casefold())
    return float(value) * factor if factor else None


def _normalize_shared_supply_speeds(spec: RequirementsSpec, notes: list[str]) -> None:
    """Throttle the slower actuator when one pump must serve two commanded speeds.

    A live P7-05 run returned a validated circuit with no flow control on the
    drill feed. Both the clamp (25 mm/s) and the drill (1.667 mm/s) were typed
    ``sizing_only``, meaning "choose the pump and the bores to get this speed".
    On a shared supply that is not something sizing can deliver.

    The two actuators run sequentially from one fixed pump in the same command
    direction, so each in turn receives the whole delivery and moves at
    ``Q_pump / A``. Their areas are already fixed by their force requirements and
    the pump is already fixed by the faster motion, which leaves the slower
    actuator's speed over-determined. In the P7-05 numbers the pump is sized at
    18.4 L/min for the clamp, and feeding that to the drill gives 98 mm/s against
    a required 1.667 mm/s - a factor of 59. Reaching the required speed by bore
    choice alone would need a 484 mm drill cylinder.

    So the slower motion needs a metered path. Only the fastest phase in a
    direction can stay sizing-only; the pump is sized for that one. Meter-out is
    the conservative default - it holds the actuator back rather than letting it
    run away when a drill breaks through or a load drops off.
    """
    directional: dict[str, list[tuple[str, str, float, Any]]] = {}
    for function in spec.functions:
        for phase in function.motion_phases:
            if phase.speed_realization not in _UNMETERED_REALIZATIONS:
                continue
            speed = _speed_mm_per_s(phase.speed)
            if speed is None or speed <= 0:
                continue
            directional.setdefault(phase.motion.value, []).append(
                (function.id, phase.id, speed, phase)
            )

    for motion, entries in directional.items():
        if len({function_id for function_id, _, _, _ in entries}) < 2:
            continue
        fastest = max(entry[2] for entry in entries)
        slower = [entry for entry in entries if fastest >= _SHARED_SUPPLY_SPEED_RATIO * entry[2]]
        if not slower:
            continue
        for function_id, phase_id, speed, phase in slower:
            phase.speed_realization = SpeedRealization.adjustable_throttled
            phase.metering_side = MeteringSide.meter_out
            phase.metered_chamber = expected_metered_chamber(phase.motion, MeteringSide.meter_out)
            phase.metered_flow = MeteredFlow.exhaust
            phase.flow_compensation = FlowCompensation.non_compensated
            phase.speed_adjustable = True
            notes.append(
                f"Phase {phase_id!r} promoted to an adjustable meter-out throttle: one supply also "
                f"serves a {fastest:g} mm/s {motion} motion, so a {speed:g} mm/s motion on the same "
                "pump cannot be realized by pump and bore sizing alone."
            )


_HOLDING_FIELDS = ("must_hold_position", "no_creep", "hold_on_power_loss", "retain_on_hose_burst")


def _normalize_load_actuated_group(spec: RequirementsSpec, notes: list[str]) -> None:
    """One fixed orifice restricts every phase that flows through it.

    P7-04's extraction typed the approach ``unrestricted_rapid`` and the working
    feed ``load_sensitive_throttled``, both extending. That pair cannot be built.
    A non-compensated throttle sitting in the rod line is in circuit for the
    whole extend stroke; the approach is not unrestricted, it is simply less
    restricted, because the load is lower and the pressure drop across the
    orifice is larger. There is no mechanism that could remove the restriction
    for one phase and reinstate it for the next.

    Something *can* do that - a position-operated bypass tripped by a cam, which
    is exactly the P7-01 and P7-02 rapid/feed pattern. So the promotion below
    applies only when the requirements contain no position trigger for this
    function. Where a position trigger exists the bypass is real and the
    unrestricted phase is genuine.

    Left alone, the mismatch is fatal: the unmetered phase demands a bypass path,
    the metered phase forbids one, and no topology satisfies both.
    """
    sequence = (spec.operational_logic.sequence if spec.operational_logic else []) or []
    for function in spec.functions:
        phase_ids = {phase.id for phase in function.motion_phases}
        has_position_trigger = any(
            step.trigger == SequenceTrigger.position and step.phase_id in phase_ids
            for step in sequence
        )
        if has_position_trigger:
            continue
        by_motion: dict[str, list] = {}
        for phase in function.motion_phases:
            by_motion.setdefault(phase.motion.value, []).append(phase)
        for motion, phases in by_motion.items():
            throttled = [
                phase
                for phase in phases
                if phase.speed_realization == SpeedRealization.load_sensitive_throttled
            ]
            if not throttled:
                continue
            template = throttled[0]
            unmetered = [
                phase
                for phase in phases
                if phase.speed_realization == SpeedRealization.unrestricted_rapid
            ]
            for phase in unmetered:
                phase.speed_realization = SpeedRealization.load_sensitive_throttled
                phase.metering_side = template.metering_side
                phase.metered_chamber = template.metered_chamber
                phase.metered_flow = template.metered_flow
                phase.flow_compensation = template.flow_compensation
                notes.append(
                    f"Phase {phase.id!r} changed from 'unrestricted_rapid' to "
                    f"'load_sensitive_throttled': it shares the {motion} path with throttled phase "
                    f"{template.id!r}, and with no position trigger there is no bypass that could "
                    "take the restriction out of circuit for this phase alone."
                )


def _holding_required(function) -> bool:
    holding = function.holding
    if holding is None:
        return False
    if any(getattr(holding, field, False) for field in _HOLDING_FIELDS):
        return True
    return holding.hold_duration is not None or holding.drift_tolerance is not None


def _normalize_load_control(spec: RequirementsSpec, notes: list[str]) -> None:
    """Resolve ``load_control=unspecified`` deterministically instead of blocking.

    A live P7-07 run failed the requirements gate on a single issue: one hold
    phase carried ``load_control=unspecified``.  The critic had already returned
    ``proceed_with_assumptions`` with no blocking questions, the function's
    ``holding.must_hold_position`` was true, and ``load_holding`` was already a
    derived design driver - so the intent was unambiguous.  The model simply
    declined to name a strategy, ``unspecified`` reached the structural check,
    and the whole run stopped before research.

    ``unspecified`` means "the extractor did not decide", and the decision is
    derivable from the load type and the holding contract.  Every rule below is
    conservative: it can only add load control, never remove it.  Each change is
    recorded so the choice stays auditable.
    """
    for function in spec.functions:
        holding_required = _holding_required(function)
        for phase in function.motion_phases:
            if phase.load_control != LoadControlStrategy.unspecified:
                continue
            if phase.load_type in {LoadType.overrunning, LoadType.both}:
                phase.load_control = LoadControlStrategy.counterbalance
                reason = (
                    "the load is overrunning, which needs dynamic control rather than a seated check"
                )
            elif holding_required and phase.motion == MotionDirection.hold:
                phase.load_control = LoadControlStrategy.pilot_check
                reason = (
                    "the function declares a holding requirement and this phase is a hold, "
                    "so the load must be retained by a seated load lock"
                )
            elif phase.motion == MotionDirection.hold:
                phase.load_control = LoadControlStrategy.none
                reason = (
                    "the phase is a resistive hold with no declared retention requirement, "
                    "so no load-control element is implied"
                )
            else:
                phase.load_control = LoadControlStrategy.none
                reason = (
                    "the phase is a powered resistive motion with no declared retention "
                    "requirement, so no load-control element is implied"
                )
            notes.append(
                f"Phase {phase.id!r} load_control resolved from 'unspecified' to "
                f"{phase.load_control.value!r} because {reason}."
            )


def _normalize_flow_compensation(spec: RequirementsSpec, notes: list[str]) -> None:
    """Promote a throttle to a pressure-compensated one where physics demands it.

    A plain (non-compensated) throttle passes a flow that depends on the
    pressure drop across it, so the actuator speed moves with the load.  That is
    exactly the mechanism a load-actuated speed change relies on, and exactly
    what breaks a "hold this feed rate whatever the load does" requirement.

    Two deterministic cases are promoted here rather than left to the designer:

    1. a phase that explicitly declares the speed load-independent; and
    2. two throttled phases of one function, in the same direction, commanded to
       the same speed while the force differs materially - a non-compensated
       throttle cannot hold one speed across both.
    """
    for function in spec.functions:
        throttled = [phase for phase in function.motion_phases if phase.speed_realization in _THROTTLED]
        for phase in throttled:
            if (
                phase.speed_load_independent
                and phase.flow_compensation != FlowCompensation.pressure_compensated
            ):
                phase.flow_compensation = FlowCompensation.pressure_compensated
                phase.speed_realization = SpeedRealization.load_independent_throttled
                notes.append(
                    f"Phase {phase.id!r} promoted to a pressure-compensated throttle because the "
                    "speed is declared load-independent."
                )

        by_motion: dict[str, list] = {}
        for phase in throttled:
            by_motion.setdefault(phase.motion.value, []).append(phase)
        for motion, phases in by_motion.items():
            if len(phases) < 2:
                continue
            speeds = {_magnitude(phase.speed) for phase in phases}
            forces = [value for value in (_magnitude(phase.force) for phase in phases) if value]
            if len(speeds) != 1 or None in speeds or len(forces) < 2:
                continue
            if max(forces) < _LOAD_CHANGE_RATIO * min(forces):
                continue
            uncompensated = [
                phase
                for phase in phases
                if phase.flow_compensation != FlowCompensation.pressure_compensated
            ]
            if not uncompensated:
                continue
            for phase in uncompensated:
                phase.flow_compensation = FlowCompensation.pressure_compensated
                phase.speed_realization = SpeedRealization.load_independent_throttled
            notes.append(
                f"Function {function.id!r} keeps one {motion} speed across loads "
                f"{min(forces):g}-{max(forces):g}; phases "
                f"{[phase.id for phase in uncompensated]} promoted to a pressure-compensated "
                "throttle because a plain throttle would change speed with the load."
            )


_PHYSICAL_MOTION_NOUNS = ("slide", "ram", "platen", "carriage", "table", "cylinder", "actuator")


def normalize_requirements(
    requirements: RequirementsSpec | dict,
    user_query: str | None = None,
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
        if overlap:
            step.forbidden_overlap_function_ids = [
                function_id
                for function_id in step.forbidden_overlap_function_ids
                if function_id not in active
            ]
            notes.append(
                f"Sequence step {step.order} removed active function ids {overlap} from "
                "forbidden_overlap_function_ids."
            )

        # Initial/external/simultaneous commands describe operator/controller
        # modes, not an automatic hydraulic transition.  Marking them as
        # hydraulically enforced creates an impossible sequence-valve demand.
        if step.hydraulically_enforced and step.trigger in {
            SequenceTrigger.initial_command,
            SequenceTrigger.external_command,
            SequenceTrigger.simultaneous,
        }:
            step.hydraulically_enforced = False
            notes.append(
                f"Sequence step {step.order} changed hydraulically_enforced to false because "
                f"trigger={step.trigger.value} is command logic, not a hydraulic trigger."
            )

        # Some legacy extractions marked an operator-controlled single-actuator
        # mode as hydraulic while leaving the trigger unspecified.  The source
        # wording makes this normalization safe; automatic/pressure/position
        # sequences are deliberately excluded.
        query = (user_query or "").casefold()
        operator_controlled = any(
            phrase in query
            for phrase in ("operator must", "on command", "releasing the command", "release the command")
        )
        automatic_wording = any(
            phrase in query
            for phrase in ("automatically", "automatic", "after pressure", "once full", "only after")
        )
        if (
            step.hydraulically_enforced
            and step.trigger == SequenceTrigger.unspecified
            and operator_controlled
            and not automatic_wording
            and len(spec.functions) == 1
        ):
            step.hydraulically_enforced = False
            step.trigger = SequenceTrigger.external_command
            notes.append(
                f"Sequence step {step.order} normalized to an external command because the source "
                "describes one operator-controlled actuator and no automatic transition."
            )
    _normalize_flow_compensation(spec, notes)
    _normalize_load_control(spec, notes)
    _normalize_shared_supply_speeds(spec, notes)
    _normalize_load_actuated_group(spec, notes)
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

    throttled = {
        SpeedRealization.load_sensitive_throttled,
        SpeedRealization.adjustable_throttled,
        SpeedRealization.load_independent_throttled,
    }
    for function in spec.functions:
        if function.branch_pressure_limit_required and (
            function.max_working_pressure is None or function.max_working_pressure.value is None
        ):
            issues.append(
                f"Function {function.id!r} requires independent branch pressure limiting but has no "
                "max_working_pressure value."
            )
        for direction in {phase.motion for phase in function.motion_phases}:
            phases = [phase for phase in function.motion_phases if phase.motion == direction]
            numeric_speeds = {
                (phase.speed.value, phase.speed.unit)
                for phase in phases
                if phase.speed is not None and phase.speed.value is not None
            }
            if len(phases) < 2 or len(numeric_speeds) < 2:
                continue
            if not any(phase.speed_realization in throttled for phase in phases):
                issues.append(
                    f"Function {function.id!r} has multiple {direction.value} phases with distinct speeds, "
                    "but none declares the throttled topology that realizes the speed change."
                )

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
        if step.hydraulically_enforced and step.trigger in {
            SequenceTrigger.unspecified,
            SequenceTrigger.initial_command,
            SequenceTrigger.external_command,
            SequenceTrigger.simultaneous,
        }:
            issues.append(
                f"Sequence step {step.order} is marked hydraulically_enforced but trigger="
                f"{step.trigger.value!r} does not identify a hydraulic transition."
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
