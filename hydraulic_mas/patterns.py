"""Deterministic, problem-agnostic hydraulic topology pattern guidance.

The library contains engineering rules rather than solved benchmark circuits.
It translates typed requirements into small connection/state invariants that the
LLM planner and netlist builder can compose.  The deterministic validator still
owns the verdict.
"""

from __future__ import annotations

import json
from typing import Any

from .gap_policy import interfunction_sequence_steps


THROTTLED_REALIZATIONS = {
    "load_sensitive_throttled",
    "adjustable_throttled",
    "load_independent_throttled",
}


def _quantity_value(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("value")
    return float(raw) if isinstance(raw, (int, float)) else None


def derive_pattern_hints(requirements: dict[str, Any]) -> list[dict[str, Any]]:
    """Return applicable generic patterns and their testable invariants."""
    hints: list[dict[str, Any]] = [
        {
            "id": "open_circuit_power_and_relief",
            "applies_to": ["system"],
            "required_types": ["tank", "pump", "relief_valve"],
            "connection_rules": [
                "Tank.S -> Pump.S",
                "Pump.P branches directly to ReliefValve.P and every supplied control branch",
                "ReliefValve.T and valve returns/drains terminate at Tank.R",
            ],
            "phase_rules": [],
            "prohibitions": ["Do not create tee/manifold/line components for branches."],
        }
    ]

    functions = requirements.get("functions") or []
    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    global_limit = _quantity_value((requirements.get("global_constraints") or {}).get("max_system_pressure"))

    for function in functions:
        function_id = str(function.get("id") or "unknown")
        phases = function.get("motion_phases") or []
        realizations = {str(phase.get("speed_realization") or "sizing_only") for phase in phases}
        throttled = [phase for phase in phases if phase.get("speed_realization") in THROTTLED_REALIZATIONS]

        if phases and not throttled and realizations <= {"sizing_only", "unrestricted_rapid"}:
            hints.append(
                {
                    "id": f"{function_id}_sizing_only_speed",
                    "applies_to": [function_id],
                    "required_types": [],
                    "connection_rules": [],
                    "phase_rules": [
                        "Numeric speed/force/travel targets are deferred to pump/cylinder sizing and do not justify a flow-control valve."
                    ],
                    "prohibitions": ["Do not add a flow-control valve solely because a numeric speed is present."],
                }
            )

        for phase in throttled:
            realization = str(phase.get("speed_realization"))
            flow_type = (
                "pressure_comp_flow_control"
                if realization == "load_independent_throttled"
                else "one_way_flow_control"
            )
            hints.append(
                {
                    "id": f"{phase.get('id')}_typed_metering",
                    "applies_to": [function_id, str(phase.get("id"))],
                    "required_types": [flow_type],
                    "connection_rules": [
                        f"Place exactly one {flow_type} in the declared {phase.get('metering_side')} "
                        f"path at the {phase.get('metered_chamber')} chamber ({phase.get('metered_flow')})."
                    ],
                    "phase_rules": ["The metered phase must have no simultaneously open unmetered bypass."],
                    "prohibitions": ["Do not add separate approach/return controls unless another typed phase requires them."],
                }
            )

        # A load-actuated speed change is a property of a plain throttle, not a
        # circuit that has to be built out of extra valves.  Without this hint
        # planners reached for a bypass check plus a sequence valve plus a
        # second pump, all of which are then correctly rejected as unjustified.
        load_sensitive = [
            phase for phase in phases if phase.get("speed_realization") == "load_sensitive_throttled"
        ]
        function_phase_ids_for_load = {str(phase.get("id")) for phase in phases if phase.get("id")}
        # Only a trigger that gates a *different* function, or a position
        # trigger needing a cam-operated valve, takes this function out of the
        # minimal single-throttle pattern. A pressure trigger inside this same
        # actuator is exactly what the throttle characteristic realizes, and
        # treating it as a sequencing requirement is what made P7-04
        # unsatisfiable: the validator demanded a sequence valve while the
        # minimality rules rejected the same valve.
        interfunction_phase_ids = {
            str(step.get("phase_id")) for step in interfunction_sequence_steps(requirements)
        }
        externally_triggered = [
            step
            for step in sequence
            if step.get("hydraulically_enforced")
            and step.get("phase_id") in function_phase_ids_for_load
            and (
                step.get("trigger") == "position"
                or (
                    step.get("trigger") == "pressure"
                    and str(step.get("phase_id")) in interfunction_phase_ids
                )
            )
        ]
        if load_sensitive and not externally_triggered:
            hints.append(
                {
                    "id": f"{function_id}_load_actuated_speed_change_minimal",
                    "applies_to": [function_id, *[str(phase.get("id")) for phase in load_sensitive]],
                    "required_types": ["one_way_flow_control"],
                    "connection_rules": [
                        "Use exactly one non-compensated one-way flow control in the declared metering path.",
                        "The same single throttle serves every load-sensitive phase of this function; "
                        "it is not switched, gated or bypassed.",
                    ],
                    "phase_rules": [
                        "The throttle stays in circuit in every load-sensitive phase and keeps the same state.",
                        "Speed falls as the load rises because the pressure drop across the fixed "
                        "restriction falls; no valve has to change state for that to happen.",
                        "The free-reverse check inside the same one-way flow control handles the return.",
                    ],
                    "prohibitions": [
                        "Do not add a parallel bypass check around the throttle: it would make the "
                        "metered path irrelevant and the speed change would never occur.",
                        "Do not add a sequence valve to 'enable' the feed. The load change itself is "
                        "the trigger, and there is no commanded phase transition to interlock.",
                        "Do not add a second pump or an unloading valve. A hi-lo supply is an "
                        "energy-saving pattern and is not requested here.",
                    ],
                }
            )

        function_phase_ids = {str(phase.get("id")) for phase in phases if phase.get("id")}
        position_steps = [
            step
            for step in sequence
            if step.get("trigger") == "position" and step.get("phase_id") in function_phase_ids
        ]
        rapid_phases = [phase for phase in phases if phase.get("speed_realization") == "unrestricted_rapid"]
        if position_steps and rapid_phases and throttled:
            hints.append(
                {
                    "id": f"{function_id}_position_rapid_feed_bypass",
                    "applies_to": [function_id],
                    "required_types": ["position_valve"],
                    "connection_rules": [
                        "Connect the position-operated valve in parallel around the single typed feed-control path.",
                        "Both parallel legs must share the same upstream and downstream hydraulic nodes.",
                    ],
                    "phase_rules": [
                        "Bypass open during unrestricted rapid motion.",
                        "Bypass closed after the position trip during controlled feed.",
                        "Use the free-reverse direction or reopened bypass for the unmetered return when required.",
                    ],
                    "prohibitions": ["Do not close the bypass in every phase; that destroys the rapid/feed changeover."],
                }
            )

        if "regenerative" in realizations:
            hints.append(
                {
                    "id": f"{function_id}_regenerative_extension",
                    "applies_to": [function_id],
                    "required_types": ["check_valve"],
                    "connection_rules": [
                        "During the regenerative phase, route the exhausting chamber through a one-way path into the supplied chamber.",
                        "The pump supply and recirculated flow meet at the supplied chamber branch.",
                    ],
                    "phase_rules": ["A regenerative phase needs a proven chamber-to-chamber recirculation path instead of a tank exhaust path."],
                    "prohibitions": ["Do not require rod exhaust to tank while regeneration is active."],
                }
            )

        holding = function.get("holding") or {}
        load_controls = {str(phase.get("load_control") or "none") for phase in phases}
        if "counterbalance" in load_controls:
            hints.append(
                {
                    "id": f"{function_id}_counterbalance",
                    "applies_to": [function_id],
                    "required_types": ["counterbalance_valve"],
                    "connection_rules": ["Place the counterbalance valve at the load-supporting actuator port and pilot it from the opposite work line."],
                    "phase_rules": ["Select controlled_release only when opposite-line pilot pressure is available."],
                    "prohibitions": ["A pilot-operated check is not dynamic overrunning-load control."],
                }
            )
        elif any(
            holding.get(key)
            for key in ("must_hold_position", "no_creep", "hold_on_power_loss", "retain_on_hose_burst")
        ) or "pilot_check" in load_controls:
            both_directions = len({phase.get("motion") for phase in phases if phase.get("motion") in {"extend", "retract"}}) > 1
            hints.append(
                {
                    "id": f"{function_id}_load_lock",
                    "applies_to": [function_id],
                    "required_types": ["pilot_check_valve" if both_directions else "single_pilot_check_valve"],
                    "connection_rules": [
                        "Install the load lock at the actuator work port(s) and provide hydraulic pilot release from the opposite commanded line.",
                        "Pair the load lock with a directional-valve neutral that vents both work lines "
                        "to tank: use a float centre with a pressure-compensated pump, or an open centre "
                        "with a fixed-displacement pump.",
                    ],
                    "phase_rules": [
                        "Neutral/power-off must leave the load-supporting reverse path blocked.",
                        "In the hold phase the lock's pilot must have a directed path to tank so the "
                        "poppet can reseat; otherwise trapped pilot pressure holds it cracked open and "
                        "the load drifts.",
                    ],
                    "prohibitions": [
                        "Do not rely on DCV spool leakage alone for a timed no-drift requirement.",
                        "Do not combine a tandem- or closed-centre neutral with a pilot-operated load "
                        "lock: both block the work ports, so the pilot pressure cannot decay.",
                    ],
                }
            )

        sync = function.get("synchronization") or {}
        strategy = sync.get("strategy")
        if strategy == "rigid_platen_parallel":
            hints.append(
                {
                    "id": f"{function_id}_rigid_platen_parallel",
                    "applies_to": [function_id],
                    "required_types": ["cylinder"],
                    "connection_rules": [
                        "Use the declared actuator_count cylinders in parallel: every cap shares one DCV work branch and every rod shares the opposite branch."
                    ],
                    "phase_rules": ["The rigid shared structure is the synchronization mechanism."],
                    "prohibitions": ["Never connect one cylinder chamber to another for a rigid-platen requirement."],
                }
            )
        elif strategy == "flow_divider_parallel":
            hints.append(
                {
                    "id": f"{function_id}_flow_divider_parallel",
                    "applies_to": [function_id],
                    "required_types": ["flow_divider_combiner", "cylinder"],
                    "connection_rules": ["Divide the supply to parallel actuator chambers and combine the corresponding reverse flow."],
                    "phase_rules": [],
                    "prohibitions": ["Do not series-connect the actuators."],
                }
            )

        function_limit = _quantity_value(function.get("max_working_pressure"))
        if (
            function.get("branch_pressure_limit_required")
            and function_limit is not None
            and global_limit is not None
            and function_limit < global_limit
        ):
            hints.append(
                {
                    "id": f"{function_id}_reduced_pressure_branch",
                    "applies_to": [function_id],
                    "required_types": ["pressure_reducing_valve"],
                    "connection_rules": ["Feed this function through a pressure-reducing valve and connect its drain/relief port to Tank.R."],
                    "phase_rules": [],
                    "prohibitions": ["A system relief valve at the higher global ceiling does not enforce the lower branch ceiling."],
                }
            )

    hydraulic_steps = [step for step in sequence if step.get("hydraulically_enforced")]
    interfunction_ids = {
        str(step.get("phase_id")) for step in interfunction_sequence_steps(requirements)
    }
    pressure_steps = [
        step
        for step in hydraulic_steps
        if step.get("trigger") == "pressure" and str(step.get("phase_id")) in interfunction_ids
    ]
    if pressure_steps:
        related = sorted({str(fid) for step in pressure_steps for fid in step.get("function_ids", [])})
        hints.append(
            {
                "id": "pressure_sequence_between_functions",
                "applies_to": related or ["system"],
                "required_types": ["sequence_valve"],
                "connection_rules": [
                    "Command every pressure-sequenced function from ONE directional valve. Separate valves "
                    "let the prerequisite branch be de-commanded independently, which kills any pilot "
                    "referenced to it and makes the ordering a property of the control wiring rather than "
                    "of the hydraulics.",
                    "Supply the later function through a sequence valve whose hydraulic trigger represents completion/pressure of the prerequisite function.",
                    "Connect every sequence-valve drain to Tank.R and preserve a reverse-return path.",
                ],
                "phase_rules": [
                    "The sequence valve is closed before the prerequisite pressure and open for the sequenced phase.",
                    "In every phase where the valve is declared open, its pilot node must have a directed "
                    "path from a pump. The directed phase graphs prove pressure from a supply path, so a "
                    "chamber that is only holding pressure does not count as pilot pressure.",
                ],
                "prohibitions": [
                    "Do not use an electrical pressure switch when the requirement forbids it.",
                    "Do not give the prerequisite and sequenced functions their own directional valves.",
                    "Do not pilot the sequence valve from the common pump line: pump pressure rises for many "
                    "reasons and proves nothing about the prerequisite actuator. Pilot it from the "
                    "prerequisite actuator's own supply line, which stays live because the two functions "
                    "share one command.",
                    "Do not pilot a sequence valve from a chamber isolated by a load lock, or from a work "
                    "line whose directional valve is centred in the sequenced phase. The pilot dies exactly "
                    "when it is needed. Take the pilot from a line that stays commanded and pump-supplied "
                    "for the whole sequenced phase.",
                    "Do not centre the directional valve of a function that must stay force-applied while "
                    "another function is running. A clamp held by a checked-in volume is not a maintained "
                    "clamp force, and any line piloted from it goes dead.",
                ],
            }
        )

    sequence_text = " ".join(
        f"{step.get('description', '')} {step.get('precondition', '')}"
        for step in sequence
    ).casefold()
    if len(functions) > 1 and "retract" in sequence_text and "releas" in sequence_text:
        related = sorted(
            {
                str(function_id)
                for step in sequence
                for function_id in step.get("function_ids", [])
                if function_id
            }
        )
        hints.append(
            {
                "id": "reverse_order_interlock",
                "applies_to": related or ["system"],
                "required_types": ["sequence_valve"],
                "connection_rules": [
                    "Gate clamp-release flow so it is unavailable until the working actuator has retracted.",
                    "Use a distinct reverse-sequence/pilot path when the forward sequence valve alone cannot enforce reverse order.",
                    "Canonical wiring: DCV.B feeds the working actuator's retract chamber directly and "
                    "also feeds a second sequence valve's P port. That sequence valve's A port feeds the "
                    "clamp's release chamber and the release pilot of the clamp load lock.",
                    "The reverse sequence valve opens on the pressure rise that occurs only once the "
                    "working actuator has reached its retracted end position and can accept no more flow.",
                    "Every sequence-valve drain goes to Tank.R; the integral reverse check carries the "
                    "forward-direction return.",
                ],
                "phase_rules": [
                    "Work retract active while clamp release remains blocked.",
                    "Clamp release enabled only after the work-retract precondition is satisfied.",
                    "In the clamp-release phase the working actuator is already at its end stop. Declare "
                    "it in completed_function_ids rather than adding a valve to isolate a line that "
                    "cannot move anything.",
                ],
                "prohibitions": [
                    "Do not assume reverse order is enforced merely because forward pressure sequencing is correct.",
                    "Do not use a position-operated valve to enforce reverse order unless the requirement "
                    "actually specifies a position trigger; the trigger here is pressure.",
                    "Do not add plain check valves to 'protect' a return path that the sequence valve's "
                    "own integral reverse check already carries.",
                    "Do not rely on the order in which two independently commanded solenoid valves are "
                    "energised: that is electrical sequencing, not a hydraulic interlock.",
                ],
            }
        )

    return hints


def format_pattern_hints(requirements: dict[str, Any]) -> str:
    return json.dumps(derive_pattern_hints(requirements), indent=2, ensure_ascii=False)
