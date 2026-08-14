from __future__ import annotations

from copy import deepcopy

from hydraulic_mas.validation import validate_topology


def requirements() -> dict:
    return {
        "title": "Two-stage horizontal slide",
        "restated_problem": "Move a horizontal slide, change feed by position, and limit system pressure.",
        "functions": [
            {
                "id": "slide",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "load_type": "resistive",
                "total_travel": {"value": 350, "unit": "mm"},
                "peak_force": {"value": 35, "unit": "kN"},
                "holding": {},
            }
        ],
        "global_constraints": {"max_system_pressure": {"value": 70, "unit": "bar"}},
        "operational_logic": {"sequence": [], "interlocks": []},
        "derived_design_drivers": [
            {"capability": "pressure_limiting_stall"},
            {"capability": "pressure_compensation_load_independence"},
            {"capability": "two_speed_force_switching"},
        ],
    }


def valid_topology() -> dict:
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
