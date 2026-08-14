from __future__ import annotations

from copy import deepcopy

from hydraulic_mas.decision_flow import canonical_decision_payload
from hydraulic_mas.validation import validate_topology


def requirements() -> dict:
    return {
        "title": "Two-stage horizontal slide",
        "restated_problem": "Move a horizontal slide, change feed by position, and limit system pressure.",
        "functions": [
            {
                "id": "slide",
                "name": "Two-stage slide",
                "description": "Extend rapidly, change to controlled feed by position, then retract.",
                "physical_actuator_id": "slide_cylinder",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "load_type": "resistive",
                "motion_phases": [
                    {
                        "id": "rapid_approach",
                        "name": "Rapid approach",
                        "motion": "extend",
                        "load_type": "resistive",
                        "speed_adjustable": False,
                        "speed_load_independent": False,
                        "metering_side": "none",
                        "metered_chamber": "none",
                        "metered_flow": "none",
                        "flow_compensation": "none",
                        "load_control": "none",
                        "motion_control_justification": "The open position bypass provides unrestricted rapid approach.",
                        "motion_control_source": "inferred",
                    },
                    {
                        "id": "controlled_feed",
                        "name": "Controlled feed",
                        "motion": "extend",
                        "load_type": "resistive",
                        "speed_adjustable": True,
                        "speed_load_independent": True,
                        "metering_side": "meter_out",
                        "metered_chamber": "rod",
                        "metered_flow": "exhaust",
                        "flow_compensation": "pressure_compensated",
                        "load_control": "none",
                        "motion_control_justification": "Rod exhaust is pressure-compensated during the feed phase.",
                        "motion_control_source": "inferred",
                    },
                    {
                        "id": "return_stroke",
                        "name": "Return",
                        "motion": "retract",
                        "load_type": "resistive",
                        "speed_adjustable": False,
                        "speed_load_independent": False,
                        "metering_side": "none",
                        "metered_chamber": "none",
                        "metered_flow": "none",
                        "flow_compensation": "none",
                        "load_control": "none",
                        "motion_control_justification": "The integral reverse check and open bypass permit free return.",
                        "motion_control_source": "inferred",
                    },
                ],
                "total_travel": {"value": 350, "unit": "mm"},
                "peak_force": {"value": 35, "unit": "kN"},
                "holding": {},
                "synchronization": {
                    "required": False,
                    "actuator_count": 1,
                    "strategy": "none",
                    "series_displacement_compatibility": "not_applicable",
                    "justification": "One actuator is used.",
                    "source": "explicit",
                },
            }
        ],
        "global_constraints": {"max_system_pressure": {"value": 70, "unit": "bar"}},
        "operational_logic": {
            "sequence": [
                {
                    "order": 1,
                    "phase_id": "rapid_approach",
                    "description": "Begin rapid extension.",
                    "function_ids": ["slide"],
                    "trigger": "initial_command",
                },
                {
                    "order": 2,
                    "phase_id": "controlled_feed",
                    "predecessor_phase_id": "rapid_approach",
                    "description": "Trip the position valve and enter controlled feed.",
                    "function_ids": ["slide"],
                    "trigger": "position",
                    "hydraulically_enforced": True,
                },
                {
                    "order": 3,
                    "phase_id": "return_stroke",
                    "predecessor_phase_id": "controlled_feed",
                    "description": "Retract on command.",
                    "function_ids": ["slide"],
                    "trigger": "external_command",
                },
            ],
            "interlocks": [],
        },
        "derived_design_drivers": [
            {"capability": "pressure_limiting_stall", "evidence": "The system pressure is limited."},
            {
                "capability": "pressure_compensation_load_independence",
                "evidence": "Feed speed must remain stable as load varies.",
                "related_function_ids": ["slide"],
            },
            {
                "capability": "two_speed_force_switching",
                "evidence": "The slide changes from rapid approach to feed by position.",
                "related_function_ids": ["slide"],
            },
        ],
    }


def valid_topology() -> dict:
    req = requirements()
    motion_decisions, synchronization_decisions = canonical_decision_payload(req)
    components = [
        ("Tank", "GENERIC_TANK", "tank", "reservoir boundary", None),
        ("Pump", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump", "hydraulic supply", None),
        ("ReliefValve", "GENERIC_RELIEF_VALVE", "relief_valve", "overpressure protection", None),
        ("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "bidirectional control", "slide"),
        (
            "FeedControl",
            "GENERIC_PRESSURE_COMPENSATED_ONE_WAY_FLOW_CONTROL",
            "pressure_comp_flow_control",
            "load-independent cutting-feed control",
            "slide",
        ),
        (
            "PositionBypass",
            "GENERIC_POSITION_OPERATED_BYPASS",
            "position_valve",
            "rapid-to-feed changeover",
            "slide",
        ),
        ("Cylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide actuator", "slide"),
    ]
    return {
        "design_narrative": "A direct, topology-only two-stage slide circuit with shared-port branches.",
        "components": [
            {
                "id": component_id,
                "catalog_key": key,
                "comp_type": comp_type,
                "role": role,
                "function_id": function_id,
                "configuration": [],
                "selection_basis": "Required functional topology class.",
            }
            for component_id, key, comp_type, role, function_id in components
        ],
        "connections": [
            {"from_component": "Tank", "from_port": "S", "to_component": "Pump", "to_port": "S", "line": "suction"},
            {"from_component": "Pump", "from_port": "P", "to_component": "ReliefValve", "to_port": "P", "line": "pressure"},
            {"from_component": "ReliefValve", "from_port": "T", "to_component": "Tank", "to_port": "R", "line": "return"},
            {"from_component": "Pump", "from_port": "P", "to_component": "DCV", "to_port": "P", "line": "pressure"},
            {"from_component": "DCV", "from_port": "T", "to_component": "Tank", "to_port": "R", "line": "return"},
            {"from_component": "DCV", "from_port": "A", "to_component": "Cylinder", "to_port": "Cap", "line": "work"},
            {"from_component": "Cylinder", "from_port": "Rod", "to_component": "FeedControl", "to_port": "A", "line": "work"},
            {"from_component": "FeedControl", "from_port": "B", "to_component": "DCV", "to_port": "B", "line": "work"},
            {"from_component": "Cylinder", "from_port": "Rod", "to_component": "PositionBypass", "to_port": "P", "line": "work"},
            {"from_component": "PositionBypass", "from_port": "A", "to_component": "DCV", "to_port": "B", "line": "work"},
        ],
        "motion_control_decisions": motion_decisions,
        "synchronization_decisions": synchronization_decisions,
        "phase_configurations": [
            {
                "phase_id": "rapid_approach",
                "function_id": "slide",
                "motion": "extend",
                "component_states": [
                    {"component_id": "DCV", "state": "extend"},
                    {"component_id": "PositionBypass", "state": "open"},
                ],
                "expected_active_function_ids": ["slide"],
                "forbidden_active_function_ids": [],
            },
            {
                "phase_id": "controlled_feed",
                "function_id": "slide",
                "motion": "extend",
                "component_states": [
                    {"component_id": "DCV", "state": "extend"},
                    {"component_id": "PositionBypass", "state": "closed"},
                ],
                "expected_active_function_ids": ["slide"],
                "forbidden_active_function_ids": [],
            },
            {
                "phase_id": "return_stroke",
                "function_id": "slide",
                "motion": "retract",
                "component_states": [
                    {"component_id": "DCV", "state": "retract"},
                    {"component_id": "PositionBypass", "state": "open"},
                ],
                "expected_active_function_ids": ["slide"],
                "forbidden_active_function_ids": [],
            },
        ],
        "external_interfaces": [],
        "port_terminations": [],
        "function_implementations": [
            {
                "function_id": "slide",
                "component_ids": ["DCV", "FeedControl", "PositionBypass", "Cylinder"],
                "circuit_pattern": "position-operated rapid bypass around compensated feed control",
                "how_requirements_met": "The open bypass gives rapid approach; its position trip leaves the compensated feed path.",
            }
        ],
        "design_decisions": [],
        "catalog_gaps": [],
        "assumptions": [],
        "open_issues": [],
    }


def test_valid_generic_problem_one_topology_passes() -> None:
    report = validate_topology(valid_topology(), requirements())
    assert report.verdict == "valid", [issue.model_dump() for issue in report.issues]
    assert all(check.passed for check in report.checks)


def test_problem_one_uses_direct_shared_port_connections() -> None:
    edges = {
        (
            item["from_component"],
            item["from_port"],
            item["to_component"],
            item["to_port"],
        )
        for item in valid_topology()["connections"]
    }
    assert ("Pump", "P", "ReliefValve", "P") in edges
    assert ("Pump", "P", "DCV", "P") in edges
    assert ("DCV", "A", "Cylinder", "Cap") in edges
    assert ("Cylinder", "Rod", "FeedControl", "A") in edges
    assert ("Cylinder", "Rod", "PositionBypass", "P") in edges
    assert not any(component["comp_type"] in {"manifold", "tee"} for component in valid_topology()["components"])


def test_invalid_port_and_missing_relief_are_rejected() -> None:
    design = deepcopy(valid_topology())
    design["connections"][5]["from_port"] = "C"
    design["components"] = [item for item in design["components"] if item["id"] != "ReliefValve"]
    design["connections"] = [
        item
        for item in design["connections"]
        if item["from_component"] != "ReliefValve" and item["to_component"] != "ReliefValve"
    ]
    report = validate_topology(design, requirements())
    codes = {issue.code for issue in report.issues}
    assert report.verdict == "invalid"
    assert "INVALID_CATALOG_PORT" in codes
    assert "MISSING_POWER_UNIT_COMPONENT" in codes


def test_accessory_or_sizing_component_is_rejected() -> None:
    design = deepcopy(valid_topology())
    design["components"].append(
        {
            "id": "Cooler",
            "catalog_key": "NOT_A_GENERIC_CLASS",
            "comp_type": "cooler",
            "role": "out-of-scope accessory",
            "configuration": [],
            "selection_basis": "Should never be selected in topology phase.",
        }
    )
    report = validate_topology(design, requirements())
    codes = {issue.code for issue in report.issues}
    assert "FORBIDDEN_TOPOLOGY_COMPONENT" in codes


def test_sizing_values_do_not_affect_topology_verdict() -> None:
    req = requirements()
    req["functions"][0]["total_travel"]["value"] = 100000
    req["functions"][0]["peak_force"]["value"] = 100000
    req["global_constraints"]["max_system_pressure"]["value"] = 1
    report = validate_topology(valid_topology(), req)
    assert report.verdict == "valid", [issue.model_dump() for issue in report.issues]
