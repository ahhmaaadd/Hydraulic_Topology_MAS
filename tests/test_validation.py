from __future__ import annotations

from copy import deepcopy

from hydraulic_mas.validation import validate_topology


def requirements() -> dict:
    return {
        "title": "Horizontal slide",
        "restated_problem": "Move a horizontal slide and limit system pressure.",
        "functions": [
            {
                "id": "slide",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "load_type": "resistive",
                "total_travel": {"value": 300, "unit": "mm"},
                "peak_force": {"value": 10, "unit": "kN"},
                "holding": {},
            }
        ],
        "global_constraints": {"max_system_pressure": {"value": 100, "unit": "bar"}},
        "operational_logic": {"sequence": [], "interlocks": []},
        "derived_design_drivers": [{"capability": "pressure_limiting_stall"}],
    }


def valid_topology() -> dict:
    components = [
        ("T1", "ENGINEERED_Industrial_Reservoir_30L", "tank", "reservoir", None),
        ("SS1", "ENGINEERED_SuctionScreen", "suction_strainer", "inlet protection", None),
        ("P1", "Danfoss_GearMe_GR1_3p2cc_1450", "pump", "supply", None),
        ("RV1", "REXROTH_DBD6_Relief_50L_350bar", "relief_valve", "overpressure protection", None),
        ("DV1", "REXROTH_4WE6_4_3_Tandem_80L", "dcv", "directional control", "slide"),
        ("CY1", "Parker_HMI_50_28x300_P100", "cylinder", "slide actuator", "slide"),
        ("FB1", "ENGINEERED_FillerBreather", "breather", "reservoir breather", None),
    ]
    return {
        "design_narrative": "Simple open-circuit slide topology.",
        "components": [
            {
                "id": component_id,
                "catalog_key": key,
                "comp_type": comp_type,
                "role": role,
                "function_id": function_id,
                "configuration": [],
                "selection_basis": "Test fixture catalog selection.",
                "requires_sizing_verification": True,
            }
            for component_id, key, comp_type, role, function_id in components
        ],
        "connections": [
            {"from_component": "T1", "from_port": "S", "to_component": "SS1", "to_port": "IN", "line": "suction"},
            {"from_component": "SS1", "from_port": "OUT", "to_component": "P1", "to_port": "S", "line": "suction"},
            {"from_component": "P1", "from_port": "P", "to_component": "DV1", "to_port": "P", "line": "pressure"},
            {"from_component": "P1", "from_port": "P", "to_component": "RV1", "to_port": "P", "line": "pressure"},
            {"from_component": "RV1", "from_port": "T", "to_component": "T1", "to_port": "R", "line": "return"},
            {"from_component": "DV1", "from_port": "T", "to_component": "T1", "to_port": "R", "line": "return"},
            {"from_component": "P1", "from_port": "L", "to_component": "T1", "to_port": "D", "line": "drain"},
            {"from_component": "DV1", "from_port": "A", "to_component": "CY1", "to_port": "A", "line": "work"},
            {"from_component": "DV1", "from_port": "B", "to_component": "CY1", "to_port": "B", "line": "work"},
            {"from_component": "T1", "from_port": "B", "to_component": "FB1", "to_port": "T", "line": "vent"},
        ],
        "external_interfaces": [
            {"component_id": "FB1", "port": "AIR", "external_system": "atmosphere", "domain": "atmosphere"}
        ],
        "port_terminations": [],
        "function_implementations": [
            {
                "function_id": "slide",
                "component_ids": ["DV1", "CY1"],
                "how_requirements_met": "DV1 commands both CY1 chambers.",
            }
        ],
        "design_decisions": [],
        "catalog_gaps": [],
        "assumptions": [],
        "open_issues": [],
    }


def test_valid_catalog_netlist_passes() -> None:
    report = validate_topology(valid_topology(), requirements())
    assert report.verdict == "valid", [issue.model_dump() for issue in report.issues]
    assert all(check.passed for check in report.checks)


def test_invalid_port_and_missing_relief_are_rejected() -> None:
    design = deepcopy(valid_topology())
    design["connections"][7]["from_port"] = "C"
    design["components"] = [item for item in design["components"] if item["id"] != "RV1"]
    design["connections"] = [
        item
        for item in design["connections"]
        if item["from_component"] != "RV1" and item["to_component"] != "RV1"
    ]
    report = validate_topology(design, requirements())
    codes = {issue.code for issue in report.issues}
    assert report.verdict == "invalid"
    assert "INVALID_CATALOG_PORT" in codes
    assert "MISSING_POWER_UNIT_COMPONENT" in codes


