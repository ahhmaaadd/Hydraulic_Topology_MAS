from __future__ import annotations

from copy import deepcopy

from hydraulic_mas.decision_flow import canonical_decision_payload
from hydraulic_mas.validation import validate_topology
from test_validation import requirements, valid_topology


def _codes(design: dict, req: dict | None = None) -> set[str]:
    report = validate_topology(design, req or requirements())
    return {item.code for item in report.issues}


def test_motion_decision_cannot_be_changed_after_requirements() -> None:
    design = deepcopy(valid_topology())
    decision = design["motion_control_decisions"][1]
    decision.update(
        {
            "metering_side": "meter_in",
            "metered_chamber": "cap",
            "metered_flow": "supply",
        }
    )
    assert "MOTION_DECISION_MUTATED" in _codes(design)


def test_wrong_flow_control_orientation_is_rejected() -> None:
    design = deepcopy(valid_topology())
    # Reverse the one-way control. Extension then follows its free-check path,
    # while retraction is throttled: the opposite of the typed decision.
    design["connections"][6]["to_port"] = "B"
    design["connections"][7]["from_port"] = "A"
    codes = _codes(design)
    assert "METERING_PATH_NOT_REALIZED" in codes


def test_open_bypass_cannot_defeat_metered_feed_phase() -> None:
    design = deepcopy(valid_topology())
    feed = next(item for item in design["phase_configurations"] if item["phase_id"] == "controlled_feed")
    bypass = next(item for item in feed["component_states"] if item["component_id"] == "PositionBypass")
    bypass["state"] = "open"
    codes = _codes(design)
    assert "METERING_BYPASSED_IN_PHASE" in codes
    assert "SEQUENCE_TRIGGER_STATE_CHANGE_NOT_PROVEN" in codes


def test_wrong_dcv_state_does_not_pass_via_another_possible_state() -> None:
    design = deepcopy(valid_topology())
    feed = next(item for item in design["phase_configurations"] if item["phase_id"] == "controlled_feed")
    dcv = next(item for item in feed["component_states"] if item["component_id"] == "DCV")
    dcv["state"] = "retract"
    codes = _codes(design)
    assert "PHASE_SUPPLY_PATH_MISSING" in codes
    assert "PHASE_EXHAUST_PATH_MISSING" in codes


def test_every_topology_catalog_gap_blocks_even_if_model_marks_nonblocking() -> None:
    design = deepcopy(valid_topology())
    design["catalog_gaps"] = [
        {
            "capability": "dynamic load control",
            "needed_component_class": "counterbalance valve",
            "reason": "The class is absent.",
            "chosen_workaround": "pilot-operated check",
            "scope": "topology",
            "blocking": False,
        }
    ]
    assert "BLOCKING_TOPOLOGY_CATALOG_GAP" in _codes(design)


def test_pilot_check_is_not_accepted_as_counterbalance_capability() -> None:
    req = deepcopy(requirements())
    req["derived_design_drivers"].append(
        {
            "capability": "counterbalance_overrunning",
            "evidence": "The load can drive the cylinder during lowering.",
            "related_function_ids": ["slide"],
        }
    )
    design = deepcopy(valid_topology())
    design["components"].append(
        {
            "id": "LoadLock",
            "catalog_key": "GENERIC_DUAL_PILOT_OPERATED_CHECK",
            "comp_type": "pilot_check_valve",
            "role": "Static load lock, deliberately not a counterbalance valve.",
            "function_id": "slide",
            "configuration": [],
            "selection_basis": "Used to verify capability distinction.",
        }
    )
    # Place the dual check between the DCV and both existing work branches.
    design["connections"][5] = {
        "from_component": "DCV",
        "from_port": "A",
        "to_component": "LoadLock",
        "to_port": "A1",
        "line": "work",
    }
    design["connections"].append(
        {
            "from_component": "LoadLock",
            "from_port": "A2",
            "to_component": "Cylinder",
            "to_port": "Cap",
            "line": "work",
        }
    )
    design["connections"][7] = {
        "from_component": "FeedControl",
        "from_port": "B",
        "to_component": "LoadLock",
        "to_port": "B2",
        "line": "work",
    }
    design["connections"][9] = {
        "from_component": "PositionBypass",
        "from_port": "A",
        "to_component": "LoadLock",
        "to_port": "B2",
        "line": "work",
    }
    design["connections"].append(
        {
            "from_component": "DCV",
            "from_port": "B",
            "to_component": "LoadLock",
            "to_port": "B1",
            "line": "work",
        }
    )
    assert "MISSING_DRIVER_CAPABILITY" in _codes(design, req)


def _rigid_parallel_fixture() -> tuple[dict, dict]:
    req = deepcopy(requirements())
    synchronization = {
        "required": True,
        "actuator_count": 2,
        "strategy": "rigid_platen_parallel",
        "series_displacement_compatibility": "not_applicable",
        "justification": "Two matched cylinders act on one explicit rigid platen.",
        "source": "explicit",
    }
    req["functions"][0]["synchronization"] = synchronization
    req["derived_design_drivers"].append(
        {
            "capability": "synchronization",
            "evidence": "The two cylinders share a rigid platen.",
            "related_function_ids": ["slide"],
        }
    )
    design = deepcopy(valid_topology())
    design["components"].append(
        {
            "id": "Cylinder2",
            "catalog_key": "GENERIC_DOUBLE_ACTING_CYLINDER",
            "comp_type": "cylinder",
            "role": "Second rigid-platen actuator",
            "function_id": "slide",
            "configuration": [],
            "selection_basis": "The explicit shared platen requires two parallel actuators.",
        }
    )
    design["connections"].extend(
        [
            {
                "from_component": "DCV",
                "from_port": "A",
                "to_component": "Cylinder2",
                "to_port": "Cap",
                "line": "work",
            },
            {
                "from_component": "Cylinder2",
                "from_port": "Rod",
                "to_component": "FeedControl",
                "to_port": "A",
                "line": "work",
            },
            {
                "from_component": "Cylinder2",
                "from_port": "Rod",
                "to_component": "PositionBypass",
                "to_port": "P",
                "line": "work",
            },
        ]
    )
    motion, sync = canonical_decision_payload(req)
    design["motion_control_decisions"] = motion
    design["synchronization_decisions"] = sync
    design["function_implementations"][0]["component_ids"].append("Cylinder2")
    return design, req


def test_rigid_platen_parallel_cylinders_pass() -> None:
    design, req = _rigid_parallel_fixture()
    report = validate_topology(design, req)
    assert report.verdict == "valid", [item.model_dump() for item in report.issues]


def test_rigid_platen_series_connection_is_rejected() -> None:
    design, req = _rigid_parallel_fixture()
    design["connections"] = [
        item
        for item in design["connections"]
        if not (
            (item["from_component"] == "Cylinder" and item["from_port"] == "Rod")
            or item["to_component"] == "Cylinder2"
            or item["from_component"] == "Cylinder2"
        )
    ]
    design["connections"].extend(
        [
            {
                "from_component": "Cylinder",
                "from_port": "Rod",
                "to_component": "Cylinder2",
                "to_port": "Cap",
                "line": "work",
            },
            {
                "from_component": "Cylinder2",
                "from_port": "Rod",
                "to_component": "FeedControl",
                "to_port": "A",
                "line": "work",
            },
            {
                "from_component": "Cylinder2",
                "from_port": "Rod",
                "to_component": "PositionBypass",
                "to_port": "P",
                "line": "work",
            },
        ]
    )
    codes = _codes(design, req)
    assert "UNAUTHORIZED_SERIES_ACTUATORS" in codes
    assert "RIGID_PLATEN_REQUIRES_PARALLEL_CYLINDERS" in codes
