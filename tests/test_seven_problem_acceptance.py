"""Canonical acceptance circuits for the seven requirement families.

P7-01 is covered by ``test_validation.valid_topology``.  These fixtures cover
P7-02 through P7-07 and live only in tests; runtime agents receive generic
pattern rules, never a problem-id-to-solution map.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydraulic_mas.decision_flow import canonical_decision_payload
from hydraulic_mas.schemas import RequirementsSpec
from hydraulic_mas.validation import validate_topology


def _phase(
    phase_id: str,
    motion: str,
    *,
    realization: str = "sizing_only",
    side: str = "none",
    chamber: str = "none",
    flow: str = "none",
    compensation: str = "none",
    load_control: str = "none",
) -> dict[str, Any]:
    return {
        "id": phase_id,
        "name": phase_id.replace("_", " ").title(),
        "motion": motion,
        "load_type": "resistive",
        "speed_adjustable": realization in {"adjustable_throttled", "load_independent_throttled"},
        "speed_load_independent": realization == "load_independent_throttled",
        "speed_realization": realization,
        "metering_side": side,
        "metered_chamber": chamber,
        "metered_flow": flow,
        "flow_compensation": compensation,
        "load_control": load_control,
        "motion_control_justification": f"Typed {realization} regression decision.",
        "motion_control_source": "inferred",
    }


def _function(
    function_id: str,
    phases: list[dict[str, Any]],
    *,
    holding: dict[str, Any] | None = None,
    synchronization: dict[str, Any] | None = None,
    max_working_pressure: dict[str, Any] | None = None,
    branch_limit: bool = False,
) -> dict[str, Any]:
    return {
        "id": function_id,
        "name": function_id.replace("_", " ").title(),
        "description": f"Canonical {function_id} function.",
        "physical_actuator_id": f"{function_id}_cylinder",
        "actuator_type": "linear",
        "orientation": "horizontal",
        "load_type": "resistive",
        "motion_phases": phases,
        "holding": holding or {},
        "synchronization": synchronization
        or {
            "required": False,
            "actuator_count": 1,
            "strategy": "none",
            "series_displacement_compatibility": "not_applicable",
            "justification": "One actuator.",
            "source": "explicit",
        },
        "max_working_pressure": max_working_pressure,
        "branch_pressure_limit_required": branch_limit,
    }


def _requirements(
    title: str,
    functions: list[dict[str, Any]],
    sequence: list[dict[str, Any]],
    *,
    pressure: float,
    drivers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = RequirementsSpec.model_validate(
        {
            "title": title,
            "restated_problem": title,
            "functions": functions,
            "global_constraints": {"max_system_pressure": {"value": pressure, "unit": "bar"}},
            "operational_logic": {"sequence": sequence},
            "derived_design_drivers": drivers or [],
        }
    )
    return value.model_dump(mode="json")


def _component(
    component_id: str,
    key: str,
    comp_type: str,
    function_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "catalog_key": key,
        "comp_type": comp_type,
        "role": f"Canonical {comp_type} function.",
        "function_id": function_id,
        "configuration": [],
        "selection_basis": "Required by the canonical acceptance circuit.",
    }


def _connection(
    source: str,
    source_port: str,
    target: str,
    target_port: str,
    line: str,
) -> dict[str, Any]:
    return {
        "from_component": source,
        "from_port": source_port,
        "to_component": target,
        "to_port": target_port,
        "line": line,
    }


def _base_topology(
    requirements: dict[str, Any],
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    configurations: list[dict[str, Any]],
) -> dict[str, Any]:
    motion, synchronization = canonical_decision_payload(requirements)
    function_implementations = []
    for function in requirements["functions"]:
        function_id = function["id"]
        function_implementations.append(
            {
                "function_id": function_id,
                "component_ids": [
                    item["id"]
                    for item in components
                    if item.get("function_id") in {None, function_id}
                    and item["comp_type"] not in {"tank", "pump", "relief_valve"}
                ],
                "circuit_pattern": "canonical acceptance pattern",
                "how_requirements_met": "Directed phase paths and component states are validated.",
            }
        )
    return {
        "design_narrative": "Canonical topology-only acceptance circuit.",
        "components": components,
        "connections": connections,
        "motion_control_decisions": motion,
        "synchronization_decisions": synchronization,
        "phase_configurations": configurations,
        "function_implementations": function_implementations,
    }


def _power_components() -> list[dict[str, Any]]:
    return [
        _component("Tank", "GENERIC_TANK", "tank"),
        _component("Pump", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump"),
        _component("ReliefValve", "GENERIC_RELIEF_VALVE", "relief_valve"),
    ]


def _power_connections() -> list[dict[str, Any]]:
    return [
        _connection("Tank", "S", "Pump", "S", "suction"),
        _connection("Pump", "P", "ReliefValve", "P", "pressure"),
        _connection("ReliefValve", "T", "Tank", "R", "return"),
    ]


def _config(
    phase_id: str,
    function_id: str,
    motion: str,
    states: list[tuple[str, str]],
    *,
    active: list[str] | None = None,
    forbidden: list[str] | None = None,
    completed: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "function_id": function_id,
        "motion": motion,
        "component_states": [
            {"component_id": component_id, "state": state}
            for component_id, state in states
        ],
        "expected_active_function_ids": active or [function_id],
        "forbidden_active_function_ids": forbidden or [],
        "completed_function_ids": completed or [],
    }


def _p2_vertical_press() -> tuple[dict[str, Any], dict[str, Any]]:
    phases = [
        _phase("rapid_down", "extend", realization="unrestricted_rapid"),
        _phase(
            "feed_down",
            "extend",
            realization="load_sensitive_throttled",
            side="meter_out",
            chamber="rod",
            flow="exhaust",
            compensation="non_compensated",
        ),
        _phase(
            "press_down",
            "extend",
            realization="load_sensitive_throttled",
            side="meter_out",
            chamber="rod",
            flow="exhaust",
            compensation="non_compensated",
        ),
        _phase("return_up", "retract"),
    ]
    requirements = _requirements(
        "P7-02 vertical press",
        [_function("ram", phases, holding={"must_hold_position": True, "hold_on_power_loss": True})],
        [
            {"order": 1, "phase_id": "rapid_down", "description": "Rapid down", "function_ids": ["ram"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "feed_down", "predecessor_phase_id": "rapid_down", "description": "Position change to feed", "function_ids": ["ram"], "trigger": "position", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "press_down", "predecessor_phase_id": "feed_down", "description": "Continue at feed speed", "function_ids": ["ram"], "trigger": "completion_of_previous"},
            {"order": 4, "phase_id": "return_up", "predecessor_phase_id": "press_down", "description": "Return on command", "function_ids": ["ram"], "trigger": "external_command"},
        ],
        pressure=100,
        drivers=[
            {"capability": "load_holding", "evidence": "Upper-position power-off hold.", "related_function_ids": ["ram"]},
            {"capability": "two_speed_force_switching", "evidence": "Rapid-to-feed change.", "related_function_ids": ["ram"]},
            {"capability": "pressure_limiting_stall", "evidence": "System pressure ceiling."},
        ],
    )
    components = [
        *_power_components(),
        _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "ram"),
        _component("FeedControl", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "ram"),
        _component("PositionBypass", "GENERIC_POSITION_OPERATED_BYPASS", "position_valve", "ram"),
        _component("RodPilotCheck", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "ram"),
        _component("RamCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "ram"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "RamCylinder", "Cap", "work"),
        _connection("DCV", "A", "RodPilotCheck", "X", "pilot"),
        _connection("RamCylinder", "Rod", "RodPilotCheck", "C", "work"),
        _connection("RodPilotCheck", "V", "FeedControl", "A", "work"),
        _connection("FeedControl", "B", "DCV", "B", "work"),
        _connection("RodPilotCheck", "V", "PositionBypass", "P", "work"),
        _connection("PositionBypass", "A", "DCV", "B", "work"),
    ]
    configurations = [
        _config("rapid_down", "ram", "extend", [("DCV", "extend"), ("PositionBypass", "open")]),
        _config("feed_down", "ram", "extend", [("DCV", "extend"), ("PositionBypass", "closed")]),
        _config("press_down", "ram", "extend", [("DCV", "extend"), ("PositionBypass", "closed")]),
        _config("return_up", "ram", "retract", [("DCV", "retract"), ("PositionBypass", "open")]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def _p3_positioning_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = _requirements(
        "P7-03 positioning fixture",
        [
            _function(
                "carriage",
                [_phase("advance", "extend"), _phase("return", "retract"), _phase("hold", "hold")],
                holding={"must_hold_position": True, "no_creep": True},
            )
        ],
        [
            {"order": 1, "phase_id": "advance", "description": "Advance on command", "function_ids": ["carriage"], "trigger": "external_command"},
            {"order": 2, "phase_id": "hold", "description": "Hold when command is released", "function_ids": ["carriage"], "trigger": "external_command"},
            {"order": 3, "phase_id": "return", "description": "Return on command", "function_ids": ["carriage"], "trigger": "external_command"},
        ],
        pressure=100,
        drivers=[{"capability": "load_holding", "evidence": "Ten-minute no-drift hold.", "related_function_ids": ["carriage"]}],
    )
    components = [
        *_power_components(),
        # Float centre: neutral blocks P and vents A and B to tank so the dual
        # load lock can reseat when the command is released.  A tandem or closed
        # centre traps the cross-pilot pressure and cannot prove no-drift hold.
        _component("DCV", "GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV", "dcv", "carriage"),
        _component("DualPilotCheck", "GENERIC_DUAL_PILOT_OPERATED_CHECK", "pilot_check_valve", "carriage"),
        _component("CarriageCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "carriage"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "DualPilotCheck", "A1", "work"),
        _connection("DualPilotCheck", "A2", "CarriageCylinder", "Cap", "work"),
        _connection("DCV", "B", "DualPilotCheck", "B1", "work"),
        _connection("DualPilotCheck", "B2", "CarriageCylinder", "Rod", "work"),
    ]
    configurations = [
        _config("advance", "carriage", "extend", [("DCV", "extend")]),
        _config("return", "carriage", "retract", [("DCV", "retract")]),
        _config("hold", "carriage", "hold", [("DCV", "neutral")], active=[]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def _p4_load_sensitive_slide() -> tuple[dict[str, Any], dict[str, Any]]:
    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    requirements = _requirements(
        "P7-04 load-sensitive slide",
        [_function("slide", [_phase("approach", "extend", **metered), _phase("working", "extend", **metered), _phase("return", "retract")])],
        [
            {"order": 1, "phase_id": "approach", "description": "Approach", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "working", "predecessor_phase_id": "approach", "description": "Load rise changes speed", "function_ids": ["slide"], "trigger": "mechanical_coupling", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "return", "description": "Return", "function_ids": ["slide"], "trigger": "external_command"},
        ],
        pressure=20,
    )
    components = [
        *_power_components(),
        _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
        _component("LoadSensitiveControl", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "slide"),
        _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "SlideCylinder", "Cap", "work"),
        _connection("SlideCylinder", "Rod", "LoadSensitiveControl", "A", "work"),
        _connection("LoadSensitiveControl", "B", "DCV", "B", "work"),
    ]
    configurations = [
        _config("approach", "slide", "extend", [("DCV", "extend")]),
        _config("working", "slide", "extend", [("DCV", "extend")]),
        _config("return", "slide", "retract", [("DCV", "retract")]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def _p5_clamp_then_drill() -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = _requirements(
        "P7-05 clamp then drill",
        [
            _function("clamp", [_phase("clamp_advance", "extend"), _phase("clamp_release", "retract")], holding={"must_hold_position": True}),
            _function("drill", [_phase("drill_feed", "extend"), _phase("drill_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp first", "function_ids": ["clamp"], "trigger": "initial_command", "forbidden_overlap_function_ids": ["drill"]},
            {"order": 2, "phase_id": "drill_feed", "predecessor_phase_id": "clamp_advance", "description": "Drill after clamp pressure", "function_ids": ["drill"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "drill_retract", "predecessor_phase_id": "drill_feed", "description": "Drill retracts first", "function_ids": ["drill"], "trigger": "external_command", "forbidden_overlap_function_ids": ["clamp"]},
            {"order": 4, "phase_id": "clamp_release", "predecessor_phase_id": "drill_retract", "description": "Clamp releases after drill retract pressure", "function_ids": ["clamp"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
        drivers=[{"capability": "load_holding", "evidence": "Clamp stays set during drilling.", "related_function_ids": ["clamp"]}],
    )
    components = [
        *_power_components(),
        _component("CycleDCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv"),
        _component("ClampPilotCheck", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp"),
        _component("ForwardSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "drill"),
        _component("ReturnSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "clamp"),
        _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp"),
        _component("DrillCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "drill"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "CycleDCV", "P", "pressure"),
        _connection("CycleDCV", "T", "Tank", "R", "return"),
        _connection("CycleDCV", "A", "ClampPilotCheck", "V", "work"),
        _connection("ClampPilotCheck", "C", "ClampCylinder", "Cap", "work"),
        _connection("ClampCylinder", "Rod", "ReturnSequence", "A", "work"),
        _connection("ReturnSequence", "P", "CycleDCV", "B", "work"),
        _connection("ReturnSequence", "A", "ClampPilotCheck", "X", "pilot"),
        _connection("ReturnSequence", "T", "Tank", "R", "drain"),
        _connection("CycleDCV", "A", "ForwardSequence", "P", "work"),
        _connection("ForwardSequence", "A", "DrillCylinder", "Cap", "work"),
        _connection("ForwardSequence", "T", "Tank", "R", "drain"),
        _connection("DrillCylinder", "Rod", "CycleDCV", "B", "work"),
    ]
    states = {
        "clamp_advance": [("CycleDCV", "extend"), ("ForwardSequence", "closed"), ("ReturnSequence", "closed")],
        "drill_feed": [("CycleDCV", "extend"), ("ForwardSequence", "open"), ("ReturnSequence", "closed")],
        "drill_retract": [("CycleDCV", "retract"), ("ForwardSequence", "open"), ("ReturnSequence", "closed")],
        "clamp_release": [("CycleDCV", "retract"), ("ForwardSequence", "closed"), ("ReturnSequence", "open")],
    }
    configurations = [
        _config("clamp_advance", "clamp", "extend", states["clamp_advance"], forbidden=["drill"]),
        _config("drill_feed", "drill", "extend", states["drill_feed"]),
        _config("drill_retract", "drill", "retract", states["drill_retract"], forbidden=["clamp"]),
        _config("clamp_release", "clamp", "retract", states["clamp_release"]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def _p6_rigid_platen() -> tuple[dict[str, Any], dict[str, Any]]:
    synchronization = {
        "required": True,
        "actuator_count": 2,
        "strategy": "rigid_platen_parallel",
        "series_displacement_compatibility": "not_applicable",
        "justification": "The explicitly rigid common platen synchronizes the parallel cylinders.",
        "source": "explicit",
    }
    requirements = _requirements(
        "P7-06 rigid platen",
        [_function("platen", [_phase("advance", "extend"), _phase("return", "retract")], synchronization=synchronization)],
        [
            {"order": 1, "phase_id": "advance", "description": "Advance", "function_ids": ["platen"], "trigger": "external_command"},
            {"order": 2, "phase_id": "return", "description": "Return", "function_ids": ["platen"], "trigger": "external_command"},
        ],
        pressure=80,
        drivers=[{"capability": "synchronization", "evidence": "Rigid shared platen.", "related_function_ids": ["platen"]}],
    )
    components = [
        *_power_components(),
        _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "platen"),
        _component("LeftCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "platen"),
        _component("RightCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "platen"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "LeftCylinder", "Cap", "work"),
        _connection("DCV", "A", "RightCylinder", "Cap", "work"),
        _connection("DCV", "B", "LeftCylinder", "Rod", "work"),
        _connection("DCV", "B", "RightCylinder", "Rod", "work"),
    ]
    configurations = [
        _config("advance", "platen", "extend", [("DCV", "extend")]),
        _config("return", "platen", "retract", [("DCV", "retract")]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def _p7_clamp_then_work() -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = _requirements(
        "P7-07 reduced-pressure clamp then work",
        [
            _function(
                "clamp",
                [_phase("clamp_advance", "extend"), _phase("clamp_release", "retract")],
                max_working_pressure={"value": 40, "unit": "bar"},
                branch_limit=True,
            ),
            _function("work", [_phase("work_advance", "extend"), _phase("work_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp first", "function_ids": ["clamp"], "trigger": "initial_command", "forbidden_overlap_function_ids": ["work"]},
            {"order": 2, "phase_id": "work_advance", "predecessor_phase_id": "clamp_advance", "description": "Work advances after clamp pressure", "function_ids": ["work"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "work_retract", "predecessor_phase_id": "work_advance", "description": "Work retracts before clamp release", "function_ids": ["work"], "trigger": "external_command", "forbidden_overlap_function_ids": ["clamp"]},
            {"order": 4, "phase_id": "clamp_release", "predecessor_phase_id": "work_retract", "description": "Clamp releases after work retract", "function_ids": ["clamp"], "trigger": "completion_of_previous"},
        ],
        pressure=70,
        drivers=[{"capability": "branch_pressure_reduction", "evidence": "Clamp branch must not exceed 40 bar.", "related_function_ids": ["clamp"]}],
    )
    components = [
        *_power_components(),
        _component("ClampReducer", "GENERIC_PRESSURE_REDUCING_VALVE_WITH_REVERSE_CHECK", "pressure_reducing_valve", "clamp"),
        _component("ClampDCV", "GENERIC_4_3_SOLENOID_CLOSED_CENTER_DCV", "dcv", "clamp"),
        _component("WorkSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "work"),
        _component("WorkDCV", "GENERIC_4_3_SOLENOID_CLOSED_CENTER_DCV", "dcv", "work"),
        _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp"),
        _component("WorkCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "work"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "ClampReducer", "P", "pressure"),
        _connection("ClampReducer", "T", "Tank", "R", "drain"),
        _connection("ClampReducer", "A", "ClampDCV", "P", "pressure"),
        _connection("ClampDCV", "T", "Tank", "R", "return"),
        _connection("ClampDCV", "A", "ClampCylinder", "Cap", "work"),
        _connection("ClampDCV", "B", "ClampCylinder", "Rod", "work"),
        _connection("Pump", "P", "WorkSequence", "P", "pressure"),
        _connection("WorkSequence", "T", "Tank", "R", "drain"),
        _connection("WorkSequence", "A", "WorkDCV", "P", "pressure"),
        _connection("WorkDCV", "T", "Tank", "R", "return"),
        _connection("WorkDCV", "A", "WorkCylinder", "Cap", "work"),
        _connection("WorkDCV", "B", "WorkCylinder", "Rod", "work"),
    ]
    configurations = [
        _config("clamp_advance", "clamp", "extend", [("ClampDCV", "extend"), ("WorkDCV", "extend"), ("WorkSequence", "closed")], forbidden=["work"]),
        _config("work_advance", "work", "extend", [("ClampDCV", "extend"), ("WorkDCV", "extend"), ("WorkSequence", "open")]),
        _config("work_retract", "work", "retract", [("ClampDCV", "neutral"), ("WorkDCV", "retract"), ("WorkSequence", "open")], forbidden=["clamp"]),
        _config("clamp_release", "clamp", "retract", [("ClampDCV", "retract"), ("WorkDCV", "neutral"), ("WorkSequence", "closed")]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


@pytest.mark.parametrize(
    ("case_id", "factory"),
    [
        ("P7-02", _p2_vertical_press),
        ("P7-03", _p3_positioning_fixture),
        ("P7-04", _p4_load_sensitive_slide),
        ("P7-05", _p5_clamp_then_drill),
        ("P7-06", _p6_rigid_platen),
        ("P7-07", _p7_clamp_then_work),
    ],
)
def test_canonical_problem_family_topology_is_accepted(case_id: str, factory) -> None:
    requirements, topology = factory()
    report = validate_topology(topology, requirements)
    assert report.verdict == "valid", {
        "case": case_id,
        "issues": [item.model_dump(mode="json") for item in report.issues],
    }

