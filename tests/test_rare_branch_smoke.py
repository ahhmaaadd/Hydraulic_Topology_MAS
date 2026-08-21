"""Force every normally-empty branch to execute at least once.

A live P7-01 run crashed here::

    File "hydraulic_mas/candidate_selection.py", line 172, in _candidate_evaluation
      "topology catalog gaps: " + ", ".join(sorted({gap.capability for gap in topology_gaps}))
    AttributeError: 'dict' object has no attribute 'capability'

``blocking_catalog_gaps`` takes and returns plain dicts, but the caller read an
attribute. The bug shipped in v0.3.0 and survived every trace we reviewed for one
reason: the branch only runs when the component planner emits a genuinely
*blocking* ``CatalogGap``, and until that run it never had.

That is the real defect class. Most of this pipeline's optional fields -
``catalog_gaps``, ``evidence_gaps``, ``repair_actions``, ``external_interfaces``,
``port_terminations``, ``component_planner_recovery`` - are empty in a healthy
run, so the code that consumes them is only exercised when something has already
gone wrong. Those are exactly the moments when a crash is most expensive.

Every test here therefore populates the optional fields and runs the real
deterministic code over them. None of these assert on hydraulics; they assert
that the code survives its own error-reporting paths.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydraulic_mas.candidate_selection import (
    _candidate_evaluation,
    apply_repair_actions,
    evaluate_and_select_candidates,
)
from hydraulic_mas.gap_policy import (
    blocking_catalog_gaps,
    classify_catalog_gap,
    classify_evidence_gap,
)
from hydraulic_mas.schemas import ComponentPlan, ComponentPlanSet
from hydraulic_mas.terminal import TerminalReporter
from hydraulic_mas.validation import repair_scope, validate_topology

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


def _simple_requirements() -> dict[str, Any]:
    return _requirements(
        "Rare-branch smoke",
        [_function("slide", [_phase("advance", "extend"), _phase("retreat", "retract")])],
        [
            {"order": 1, "phase_id": "advance", "description": "Advance", "function_ids": ["slide"], "trigger": "initial_command"},
            {"order": 2, "phase_id": "retreat", "description": "Retreat", "function_ids": ["slide"], "trigger": "external_command"},
        ],
        pressure=70,
    )


def _simple_components() -> list[dict[str, Any]]:
    return [
        *_power_components(),
        _component("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
        _component("SlideCylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide"),
    ]


def _simple_connections() -> list[dict[str, Any]]:
    return [
        *_power_connections(),
        _connection("Pump", "P", "DCV", "P", "pressure"),
        _connection("DCV", "T", "Tank", "R", "return"),
        _connection("DCV", "A", "SlideCylinder", "Cap", "work"),
        _connection("DCV", "B", "SlideCylinder", "Rod", "work"),
    ]


def _plan(**extra: Any) -> ComponentPlan:
    payload: dict[str, Any] = {
        "candidate_id": "candidate_1",
        "approach": "minimal conventional",
        "planning_summary": "Smoke-test plan.",
        "components": _simple_components(),
        "connection_intent": [],
    }
    payload.update(extra)
    return ComponentPlan.model_validate(payload)


# ---------------------------------------------------------------------------
# The exact crash
# ---------------------------------------------------------------------------


_BLOCKING_GAP = {
    "capability": "Three-position valve with a regenerative centre",
    "needed_component_class": "regenerative centre directional valve",
    "reason": "No available class connects both work ports to the pressure line in one position.",
    "related_function_ids": ["slide"],
    "scope": "topology",
    "blocking": True,
}


def test_blocking_catalog_gap_is_reported_without_crashing() -> None:
    """The live P7-01 traceback, pinned."""
    evaluation = _candidate_evaluation(_plan(catalog_gaps=[_BLOCKING_GAP]), _simple_requirements())
    assert not evaluation.eligible
    assert any("topology catalog gaps" in error for error in evaluation.errors)
    assert any("regenerative centre" in error.casefold() for error in evaluation.errors)


def test_blocking_catalog_gap_survives_full_candidate_selection() -> None:
    """The same gap through the node the graph actually calls."""
    plan_set = ComponentPlanSet.model_validate(
        {
            "candidates": [
                {
                    "candidate_id": "candidate_1",
                    "approach": "gap-reporting candidate",
                    "planning_summary": "Reports a blocking catalog gap.",
                    "components": _simple_components(),
                    "connection_intent": [],
                    "catalog_gaps": [_BLOCKING_GAP],
                },
                {
                    "candidate_id": "candidate_2",
                    "approach": "clean candidate",
                    "planning_summary": "No gaps.",
                    "components": _simple_components(),
                    "connection_intent": [],
                },
            ],
            "preferred_candidate_id": "candidate_1",
            "comparison_summary": "One candidate reports a blocking catalog gap; the other does not.",
        }
    )
    selected, candidates, evaluations = evaluate_and_select_candidates(plan_set, _simple_requirements())
    assert len(candidates) == 2
    assert len(evaluations) == 2
    # The clean candidate must win over the one that cannot be built.
    assert selected.candidate_id == "candidate_2"


def test_gap_helpers_tolerate_missing_and_null_keys() -> None:
    """LLM output can omit fields the schema marks optional."""
    for gap in ({}, {"capability": None, "reason": None}, {"scope": "sizing"}):
        assert isinstance(classify_catalog_gap(gap), tuple)
        assert isinstance(classify_evidence_gap(gap), tuple)
    assert blocking_catalog_gaps([]) == []
    assert blocking_catalog_gaps([{}])[0] == {}


# ---------------------------------------------------------------------------
# Every other normally-empty consumer
# ---------------------------------------------------------------------------


def test_non_blocking_and_sizing_scoped_gaps_are_reported() -> None:
    evaluation = _candidate_evaluation(
        _plan(
            catalog_gaps=[
                {
                    "capability": "Nicer valve",
                    "needed_component_class": "check valve",
                    "reason": "No cited schematic shows this exact arrangement.",
                    "scope": "topology",
                    "blocking": True,
                },
                {
                    "capability": "Bore selection",
                    "needed_component_class": "cylinder",
                    "reason": "Sizing is out of scope.",
                    "scope": "sizing",
                    "blocking": True,
                },
            ],
            evidence_gaps=[
                {"capability": "Rod-side meter-out placement", "reason": "No cited schematic.", "blocking": True},
                {"capability": "Neutral venting", "reason": "Partial corroboration.", "blocking": False},
            ],
        ),
        _simple_requirements(),
    )
    # Neither gap is a real catalog absence, so neither may block selection.
    assert not any("topology catalog gaps" in error for error in evaluation.errors)
    assert sum("evidence gap" in warning for warning in evaluation.warnings) == 2


@pytest.mark.parametrize(
    "action",
    [
        {"action": "add_component", "rationale": "add", "component": {"id": "Extra", "catalog_key": "GENERIC_CHECK_VALVE", "comp_type": "check_valve", "role": "r", "selection_basis": "b"}},
        {"action": "delete_component", "rationale": "delete", "target_component_id": "DCV"},
        {"action": "replace_component", "rationale": "replace", "target_component_id": "DCV", "component": {"id": "DCV2", "catalog_key": "GENERIC_4_2_SOLENOID_DCV", "comp_type": "dcv", "role": "r", "selection_basis": "b"}},
        {"action": "add_connection", "rationale": "add", "replacement_connection": {"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d"}},
        {"action": "delete_connection", "rationale": "delete", "target_connection": {"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d"}},
        {"action": "replace_connection", "rationale": "replace", "target_connection": {"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d"}, "replacement_connection": {"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d2"}},
    ],
)
def test_every_repair_action_kind_applies(action: dict[str, Any]) -> None:
    plan = _plan(
        connection_intent=[{"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d"}],
        repair_actions=[action],
    )
    repaired = apply_repair_actions(plan)
    assert isinstance(repaired, ComponentPlan)
    # And the repaired plan must still be scoreable.
    assert _candidate_evaluation(repaired, _simple_requirements()) is not None


@pytest.mark.parametrize(
    "action",
    [
        {"action": "delete_component", "rationale": "no target given"},
        {"action": "add_component", "rationale": "no component given"},
        {"action": "replace_component", "rationale": "half a payload", "target_component_id": "DCV"},
        {"action": "add_connection", "rationale": "no connection given"},
        {"action": "delete_connection", "rationale": "no connection given"},
        {"action": "replace_connection", "rationale": "half a payload", "target_connection": {"from_hint": "Pump", "to_hint": "DCV", "line": "pressure", "description": "d"}},
    ],
)
def test_repair_action_with_missing_payload_is_rejected_at_parse_time(action: dict[str, Any]) -> None:
    """The prompt forbids these, so the model will eventually produce one.

    The schema rejects the payload during validation, which is the right place:
    the structured-output recovery path in the graph retries the model instead of
    letting a half-built action reach ``apply_repair_actions``. This pins that
    guarantee so a future schema edit cannot quietly drop it.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _plan(repair_actions=[action])


def test_validation_reports_populated_gaps_interfaces_and_terminations() -> None:
    requirements = _simple_requirements()
    topology = _base_topology(
        requirements,
        _simple_components(),
        _simple_connections(),
        [
            _config("advance", "slide", "extend", [("DCV", "extend")]),
            _config("retreat", "slide", "retract", [("DCV", "retract")]),
        ],
    )
    topology["catalog_gaps"] = [_BLOCKING_GAP, {**_BLOCKING_GAP, "scope": "sizing"}]
    topology["evidence_gaps"] = [
        {"capability": "Something", "reason": "No cited source.", "blocking": True},
        {"capability": "Something else", "reason": "Thin corroboration.", "blocking": False},
    ]
    topology["external_interfaces"] = [
        {"component_id": "DCV", "port": "A", "external_system": "machine frame", "domain": "mechanical", "notes": None}
    ]
    topology["port_terminations"] = [
        {"component_id": "DCV", "port": "T", "termination": "tank", "justification": "return"}
    ]
    topology["assumptions"] = ["Horizontal, resistive load."]
    topology["open_issues"] = ["Settings deferred to sizing."]
    report = validate_topology(topology, requirements)
    codes = {item.code for item in report.issues}
    assert "BLOCKING_TOPOLOGY_CATALOG_GAP" in codes
    # A dumped report must round-trip, since the graph stores it in state.
    assert report.model_dump(mode="json")["verdict"] in {"valid", "invalid"}


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("requirements", "requirements"),
        ("wiring", "wiring"),
        ("selection", "selection"),
        ("safety", "selection"),
        ("research", "research"),
    ],
)
def test_repair_scope_handles_every_scope(scope: str, expected: str) -> None:
    issues = [{"severity": "error", "scope": scope, "code": "X", "description": "d", "related": []}]
    assert repair_scope(issues) == expected


def test_repair_scope_accepts_models_and_dicts_and_empty() -> None:
    from hydraulic_mas.schemas import TopologyIssue

    assert repair_scope([]) == "none"
    assert repair_scope([{"severity": "warning", "scope": "wiring", "code": "X", "description": "d", "related": []}]) == "none"
    model = TopologyIssue(code="X", severity="error", scope="wiring", description="d", related=[])
    assert repair_scope([model]) == "wiring"


def test_terminal_renders_a_fully_populated_final_output() -> None:
    """Rendering runs after everything else and must never be the thing that fails."""
    terminal = TerminalReporter(compact=False)
    terminal.final_output(
        {
            "problem_id": "P7-01",
            "title": "Smoke",
            "status": "unresolved",
            "scope_statement": "scope",
            "design_narrative": "narrative",
            "selected_components": [
                {"id": "Tank", "catalog_key": "GENERIC_TANK", "name": "Tank", "comp_type": "tank", "role": "r", "function_id": None, "ports": ["S", "R"], "configuration": [], "selection_basis": "b"}
            ],
            "connections": [{"from_component": "Tank", "from_port": "S", "to_component": "Pump", "to_port": "S", "line": "suction"}],
            "motion_control_decisions": [],
            "synchronization_decisions": [],
            "phase_configurations": [],
            "candidate_evaluations": [{"candidate_id": "candidate_1", "eligible": False, "score": 1.0, "component_count": 2, "errors": ["e"], "warnings": ["w"], "selected": True}],
            "component_planner_recovery": [{"attempt": 1, "error": "bad payload"}],
            "repair_history": [{"action": "delete_component", "rationale": "r"}],
            "external_interfaces": [{"component_id": "DCV", "port": "A", "external_system": "frame", "domain": "mechanical", "notes": None}],
            "port_terminations": [{"component_id": "DCV", "port": "T", "termination": "tank", "justification": "j"}],
            "function_implementations": [],
            "design_decisions": [],
            "catalog_gaps": [_BLOCKING_GAP],
            "evidence_gaps": [{"capability": "c", "reason": "r", "blocking": False}],
            "assumptions": ["a"],
            "open_issues": ["o"],
            "research_audit": {"searches": 1, "rounds": 1, "unique_sources": 1, "extracted": 1, "duplicates": 0, "verified_claims": 1},
            "research_coverage": {"status": "sufficient", "can_proceed": True, "summary": "s", "items": [], "missing_or_weak_information": [], "follow_up_tasks": []},
            "validation": {"verdict": "invalid", "deterministic": {"verdict": "invalid", "issues": [], "checks": []}, "design_review": {"verdict": "invalid", "summary": "s", "design_issues": []}, "repair_scope": "selection", "topology_round": 4, "summary": "s"},
            "repair_stop_reason": "no progress",
            "validation_fingerprints": ["abc"],
        }
    )


def test_terminal_renders_a_failure_payload() -> None:
    TerminalReporter(compact=True).failure(
        {"stage": "requirements", "message": "unresolved", "critique": {"completeness_status": "needs_clarification"}}
    )


def test_candidate_set_cannot_be_empty() -> None:
    """``evaluate_and_select_candidates`` ends with ``ranked[0]``.

    That indexing is only safe because the schema guarantees at least two
    candidates. Pin the guarantee so a future schema relaxation cannot turn a
    sparse model response into an IndexError inside the graph.
    """
    from pydantic import ValidationError

    for count in (0, 1):
        with pytest.raises(ValidationError):
            ComponentPlanSet.model_validate(
                {
                    "candidates": [
                        {
                            "candidate_id": f"candidate_{index}",
                            "approach": "a",
                            "planning_summary": "s",
                            "components": _simple_components(),
                            "connection_intent": [],
                        }
                        for index in range(count)
                    ],
                    "preferred_candidate_id": "candidate_0",
                    "comparison_summary": "c",
                }
            )
