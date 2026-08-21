"""Regression tests for the four v0.3.0 live-run failures.

Every test in this file corresponds to an observed ``unresolved`` run in the
supplied LangSmith traces.  Each one pins both halves of the fix: the defect is
still rejected, and the correct circuit is now accepted.

Trace evidence:

* P7-03 - stalled on a pilot-check reseat problem the 21-class catalog could not
  express, because no directional-valve neutral vented the work ports.
* P7-04 - ``METERING_BYPASSED_IN_PHASE`` plus two ``BLOCKING_EVIDENCE_GAP``
  errors caused by a hi-lo two-pump plan with a bypass check around the throttle.
* P7-05 - ``FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE`` raised against an actuator that
  was already sitting at its retracted end stop.
* P7-07 - ``UNJUSTIFIED_POSITION_VALVE`` from an invented position-operated
  isolator used to enforce a pressure-triggered release order.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydraulic_mas.candidate_selection import _candidate_evaluation
from hydraulic_mas.data.component_catalog_problems_7 import CATALOG
from hydraulic_mas.gap_policy import classify_evidence_gap
from hydraulic_mas.graph import _ESCALATED_SCOPE, _topology_error_signature
from hydraulic_mas.patterns import derive_pattern_hints
from hydraulic_mas.requirements_quality import normalize_requirements
from hydraulic_mas.schemas import ComponentPlan, FlowCompensation, SpeedRealization
from hydraulic_mas.validation import validate_topology

from tests.test_seven_problem_acceptance import (
    _base_topology,
    _component,
    _config,
    _connection,
    _function,
    _phase,
    _power_components,
    _power_connections,
    _requirements,
)


def _codes(report) -> set[str]:
    return {item.code for item in report.issues if item.severity == "error"}


# ---------------------------------------------------------------------------
# P7-03: a pilot-operated load lock needs a neutral that vents its pilot
# ---------------------------------------------------------------------------


def _positioning_fixture(dcv_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = _requirements(
        "P7-03 positioning fixture with a ten-minute hold",
        [
            _function(
                "carriage",
                [
                    _phase("advance", "extend"),
                    _phase("hold", "hold"),
                    _phase("return", "retract"),
                ],
                holding={"must_hold_position": True, "no_creep": True},
            )
        ],
        [
            {"order": 1, "phase_id": "advance", "description": "Advance on command", "function_ids": ["carriage"], "trigger": "external_command"},
            {"order": 2, "phase_id": "hold", "description": "Hold when the command is released", "function_ids": ["carriage"], "trigger": "external_command"},
            {"order": 3, "phase_id": "return", "description": "Return on command", "function_ids": ["carriage"], "trigger": "external_command"},
        ],
        pressure=100,
        drivers=[{"capability": "load_holding", "evidence": "Ten-minute no-drift hold.", "related_function_ids": ["carriage"]}],
    )
    components = [
        *_power_components(),
        _component("DCV", dcv_key, "dcv", "carriage"),
        _component("LoadLock", "GENERIC_DUAL_PILOT_OPERATED_CHECK", "pilot_check_valve", "carriage"),
        _component("CarriageCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "carriage"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "LoadLock", "A1", "work"),
        _connection("LoadLock", "A2", "CarriageCylinder", "Cap", "work"),
        _connection("DCV", "B", "LoadLock", "B1", "work"),
        _connection("LoadLock", "B2", "CarriageCylinder", "Rod", "work"),
    ]
    configurations = [
        _config("advance", "carriage", "extend", [("DCV", "extend")]),
        _config("hold", "carriage", "hold", [("DCV", "neutral")], active=[]),
        _config("return", "carriage", "retract", [("DCV", "retract")]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


@pytest.mark.parametrize(
    "dcv_key",
    ["GENERIC_4_3_SOLENOID_TANDEM_DCV", "GENERIC_4_3_SOLENOID_CLOSED_CENTER_DCV"],
)
def test_blocked_neutral_cannot_prove_a_pilot_check_hold(dcv_key: str) -> None:
    """Tandem and closed centres both trap the cross-pilot pressure."""
    requirements, topology = _positioning_fixture(dcv_key)
    report = validate_topology(topology, requirements)
    assert "LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD" in _codes(report)
    assert report.verdict == "invalid"


@pytest.mark.parametrize(
    "dcv_key",
    ["GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV", "GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV"],
)
def test_venting_neutral_accepts_the_pilot_check_hold(dcv_key: str) -> None:
    """A neutral that vents A and B to tank lets the poppets reseat."""
    requirements, topology = _positioning_fixture(dcv_key)
    report = validate_topology(topology, requirements)
    assert report.verdict == "valid", [item.model_dump(mode="json") for item in report.issues]


def test_selection_rejects_a_load_lock_paired_with_a_blocked_neutral() -> None:
    """The wrong pairing is caught before the netlist builder is invoked."""
    requirements, _ = _positioning_fixture("GENERIC_4_3_SOLENOID_TANDEM_DCV")
    plan = ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "minimal conventional holding circuit",
            "planning_summary": "Open circuit with an actuator-port load lock.",
            "components": [
                *_power_components(),
                _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "carriage"),
                _component("LoadLock", "GENERIC_DUAL_PILOT_OPERATED_CHECK", "pilot_check_valve", "carriage"),
                _component("CarriageCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "carriage"),
            ],
            "connection_intent": [],
        }
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert not evaluation.eligible
    assert any("float-centre or open-centre" in error for error in evaluation.errors)


# ---------------------------------------------------------------------------
# P7-05: an actuator at its end stop is not "active"
# ---------------------------------------------------------------------------


def _clamp_then_drill(*, completed: list[str] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = _requirements(
        "P7-05 clamp then drill with a forbidden release overlap",
        [
            _function(
                "clamp",
                [_phase("clamp_advance", "extend"), _phase("clamp_release", "retract")],
                holding={"must_hold_position": True},
            ),
            _function("drill", [_phase("drill_feed", "extend"), _phase("drill_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp first", "function_ids": ["clamp"], "trigger": "initial_command", "forbidden_overlap_function_ids": ["drill"]},
            {"order": 2, "phase_id": "drill_feed", "predecessor_phase_id": "clamp_advance", "description": "Drill after clamp pressure", "function_ids": ["drill"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "drill_retract", "predecessor_phase_id": "drill_feed", "description": "Drill retracts first", "function_ids": ["drill"], "trigger": "external_command", "forbidden_overlap_function_ids": ["clamp"]},
            # This is the step the live run failed on: the clamp may only
            # release once the drill has finished retracting, so the drill is
            # listed as a forbidden overlap.
            {"order": 4, "phase_id": "clamp_release", "predecessor_phase_id": "drill_retract", "description": "Clamp releases after drill retract pressure", "function_ids": ["clamp"], "trigger": "pressure", "hydraulically_enforced": True, "forbidden_overlap_function_ids": ["drill"]},
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
        _config(
            "clamp_release",
            "clamp",
            "retract",
            states["clamp_release"],
            forbidden=["drill"],
            completed=completed,
        ),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def test_undeclared_end_of_stroke_still_fails_the_overlap_rule() -> None:
    """Without the declaration the residual drill path is still an error."""
    requirements, topology = _clamp_then_drill(completed=None)
    report = validate_topology(topology, requirements)
    assert "FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE" in _codes(report)


def test_declared_and_proven_end_of_stroke_is_accepted() -> None:
    """The drill retracted in the previous phase, so it cannot retract again.

    This is the exact P7-05 stall.  The live run kept adding isolating hardware
    to stop an actuator that was already against its stop.
    """
    requirements, topology = _clamp_then_drill(completed=["drill"])
    report = validate_topology(topology, requirements)
    assert report.verdict == "valid", [item.model_dump(mode="json") for item in report.issues]


def test_completion_claim_must_be_proven_by_an_earlier_phase() -> None:
    """A design cannot silence the overlap rule by asserting completion."""
    requirements, topology = _clamp_then_drill(completed=["drill"])
    for configuration in topology["phase_configurations"]:
        if configuration["phase_id"] == "clamp_advance":
            # No phase before clamp_advance drives the drill anywhere.
            configuration["completed_function_ids"] = ["drill"]
    report = validate_topology(topology, requirements)
    assert "UNPROVEN_COMPLETED_FUNCTION" in _codes(report)


# ---------------------------------------------------------------------------
# P7-04: a load-actuated speed change is one throttle and nothing else
# ---------------------------------------------------------------------------


def _load_sensitive_requirements() -> dict[str, Any]:
    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    return _requirements(
        "P7-04 load-actuated machine slide",
        [
            _function(
                "slide",
                [
                    _phase("approach", "extend", **metered),
                    _phase("working", "extend", **metered),
                    _phase("return", "retract"),
                ],
            )
        ],
        [
            {"order": 1, "phase_id": "approach", "description": "Approach", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "working", "predecessor_phase_id": "approach", "description": "Load rise slows the slide", "function_ids": ["slide"], "trigger": "mechanical_coupling"},
            {"order": 3, "phase_id": "return", "description": "Return", "function_ids": ["slide"], "trigger": "external_command"},
        ],
        pressure=20,
    )


def _slide_plan(components: list[dict[str, Any]], approach: str) -> ComponentPlan:
    return ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": approach,
            "planning_summary": "Load-actuated machine slide.",
            "components": components,
            "connection_intent": [],
        }
    )


def test_hi_lo_two_pump_plan_is_rejected_without_an_energy_requirement() -> None:
    """The live P7-04 run chose a two-pump supply for a one-pump problem."""
    requirements = _load_sensitive_requirements()
    plan = _slide_plan(
        [
            _component("Tank", "GENERIC_TANK", "tank"),
            _component("PumpRapid", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump"),
            _component("PumpFeed", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump"),
            _component("ReliefValve", "GENERIC_RELIEF_VALVE", "relief_valve"),
            _component("RapidCheck", "GENERIC_CHECK_VALVE", "check_valve"),
            _component("FeedCheck", "GENERIC_CHECK_VALVE", "check_valve"),
            _component("UnloadingValve", "GENERIC_EXTERNALLY_PILOTED_UNLOADING_VALVE", "unloading_valve"),
            _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
            _component("RodMeterOut", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "slide"),
            _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
        ],
        "hi-lo two-pump supply with rod-side meter-out",
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert not evaluation.eligible
    assert any("without an energy-saving/hi-lo requirement" in error for error in evaluation.errors)
    assert any("pump-combining or regenerative justification" in error for error in evaluation.errors)


def test_minimal_single_throttle_plan_is_eligible() -> None:
    requirements = _load_sensitive_requirements()
    plan = _slide_plan(
        [
            *_power_components(),
            _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
            _component("RodMeterOut", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "slide"),
            _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
        ],
        "minimal conventional single-pump meter-out",
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert evaluation.eligible, evaluation.errors


def test_load_actuated_pattern_forbids_bypass_sequence_and_second_pump() -> None:
    hints = derive_pattern_hints(_load_sensitive_requirements())
    hint = next(hint for hint in hints if hint["id"].endswith("_load_actuated_speed_change_minimal"))
    prohibitions = " ".join(hint["prohibitions"]).casefold()
    assert "bypass" in prohibitions
    assert "sequence valve" in prohibitions
    assert "second pump" in prohibitions
    assert hint["required_types"] == ["one_way_flow_control"]


# ---------------------------------------------------------------------------
# P7-07: a pressure-triggered release order does not need a position valve
# ---------------------------------------------------------------------------


def _clamp_then_work_requirements() -> dict[str, Any]:
    return _requirements(
        "P7-07 clamp then work station",
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
            {"order": 4, "phase_id": "clamp_release", "predecessor_phase_id": "work_retract", "description": "Clamp releases after work retract pressure", "function_ids": ["clamp"], "trigger": "pressure", "hydraulically_enforced": True, "forbidden_overlap_function_ids": ["work"]},
        ],
        pressure=70,
        drivers=[{"capability": "branch_pressure_reduction", "evidence": "Clamp branch is limited to 40 bar.", "related_function_ids": ["clamp"]}],
    )


def test_position_isolator_is_rejected_for_a_pressure_release_order() -> None:
    """The live P7-07 run invented a position-operated isolator."""
    requirements = _clamp_then_work_requirements()
    plan = ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "sequenced clamp and work station",
            "planning_summary": "Reduced-pressure clamp branch with sequenced work branch.",
            "components": [
                *_power_components(),
                _component("StationDCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv"),
                _component("ClampReducer", "GENERIC_PRESSURE_REDUCING_VALVE_WITH_REVERSE_CHECK", "pressure_reducing_valve", "clamp"),
                _component("ClampPilotCheck", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp"),
                _component("ForwardSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "work"),
                _component("ReverseSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "clamp"),
                _component("WorkRetractIsolator", "GENERIC_POSITION_OPERATED_BYPASS", "position_valve", "work"),
                _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp"),
                _component("WorkCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "work"),
            ],
            "connection_intent": [],
        }
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert not evaluation.eligible
    assert any("position valve selected without" in error for error in evaluation.errors)


def test_reverse_order_pattern_gives_a_concrete_sequence_recipe() -> None:
    hints = derive_pattern_hints(_clamp_then_work_requirements())
    hint = next(hint for hint in hints if hint["id"] == "reverse_order_interlock")
    rules = " ".join(hint["connection_rules"]).casefold()
    prohibitions = " ".join(hint["prohibitions"]).casefold()
    assert "dcv.b" in rules and "sequence valve" in rules
    assert "position-operated valve" in prohibitions
    assert "electrical sequencing" in prohibitions


# ---------------------------------------------------------------------------
# Evidence gaps may no longer block on the model's say-so
# ---------------------------------------------------------------------------


def test_missing_citation_is_never_a_blocking_evidence_gap() -> None:
    blocking, explanation = classify_evidence_gap(
        {
            "capability": "Phase-selective rapid rod-exhaust bypass closure",
            "reason": "The supplied research lacks a cited schematic for this exact port-level arrangement.",
            "blocking": True,
        }
    )
    assert blocking is False
    assert "corroboration" in explanation.casefold()


def test_available_class_is_never_a_blocking_evidence_gap() -> None:
    blocking, _ = classify_evidence_gap(
        {
            "capability": "externally piloted sequence valve for load-pressure sensing",
            "reason": "The self-piloted arrangement senses the wrong line.",
            "blocking": True,
        }
    )
    assert blocking is False


def test_validation_downgrades_a_model_declared_blocking_evidence_gap() -> None:
    requirements = _load_sensitive_requirements()
    components = [
        *_power_components(),
        _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
        _component("RodMeterOut", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "slide"),
        _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "SlideCylinder", "Cap", "work"),
        _connection("SlideCylinder", "Rod", "RodMeterOut", "A", "work"),
        _connection("RodMeterOut", "B", "DCV", "B", "work"),
    ]
    configurations = [
        _config("approach", "slide", "extend", [("DCV", "extend")]),
        _config("working", "slide", "extend", [("DCV", "extend")]),
        _config("return", "slide", "retract", [("DCV", "retract")]),
    ]
    topology = _base_topology(requirements, components, connections, configurations)
    topology["evidence_gaps"] = [
        {
            "capability": "rod-side meter-out placement for a load-actuated feed",
            "reason": "No cited schematic shows this exact arrangement.",
            "blocking": True,
            "related_function_ids": ["slide"],
        }
    ]
    report = validate_topology(topology, requirements)
    assert "BLOCKING_EVIDENCE_GAP" not in _codes(report)
    assert report.verdict == "valid", [item.model_dump(mode="json") for item in report.issues]


# ---------------------------------------------------------------------------
# One speed across two loads needs a compensated throttle
# ---------------------------------------------------------------------------


def test_same_speed_across_a_large_load_change_forces_compensation() -> None:
    """P7-02: 2 m/min at both 12 kN and 50 kN cannot use a plain throttle."""
    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    approach = _phase("slow_approach", "extend", **metered)
    approach["speed"] = {"value": 2.0, "unit": "m/min"}
    approach["force"] = {"value": 12000.0, "unit": "N"}
    press = _phase("press", "extend", **metered)
    press["speed"] = {"value": 2.0, "unit": "m/min"}
    press["force"] = {"value": 50000.0, "unit": "N"}
    requirements = _requirements(
        "P7-02 three-stage press",
        [_function("ram", [approach, press])],
        [
            {"order": 1, "phase_id": "slow_approach", "description": "Slow approach", "function_ids": ["ram"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "press", "description": "Press", "function_ids": ["ram"], "trigger": "external_command"},
        ],
        pressure=100,
    )
    normalized, notes = normalize_requirements(requirements)
    phases = normalized.functions[0].motion_phases
    assert all(phase.flow_compensation == FlowCompensation.pressure_compensated for phase in phases)
    assert all(phase.speed_realization == SpeedRealization.load_independent_throttled for phase in phases)
    assert any("pressure-compensated" in note for note in notes)


def test_small_load_change_keeps_a_plain_throttle() -> None:
    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    first = _phase("first", "extend", **metered)
    first["speed"] = {"value": 2.0, "unit": "m/min"}
    first["force"] = {"value": 12000.0, "unit": "N"}
    second = _phase("second", "extend", **metered)
    second["speed"] = {"value": 2.0, "unit": "m/min"}
    second["force"] = {"value": 13000.0, "unit": "N"}
    requirements = _requirements(
        "Small load change",
        [_function("ram", [first, second])],
        [
            {"order": 1, "phase_id": "first", "description": "First", "function_ids": ["ram"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "second", "description": "Second", "function_ids": ["ram"], "trigger": "external_command"},
        ],
        pressure=100,
    )
    normalized, _ = normalize_requirements(requirements)
    assert all(
        phase.flow_compensation == FlowCompensation.non_compensated
        for phase in normalized.functions[0].motion_phases
    )


# ---------------------------------------------------------------------------
# The repair loop escalates instead of burning its budget in one scope
# ---------------------------------------------------------------------------


def test_error_signature_ignores_cosmetic_change() -> None:
    first = [
        {"severity": "error", "code": "METERING_BYPASSED_IN_PHASE", "related": ["Cylinder", "working"]},
        {"severity": "warning", "code": "NONBLOCKING_EVIDENCE_GAP", "related": []},
    ]
    second = [
        {"severity": "error", "code": "METERING_BYPASSED_IN_PHASE", "related": ["working", "Cylinder"]},
        {"severity": "warning", "code": "EXCESS_FLOW_CONTROL_COMPLEXITY", "related": ["X"]},
    ]
    assert _topology_error_signature(first) == _topology_error_signature(second)


def test_error_signature_is_empty_when_only_warnings_remain() -> None:
    assert _topology_error_signature([{"severity": "warning", "code": "X", "related": []}]) == ""


def test_scope_escalation_moves_outward() -> None:
    assert _ESCALATED_SCOPE["wiring"] == "selection"
    assert _ESCALATED_SCOPE["research"] == "selection"
    assert _ESCALATED_SCOPE["selection"] == "requirements"
    assert "requirements" not in _ESCALATED_SCOPE


# ---------------------------------------------------------------------------
# Catalog additions stay inside the topology-only boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV",
        "GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV",
        "GENERIC_VENTED_PILOT_OPERATED_RELIEF_VALVE",
    ],
)
def test_new_catalog_classes_are_well_formed(key: str) -> None:
    entry = CATALOG[key]
    assert entry["ports"]
    assert set(entry["port_types"]) == set(entry["ports"])
    assert entry["state_paths"]
    assert entry["default_state"] in entry["state_paths"]
    for paths in entry["state_paths"].values():
        for path in paths:
            assert path["from"] in entry["ports"]
            assert path["to"] in entry["ports"]
    # No sizing, rating or procurement language leaked into the class record.
    text = f"{entry['summary']} {' '.join(entry['capabilities'])}".casefold()
    assert not any(word in text for word in ("bore", "l/min", "cm3", "model", "part number"))


def test_venting_neutral_classes_reach_tank_from_both_work_ports() -> None:
    for key in ("GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV", "GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV"):
        neutral = CATALOG[key]["state_paths"]["neutral"]
        vented = {path["from"] for path in neutral if path["to"] == "T"}
        assert {"A", "B"} <= vented


def test_float_centre_neutral_does_not_unload_the_pump() -> None:
    neutral = CATALOG["GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV"]["state_paths"]["neutral"]
    assert not any(path["from"] == "P" for path in neutral)
    open_neutral = CATALOG["GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV"]["state_paths"]["neutral"]
    assert any(path["from"] == "P" and path["to"] == "T" for path in open_neutral)


# ---------------------------------------------------------------------------
# v0.4.1 - the P7-07 requirements-gate stall observed on v0.4.0
# ---------------------------------------------------------------------------
#
# The live v0.4.0 P7-07 run never reached research. It failed at
# `requirements_failure` on one structural issue - "Motion phase
# 'clamp_hold_during_work' has an unspecified load_control decision" - while the
# critic had returned `proceed_with_assumptions` with no blocking questions and
# the function already declared `must_hold_position` plus a `load_holding`
# driver. The decision was derivable; the run stopped anyway.


def _clamp_hold_requirements(
    *,
    load_control: str,
    holding: dict[str, Any],
    load_type: str = "resistive",
) -> dict[str, Any]:
    hold = _phase("clamp_hold_during_work", "hold")
    hold["load_control"] = load_control
    hold["load_type"] = load_type
    advance = _phase("clamp_extend_and_clamp", "extend")
    advance["load_type"] = load_type
    return _requirements(
        "P7-07 clamp hold",
        [_function("clamp_actuator", [advance, hold], holding=holding)],
        [
            {"order": 1, "phase_id": "clamp_extend_and_clamp", "description": "Clamp", "function_ids": ["clamp_actuator"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "clamp_hold_during_work", "description": "Hold while the work stroke runs", "function_ids": ["clamp_actuator"], "trigger": "external_command"},
        ],
        pressure=70,
        drivers=[{"capability": "load_holding", "evidence": "Clamp stays applied.", "related_function_ids": ["clamp_actuator"]}],
    )


def test_unspecified_load_control_no_longer_blocks_the_requirements_gate() -> None:
    """The exact issue that stopped the live P7-07 v0.4.0 run."""
    from hydraulic_mas.requirements_quality import requirements_quality_issues

    requirements = _clamp_hold_requirements(
        load_control="unspecified",
        holding={"must_hold_position": True},
    )
    query = "Clamp the part, then run the working stroke."
    assert requirements_quality_issues(requirements, query)

    normalized, notes = normalize_requirements(requirements, query)
    assert requirements_quality_issues(normalized, query) == []
    hold = normalized.functions[0].motion_phases[1]
    assert hold.load_control.value == "pilot_check"
    assert any("clamp_hold_during_work" in note and "pilot_check" in note for note in notes)


def test_unspecified_load_control_on_an_overrunning_phase_takes_counterbalance() -> None:
    requirements = _clamp_hold_requirements(
        load_control="unspecified",
        holding={"must_hold_position": True},
        load_type="overrunning",
    )
    normalized, _ = normalize_requirements(requirements, "Lower the load.")
    phases = {phase.id: phase for phase in normalized.functions[0].motion_phases}
    # An overrunning load needs dynamic control, so it outranks the holding rule.
    assert phases["clamp_hold_during_work"].load_control.value == "counterbalance"
    # The advance phase already carried an explicit decision and is left alone.
    assert phases["clamp_extend_and_clamp"].load_control.value == "none"


def test_unspecified_load_control_without_a_holding_requirement_takes_none() -> None:
    requirements = _clamp_hold_requirements(load_control="unspecified", holding={})
    normalized, _ = normalize_requirements(requirements, "Move the slide.")
    assert all(
        phase.load_control.value == "none"
        for phase in normalized.functions[0].motion_phases
    )


def test_an_explicit_load_control_decision_is_never_overwritten() -> None:
    requirements = _clamp_hold_requirements(
        load_control="none",
        holding={"must_hold_position": True, "no_creep": True},
    )
    normalized, notes = normalize_requirements(requirements, "Hold the carriage.")
    assert normalized.functions[0].motion_phases[1].load_control.value == "none"
    assert not any("load_control resolved" in note for note in notes)


def test_a_powered_hold_does_not_require_a_venting_centre() -> None:
    """P7-07's clamp is held with the valve commanded, not centred over the load.

    The first cut of this rule keyed off "the function has a hold phase", which
    would have forced a float centre onto every clamp-then-work station.
    """
    requirements = _clamp_hold_requirements(
        load_control="pilot_check",
        holding={"must_hold_position": True},
    )
    plan = ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "sequenced clamp station",
            "planning_summary": "Clamp held at pressure during the working stroke.",
            "components": [
                *_power_components(),
                _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "clamp_actuator"),
                _component("ClampPilotCheck", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp_actuator"),
                _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp_actuator"),
            ],
            "connection_intent": [],
        }
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert not any("float-centre or open-centre" in error for error in evaluation.errors)


def test_an_unpowered_timed_hold_still_requires_a_venting_centre() -> None:
    """P7-03's ten-minute drift-free hold does centre the spool over the load."""
    requirements = _clamp_hold_requirements(
        load_control="pilot_check",
        holding={"must_hold_position": True, "no_creep": True, "hold_duration": {"value": 10, "unit": "min"}},
    )
    plan = ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "holding fixture",
            "planning_summary": "Carriage held with the command released.",
            "components": [
                *_power_components(),
                _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "clamp_actuator"),
                _component("LoadLock", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp_actuator"),
                _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp_actuator"),
            ],
            "connection_intent": [],
        }
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert any("float-centre or open-centre" in error for error in evaluation.errors)


def _p7_trace_shaped_requirements() -> dict[str, Any]:
    """The requirement shape the live P7-07 run actually extracted.

    Differs from the canonical acceptance fixture in ways that matter: the clamp
    carries an explicit ``hold`` phase between clamping and the working stroke,
    that hold is itself a pressure-triggered sequence step, and the release step
    is triggered by completion of the previous phase rather than by a command.
    """
    clamp_advance = _phase("clamp_extend_and_clamp", "extend")
    clamp_hold = _phase("clamp_hold_during_work", "hold")
    clamp_hold["load_control"] = "unspecified"
    clamp_release = _phase("clamp_release_retract", "retract")
    return _requirements(
        "P7-07 clamp-then-work station",
        [
            _function(
                "clamp_actuator",
                [clamp_advance, clamp_hold, clamp_release],
                holding={"must_hold_position": True},
                max_working_pressure={"value": 40, "unit": "bar"},
                branch_limit=True,
            ),
            _function(
                "working_actuator",
                [_phase("working_advance", "extend"), _phase("working_retract", "retract")],
                max_working_pressure={"value": 60, "unit": "bar"},
            ),
        ],
        [
            {"order": 1, "phase_id": "clamp_extend_and_clamp", "description": "Clamp the part", "function_ids": ["clamp_actuator"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "clamp_hold_during_work", "predecessor_phase_id": "clamp_extend_and_clamp", "description": "Hold the clamp", "function_ids": ["clamp_actuator"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "working_advance", "predecessor_phase_id": "clamp_extend_and_clamp", "description": "Work advances on clamp pressure", "function_ids": ["working_actuator"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 4, "phase_id": "working_retract", "predecessor_phase_id": "working_advance", "description": "Work retracts", "function_ids": ["working_actuator"], "trigger": "external_command"},
            {"order": 5, "phase_id": "clamp_release_retract", "predecessor_phase_id": "working_retract", "description": "Clamp releases after the work retract", "function_ids": ["clamp_actuator"], "trigger": "completion_of_previous"},
        ],
        pressure=70,
        drivers=[
            {"capability": "branch_pressure_reduction", "evidence": "Clamp limited to 40 bar.", "related_function_ids": ["clamp_actuator"]},
            {"capability": "load_holding", "evidence": "Clamp stays applied through the working stroke.", "related_function_ids": ["clamp_actuator"]},
        ],
    )


def test_corrected_p7_07_validates_against_the_live_requirement_shape() -> None:
    """End-to-end: the corrected circuit is accepted for the real extraction.

    Requirements normalization must first resolve the ``unspecified``
    load_control that stopped the live run, then the corrected clamp-then-work
    topology must validate with no issues at all.
    """
    from hydraulic_mas.decision_flow import canonical_decision_payload
    from hydraulic_mas.requirements_quality import requirements_quality_issues

    raw = _p7_trace_shaped_requirements()
    query = "Clamp the part, then advance the working motion, then retract before releasing."
    assert requirements_quality_issues(raw, query)

    requirements = normalize_requirements(raw, query)[0].model_dump(mode="json")
    assert requirements_quality_issues(requirements, query) == []

    components = [
        *_power_components(),
        _component("StationDCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv"),
        _component("ClampReducer", "GENERIC_PRESSURE_REDUCING_VALVE_WITH_REVERSE_CHECK", "pressure_reducing_valve", "clamp_actuator"),
        _component("ClampPilotCheck", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp_actuator"),
        _component("ForwardSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "working_actuator"),
        _component("ReverseSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "clamp_actuator"),
        _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp_actuator"),
        _component("WorkCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "working_actuator"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "StationDCV", "P", "pressure"),
        _connection("StationDCV", "T", "Tank", "R", "return"),
        _connection("StationDCV", "A", "ClampReducer", "P", "work"),
        _connection("ClampReducer", "A", "ClampPilotCheck", "V", "work"),
        _connection("ClampReducer", "T", "Tank", "R", "drain"),
        _connection("ClampPilotCheck", "C", "ClampCylinder", "Cap", "work"),
        _connection("StationDCV", "A", "ForwardSequence", "P", "work"),
        _connection("ForwardSequence", "A", "WorkCylinder", "Cap", "work"),
        _connection("ForwardSequence", "T", "Tank", "R", "drain"),
        _connection("WorkCylinder", "Rod", "StationDCV", "B", "work"),
        _connection("StationDCV", "B", "ReverseSequence", "P", "work"),
        _connection("ReverseSequence", "A", "ClampCylinder", "Rod", "work"),
        _connection("ReverseSequence", "A", "ClampPilotCheck", "X", "pilot"),
        _connection("ReverseSequence", "T", "Tank", "R", "drain"),
    ]
    states = {
        "clamp_extend_and_clamp": [("StationDCV", "extend"), ("ForwardSequence", "closed"), ("ReverseSequence", "closed")],
        "clamp_hold_during_work": [("StationDCV", "extend"), ("ForwardSequence", "open"), ("ReverseSequence", "closed")],
        "working_advance": [("StationDCV", "extend"), ("ForwardSequence", "open"), ("ReverseSequence", "closed")],
        "working_retract": [("StationDCV", "retract"), ("ForwardSequence", "closed"), ("ReverseSequence", "closed")],
        "clamp_release_retract": [("StationDCV", "retract"), ("ForwardSequence", "closed"), ("ReverseSequence", "open")],
    }
    configurations = [
        _config("clamp_extend_and_clamp", "clamp_actuator", "extend", states["clamp_extend_and_clamp"]),
        _config("clamp_hold_during_work", "clamp_actuator", "hold", states["clamp_hold_during_work"]),
        _config("working_advance", "working_actuator", "extend", states["working_advance"]),
        _config("working_retract", "working_actuator", "retract", states["working_retract"]),
        # The working actuator is on its retracted stop, so its live rod line
        # cannot move it. No isolating valve is needed.
        _config("clamp_release_retract", "clamp_actuator", "retract", states["clamp_release_retract"], completed=["working_actuator"]),
    ]
    motion, synchronization = canonical_decision_payload(requirements)
    topology = {
        "design_narrative": "Corrected clamp-then-work station.",
        "components": components,
        "connections": connections,
        "motion_control_decisions": motion,
        "synchronization_decisions": synchronization,
        "phase_configurations": configurations,
        "function_implementations": [
            {
                "function_id": function["id"],
                "component_ids": [
                    item["id"]
                    for item in components
                    if item.get("function_id") in {None, function["id"]}
                    and item["comp_type"] not in {"tank", "pump", "relief_valve"}
                ],
                "circuit_pattern": "reduced-pressure clamp with forward and reverse pressure sequencing",
                "how_requirements_met": "Directed phase paths and component states are validated.",
            }
            for function in requirements["functions"]
        ],
    }
    report = validate_topology(topology, requirements)
    assert report.verdict == "valid", [item.model_dump(mode="json") for item in report.issues]
    assert report.issues == []


# ---------------------------------------------------------------------------
# v0.4.2 - the P7-05 missing drill throttle observed on v0.4.1
# ---------------------------------------------------------------------------
#
# The live v0.4.1 P7-05 run returned a valid circuit with no flow control on the
# drill feed. Both the clamp (25 mm/s) and the drill (1.667 mm/s) were typed
# sizing_only. One fixed pump cannot serve both: sized at 18.4 L/min for the
# clamp, feeding a 63 mm drill unthrottled gives 98 mm/s against a required
# 1.667 mm/s, and matching the speed by bore alone would need a 484 mm cylinder.


def _shared_supply_requirements(fast_mm_s: float, slow_mm_s: float) -> dict[str, Any]:
    clamp = _phase("clamp_advance", "extend")
    clamp["speed"] = {"value": fast_mm_s, "unit": "mm/s"}
    drill = _phase("drill_advance_feed", "extend")
    drill["speed"] = {"value": slow_mm_s, "unit": "mm/s"}
    return _requirements(
        "P7-05 clamp and drill on one supply",
        [
            _function("workpiece_clamp", [clamp, _phase("clamp_release", "retract")]),
            _function("drill_feed", [drill, _phase("drill_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp", "function_ids": ["workpiece_clamp"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "drill_advance_feed", "predecessor_phase_id": "clamp_advance", "description": "Drill", "function_ids": ["drill_feed"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "drill_retract", "predecessor_phase_id": "drill_advance_feed", "description": "Drill retracts", "function_ids": ["drill_feed"], "trigger": "external_command"},
            {"order": 4, "phase_id": "clamp_release", "predecessor_phase_id": "drill_retract", "description": "Clamp releases", "function_ids": ["workpiece_clamp"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
    )


def test_slower_actuator_on_a_shared_supply_gets_a_throttle() -> None:
    """The exact P7-05 omission: 25 mm/s and 1.667 mm/s on one pump."""
    requirements = _shared_supply_requirements(25.0, 1.6666666667)
    normalized, notes = normalize_requirements(requirements, "Clamp, then drill.")
    phases = {
        phase.id: phase
        for function in normalized.functions
        for phase in function.motion_phases
    }
    drill = phases["drill_advance_feed"]
    assert drill.speed_realization.value == "adjustable_throttled"
    assert drill.metering_side.value == "meter_out"
    assert drill.metered_chamber.value == "rod"
    assert drill.flow_compensation.value == "non_compensated"
    # The pump is sized for the faster motion, which stays unmetered.
    assert phases["clamp_advance"].speed_realization.value == "sizing_only"
    assert any("cannot be realized by pump and bore sizing alone" in note for note in notes)


def test_similar_speeds_on_a_shared_supply_are_left_alone() -> None:
    """A modest speed difference is still reconcilable by bore choice."""
    requirements = _shared_supply_requirements(25.0, 20.0)
    normalized, notes = normalize_requirements(requirements, "Clamp, then work.")
    assert all(
        phase.speed_realization.value == "sizing_only"
        for function in normalized.functions
        for phase in function.motion_phases
    )
    assert not any("bore sizing alone" in note for note in notes)


def test_a_single_function_is_never_affected_by_the_shared_supply_rule() -> None:
    """One actuator owns the whole delivery; its speed is a sizing choice."""
    fast = _phase("approach", "extend")
    fast["speed"] = {"value": 50.0, "unit": "mm/s"}
    slow = _phase("feed", "extend")
    slow["speed"] = {"value": 1.0, "unit": "mm/s"}
    requirements = _requirements(
        "Single actuator, two speeds",
        [_function("slide", [fast, slow])],
        [
            {"order": 1, "phase_id": "approach", "description": "Approach", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "feed", "description": "Feed", "function_ids": ["slide"], "trigger": "position"},
        ],
        pressure=70,
    )
    normalized, notes = normalize_requirements(requirements, "Rapid then feed.")
    assert not any("bore sizing alone" in note for note in notes)


def test_speeds_stated_in_different_units_are_compared_correctly() -> None:
    clamp = _phase("clamp_advance", "extend")
    clamp["speed"] = {"value": 1.5, "unit": "m/min"}
    drill = _phase("drill_advance_feed", "extend")
    drill["speed"] = {"value": 0.1, "unit": "m/min"}
    requirements = _requirements(
        "Mixed units",
        [
            _function("workpiece_clamp", [clamp]),
            _function("drill_feed", [drill]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp", "function_ids": ["workpiece_clamp"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "drill_advance_feed", "description": "Drill", "function_ids": ["drill_feed"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
    )
    normalized, _ = normalize_requirements(requirements, "Clamp, then drill.")
    phases = {p.id: p for f in normalized.functions for p in f.motion_phases}
    assert phases["drill_advance_feed"].speed_realization.value == "adjustable_throttled"
    assert phases["clamp_advance"].speed_realization.value == "sizing_only"


# ---------------------------------------------------------------------------
# v0.4.4 - the P7-04 and P7-05 stalls observed on v0.4.3
# ---------------------------------------------------------------------------
#
# Both were rule contradictions rather than model mistakes: two deterministic
# checks demanded opposite things, so no topology could satisfy both and the
# repair loop spent all four rounds moving a component it was simultaneously
# required to have and forbidden to have.


def test_intra_function_pressure_trigger_does_not_require_a_sequence_valve() -> None:
    """P7-04: 'the speed change must result from the change in load'.

    The transition is pressure-triggered and hydraulically enforced, but both
    phases belong to one actuator. A plain throttle is the mechanism. Requiring a
    sequence valve here, and then rejecting that valve as unauthorized, is what
    made the problem unsatisfiable.
    """
    from hydraulic_mas.gap_policy import requires_pressure_sequence_valve

    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    requirements = _requirements(
        "P7-04 load-actuated slide",
        [
            _function(
                "slide",
                [
                    _phase("approach", "extend", **metered),
                    _phase("working", "extend", **metered),
                    _phase("full_return", "retract"),
                ],
            )
        ],
        [
            {"order": 1, "phase_id": "approach", "description": "Approach", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "working", "predecessor_phase_id": "approach", "description": "Load rise slows the slide", "function_ids": ["slide"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 3, "phase_id": "full_return", "predecessor_phase_id": "working", "description": "Return", "function_ids": ["slide"], "trigger": "completion_of_previous"},
        ],
        pressure=20,
    )
    assert requires_pressure_sequence_valve(requirements) is False
    hints = {hint["id"] for hint in derive_pattern_hints(requirements)}
    assert any(hint.endswith("_load_actuated_speed_change_minimal") for hint in hints)
    assert "pressure_sequence_between_functions" not in hints
    # And a sequence valve must now be rejected rather than demanded.
    plan = ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "sequence-gated feed",
            "planning_summary": "Adds a contact sequence valve.",
            "components": [
                *_power_components(),
                _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
                _component("FeedFlowControl", "GENERIC_ONE_WAY_FLOW_CONTROL", "one_way_flow_control", "slide"),
                _component("ContactSequenceValve", "GENERIC_EXTERNALLY_PILOTED_SEQUENCE_VALVE", "sequence_valve", "slide"),
                _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
            ],
            "connection_intent": [],
        }
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert any("sequence valve selected without" in error for error in evaluation.errors)


def test_interfunction_pressure_trigger_still_requires_a_sequence_valve() -> None:
    """P7-05's clamp-then-drill interlock must not be relaxed by the same change."""
    from hydraulic_mas.gap_policy import requires_pressure_sequence_valve

    requirements = _requirements(
        "P7-05 clamp then drill",
        [
            _function("clamp", [_phase("clamp_advance", "extend")]),
            _function("drill", [_phase("drill_feed", "extend")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp", "function_ids": ["clamp"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "drill_feed", "predecessor_phase_id": "clamp_advance", "description": "Drill after clamp pressure", "function_ids": ["drill"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
    )
    assert requires_pressure_sequence_valve(requirements) is True
    hints = {hint["id"] for hint in derive_pattern_hints(requirements)}
    assert "pressure_sequence_between_functions" in hints


def test_position_trigger_still_requires_a_valve_state_change() -> None:
    """P7-01's cam-operated bypass is a real position trigger inside one actuator."""
    from hydraulic_mas.requirements_quality import normalize_requirements as _norm

    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    requirements = _requirements(
        "P7-01 rapid then feed",
        [
            _function(
                "slide",
                [
                    _phase("rapid", "extend", realization="unrestricted_rapid"),
                    _phase("feed", "extend", **metered),
                ],
            )
        ],
        [
            {"order": 1, "phase_id": "rapid", "description": "Rapid", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "feed", "predecessor_phase_id": "rapid", "description": "Cam closes the bypass", "function_ids": ["slide"], "trigger": "position", "hydraulically_enforced": True},
        ],
        pressure=70,
    )
    normalized, notes = _norm(requirements, "Rapid then feed at 250 mm.")
    phases = {phase.id: phase for phase in normalized.functions[0].motion_phases}
    # The position trigger means the bypass is real, so the rapid phase stays rapid.
    assert phases["rapid"].speed_realization.value == "unrestricted_rapid"
    assert not any("no position trigger" in note for note in notes)


def test_shared_extend_path_without_a_position_trigger_is_all_throttled() -> None:
    """P7-04's other half: one fixed orifice restricts every phase through it."""
    metered = dict(
        realization="load_sensitive_throttled",
        side="meter_out",
        chamber="rod",
        flow="exhaust",
        compensation="non_compensated",
    )
    requirements = _requirements(
        "P7-04 approach and feed",
        [
            _function(
                "slide",
                [
                    _phase("approach", "extend", realization="unrestricted_rapid"),
                    _phase("working", "extend", **metered),
                ],
            )
        ],
        [
            {"order": 1, "phase_id": "approach", "description": "Approach", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "working", "predecessor_phase_id": "approach", "description": "Load rise", "function_ids": ["slide"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=20,
    )
    normalized, notes = normalize_requirements(requirements, "Speed change from the load.")
    phases = {phase.id: phase for phase in normalized.functions[0].motion_phases}
    assert phases["approach"].speed_realization.value == "load_sensitive_throttled"
    assert phases["approach"].metering_side.value == "meter_out"
    assert any("no position trigger" in note for note in notes)


def _concurrent_hold_fixture(*, clamp_state_during_drill: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A clamp holding force while a drill feeds from the same supply."""
    requirements = _requirements(
        "Maintained clamp during drilling",
        [
            _function(
                "clamp",
                [_phase("clamp_advance", "extend"), _phase("clamp_hold", "hold"), _phase("clamp_release", "retract")],
                holding={"must_hold_position": True, "no_creep": True},
            ),
            _function("drill", [_phase("drill_feed", "extend"), _phase("drill_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_advance", "description": "Clamp", "function_ids": ["clamp"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "clamp_hold", "predecessor_phase_id": "clamp_advance", "description": "Hold while drilling", "function_ids": ["clamp"], "trigger": "external_command"},
            {"order": 3, "phase_id": "drill_feed", "predecessor_phase_id": "clamp_advance", "description": "Drill", "function_ids": ["drill"], "trigger": "pressure", "hydraulically_enforced": True},
            {"order": 4, "phase_id": "drill_retract", "predecessor_phase_id": "drill_feed", "description": "Drill retracts", "function_ids": ["drill"], "trigger": "external_command"},
            {"order": 5, "phase_id": "clamp_release", "predecessor_phase_id": "drill_retract", "description": "Clamp releases", "function_ids": ["clamp"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
        drivers=[{"capability": "load_holding", "evidence": "Clamp stays applied.", "related_function_ids": ["clamp"]}],
    )
    components = [
        *_power_components(),
        _component("CycleDCV", "GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV", "dcv"),
        _component("ClampLock", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "clamp"),
        _component("ForwardSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "drill"),
        _component("ReverseSequence", "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK", "sequence_valve", "clamp"),
        _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "clamp"),
        _component("DrillCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "drill"),
    ]
    connections = [
        *_power_connections(),
        _connection("Pump", "P", "CycleDCV", "P", "pressure"),
        _connection("CycleDCV", "T", "Tank", "R", "return"),
        _connection("CycleDCV", "A", "ClampLock", "V", "work"),
        _connection("ClampLock", "C", "ClampCylinder", "Cap", "work"),
        _connection("ClampCylinder", "Rod", "ReverseSequence", "A", "work"),
        _connection("ReverseSequence", "P", "CycleDCV", "B", "work"),
        _connection("ReverseSequence", "A", "ClampLock", "X", "pilot"),
        _connection("ReverseSequence", "T", "Tank", "R", "drain"),
        _connection("CycleDCV", "A", "ForwardSequence", "P", "work"),
        _connection("ForwardSequence", "A", "DrillCylinder", "Cap", "work"),
        _connection("ForwardSequence", "T", "Tank", "R", "drain"),
        _connection("DrillCylinder", "Rod", "CycleDCV", "B", "work"),
    ]
    states = {
        "clamp_advance": [("CycleDCV", "extend"), ("ForwardSequence", "closed"), ("ReverseSequence", "closed")],
        "clamp_hold": [("CycleDCV", clamp_state_during_drill), ("ForwardSequence", "open"), ("ReverseSequence", "closed")],
        "drill_feed": [("CycleDCV", "extend"), ("ForwardSequence", "open"), ("ReverseSequence", "closed")],
        "drill_retract": [("CycleDCV", "retract"), ("ForwardSequence", "open"), ("ReverseSequence", "closed")],
        "clamp_release": [("CycleDCV", "retract"), ("ForwardSequence", "closed"), ("ReverseSequence", "open")],
    }
    configurations = [
        _config("clamp_advance", "clamp", "extend", states["clamp_advance"], forbidden=["drill"]),
        _config("clamp_hold", "clamp", "hold", states["clamp_hold"], active=["clamp", "drill"]),
        _config("drill_feed", "drill", "extend", states["drill_feed"]),
        _config("drill_retract", "drill", "retract", states["drill_retract"], forbidden=["clamp"]),
        _config("clamp_release", "clamp", "retract", states["clamp_release"], forbidden=["drill"], completed=["drill"]),
    ]
    return requirements, _base_topology(requirements, components, connections, configurations)


def test_maintained_hold_must_stay_supplied() -> None:
    """Centring the valve over a clamp that another actuator depends on is rejected."""
    requirements, topology = _concurrent_hold_fixture(clamp_state_during_drill="neutral")
    report = validate_topology(topology, requirements)
    assert "CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED" in _codes(report)


def test_maintained_hold_that_stays_commanded_is_accepted() -> None:
    """And the parked-hold venting rule must not fire on a maintained hold."""
    requirements, topology = _concurrent_hold_fixture(clamp_state_during_drill="extend")
    report = validate_topology(topology, requirements)
    assert "LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD" not in _codes(report)
    assert "CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED" not in _codes(report)
    assert report.verdict == "valid", [item.model_dump(mode="json") for item in report.issues]


def test_parked_hold_still_requires_a_venting_neutral() -> None:
    """P7-03's carriage: no concurrent function, spool centred, lock must reseat."""
    requirements, topology = _positioning_fixture("GENERIC_4_3_SOLENOID_TANDEM_DCV")
    report = validate_topology(topology, requirements)
    assert "LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD" in _codes(report)


# ---------------------------------------------------------------------------
# v0.4.5 - the P7-05 two-directional-valve oscillation observed on v0.4.4
# ---------------------------------------------------------------------------


def _sequenced_pair_requirements() -> dict[str, Any]:
    return _requirements(
        "P7-05 clamp then drill",
        [
            _function("workpiece_clamp", [_phase("clamp_apply", "extend"), _phase("clamp_release", "retract")], holding={"must_hold_position": True}),
            _function("drill_feed_axis", [_phase("drill_feed", "extend"), _phase("drill_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "clamp_apply", "description": "Clamp", "function_ids": ["workpiece_clamp"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "drill_feed", "predecessor_phase_id": "clamp_apply", "description": "Drill after clamp pressure", "function_ids": ["drill_feed_axis"], "trigger": "pressure", "hydraulically_enforced": True},
        ],
        pressure=8,
    )


def _sequenced_plan(dcv_ids: list[str]) -> ComponentPlan:
    components = [
        *_power_components(),
        _component("ClampCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "workpiece_clamp"),
        _component("DrillCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "drill_feed_axis"),
        _component("ForwardSequenceValve", "GENERIC_EXTERNALLY_PILOTED_SEQUENCE_VALVE", "sequence_valve", "drill_feed_axis"),
        _component("ClampLock", "GENERIC_SINGLE_PILOT_OPERATED_CHECK", "single_pilot_check_valve", "workpiece_clamp"),
    ]
    for index, dcv_id in enumerate(dcv_ids):
        components.append(_component(dcv_id, "GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV", "dcv", None))
    return ComponentPlan.model_validate(
        {
            "candidate_id": "candidate_1",
            "approach": "sequenced clamp and drill",
            "planning_summary": "Pressure interlock between two functions.",
            "components": components,
            "connection_intent": [],
        }
    )


def test_pressure_sequenced_functions_need_one_command_valve() -> None:
    """The structural cause of the four-round P7-05 oscillation.

    With separate valves the clamp branch can be de-commanded on its own, so a
    clamp-referenced pilot dies and a pump-referenced pilot proves nothing. The
    run alternated between those two wrong answers, renaming the finding each
    round.
    """
    evaluation = _candidate_evaluation(_sequenced_plan(["ClampDCV", "DrillDCV"]), _sequenced_pair_requirements())
    assert not evaluation.eligible
    assert any("separate directional valves" in error for error in evaluation.errors)


def test_one_command_valve_for_sequenced_functions_is_accepted() -> None:
    evaluation = _candidate_evaluation(_sequenced_plan(["CycleDCV"]), _sequenced_pair_requirements())
    assert evaluation.eligible, evaluation.errors


def test_unsequenced_functions_may_have_separate_command_valves() -> None:
    """Two independent actuators with no pressure interlock are free to differ."""
    requirements = _requirements(
        "Two independent axes",
        [
            _function("axis_a", [_phase("a_extend", "extend"), _phase("a_retract", "retract")]),
            _function("axis_b", [_phase("b_extend", "extend"), _phase("b_retract", "retract")]),
        ],
        [
            {"order": 1, "phase_id": "a_extend", "description": "A", "function_ids": ["axis_a"], "trigger": "external_command"},
            {"order": 2, "phase_id": "b_extend", "description": "B", "function_ids": ["axis_b"], "trigger": "external_command"},
        ],
        pressure=70,
    )
    components = [
        *_power_components(),
        _component("DCVA", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "axis_a"),
        _component("DCVB", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "axis_b"),
        _component("CylA", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "axis_a"),
        _component("CylB", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "axis_b"),
    ]
    plan = ComponentPlan.model_validate(
        {"candidate_id": "c1", "approach": "independent axes", "planning_summary": "s", "components": components, "connection_intent": []}
    )
    evaluation = _candidate_evaluation(plan, requirements)
    assert not any("separate directional valves" in error for error in evaluation.errors)


def test_renamed_review_findings_still_count_as_no_progress() -> None:
    """Free-form reviewer codes must not defeat the stall detector.

    These are the four real P7-05 rounds: one fault, four code names, and under
    code-only comparison four fresh signatures.
    """
    from hydraulic_mas.graph import (
        _SUBJECT_STALL_OVERLAP,
        _subjects_overlap,
        _topology_error_subjects,
        _topology_error_signature,
    )

    topology = {"components": [{"id": name} for name in ("ClampDCV", "DrillDCV", "ForwardSequenceValve", "ClampCylinder", "Pump")]}
    rounds = [
        ("FWD_SEQ_PILOT_LOST_IN_CLAMP_RELEASE", ["ClampDCV", "DrillDCV", "ForwardSequenceValve", "ClampCylinder"]),
        ("FWD_SEQ_NOT_CLAMP_PRESSURE_INTERLOCKED", ["ClampDCV", "DrillDCV", "ForwardSequenceValve", "Pump"]),
    ]
    issue_sets = [
        [{"severity": "error", "code": code, "related": related}] for code, related in rounds
    ]
    # Code-only signatures differ, so the original detector sees progress.
    assert _topology_error_signature(issue_sets[0]) != _topology_error_signature(issue_sets[1])
    # Subject overlap sees the truth.
    first = _topology_error_subjects(issue_sets[0], topology)
    second = _topology_error_subjects(issue_sets[1], topology)
    assert _subjects_overlap(second, first) >= _SUBJECT_STALL_OVERLAP


def test_subject_overlap_ignores_unrelated_rounds() -> None:
    from hydraulic_mas.graph import _subjects_overlap, _SUBJECT_STALL_OVERLAP

    assert _subjects_overlap(frozenset({"A", "B"}), frozenset({"C", "D"})) == 0.0
    assert _subjects_overlap(frozenset(), frozenset({"A"})) == 0.0
    assert _subjects_overlap(frozenset({"A", "B"}), frozenset({"C", "D"})) < _SUBJECT_STALL_OVERLAP
