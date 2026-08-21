from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from hydraulic_mas.candidate_selection import evaluate_and_select_candidates
from hydraulic_mas.config import Settings
from hydraulic_mas.decision_flow import canonical_decision_payload
from hydraulic_mas.graph import Workflow
from hydraulic_mas.patterns import derive_pattern_hints
from hydraulic_mas.requirements_quality import normalize_requirements, requirements_quality_issues
from hydraulic_mas.schemas import (
    ComponentPlan,
    ComponentPlanSet,
    CritiqueResult,
    PlannedComponent,
    RequirementsSpec,
)
from hydraulic_mas.validation import validate_topology
from test_validation import requirements as p1_requirements
from test_validation import valid_topology


class StaticRunnable:
    def __init__(self, value):
        self.value = value

    def invoke(self, _input):
        return self.value


class NoopSearch:
    def search(self, query: str, *, max_results: int):
        return []


def _settings() -> Settings:
    return Settings(
        model="test",
        fast_model="test",
        openai_api_key=None,
        openai_base_url=None,
        default_headers={},
        azure_api_key=None,
        azure_endpoint=None,
        azure_api_version="test",
        tavily_api_key=None,
    )


def _base_components() -> list[PlannedComponent]:
    values = [
        ("Tank", "GENERIC_TANK", "tank"),
        ("Pump", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump"),
        ("ReliefValve", "GENERIC_RELIEF_VALVE", "relief_valve"),
        ("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv"),
        ("Cylinder", "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder"),
    ]
    return [
        PlannedComponent(
            id=component_id,
            catalog_key=key,
            comp_type=comp_type,
            role="Required regression fixture class.",
            function_id="slide" if comp_type in {"dcv", "cylinder"} else None,
            selection_basis="Required by the typed topology contract.",
        )
        for component_id, key, comp_type in values
    ]


def _sizing_only_requirements() -> dict:
    raw = deepcopy(p1_requirements())
    raw["operational_logic"]["sequence"] = []
    raw["derived_design_drivers"] = [raw["derived_design_drivers"][0]]
    raw["functions"][0]["motion_phases"] = [
        {
            "id": "advance",
            "name": "Advance",
            "motion": "extend",
            "speed": {"value": 58.3, "unit": "mm/s"},
            "load_type": "resistive",
            "speed_adjustable": False,
            "speed_load_independent": False,
            "speed_realization": "sizing_only",
            "metering_side": "none",
            "metered_chamber": "none",
            "metered_flow": "none",
            "flow_compensation": "none",
            "load_control": "none",
            "motion_control_justification": "The numeric target is deferred to pump/cylinder sizing.",
            "motion_control_source": "inferred",
        }
    ]
    return RequirementsSpec.model_validate(raw).model_dump(mode="json")


def test_numeric_speed_target_is_a_sizing_decision_not_a_flow_control_demand() -> None:
    requirements = _sizing_only_requirements()
    first = ComponentPlan(
        candidate_id="minimal",
        planning_summary="Direct sizing-only circuit.",
        components=_base_components(),
        connection_intent=[],
    )
    second = first.model_copy(deep=True, update={"candidate_id": "alternative"})
    selected, _plans, evaluations = evaluate_and_select_candidates(
        ComponentPlanSet(
            candidates=[first, second],
            preferred_candidate_id="minimal",
            comparison_summary="Equivalent regression candidates.",
        ),
        requirements,
    )
    assert selected.candidate_id == "minimal"
    assert all(item.eligible for item in evaluations)
    hints = derive_pattern_hints(requirements)
    assert any(item["id"] == "slide_sizing_only_speed" for item in hints)


def test_flow_control_is_rejected_when_all_speed_targets_are_sizing_only() -> None:
    requirements = _sizing_only_requirements()
    bad = ComponentPlan(
        candidate_id="extra_control",
        planning_summary="Overcomplicated circuit.",
        components=[
            *_base_components(),
            PlannedComponent(
                id="UnjustifiedControl",
                catalog_key="GENERIC_ONE_WAY_FLOW_CONTROL",
                comp_type="one_way_flow_control",
                role="Unjustified numeric-speed control.",
                function_id="slide",
                selection_basis="Deliberately invalid regression fixture.",
            ),
        ],
        connection_intent=[],
    )
    good = ComponentPlan(
        candidate_id="minimal",
        planning_summary="Sizing-only circuit.",
        components=_base_components(),
        connection_intent=[],
    )
    selected, _plans, evaluations = evaluate_and_select_candidates(
        ComponentPlanSet(
            candidates=[bad, good],
            preferred_candidate_id="extra_control",
            comparison_summary="Selector must override unnecessary complexity.",
        ),
        requirements,
    )
    assert selected.candidate_id == "minimal"
    bad_eval = next(item for item in evaluations if item.candidate_id == "extra_control")
    assert any("unjustified flow-control" in error for error in bad_eval.errors)


def test_same_direction_speed_change_requires_a_typed_topology_realization() -> None:
    requirements = _sizing_only_requirements()
    second = deepcopy(requirements["functions"][0]["motion_phases"][0])
    second["id"] = "slow_feed"
    second["name"] = "Slow feed"
    second["speed"] = {"value": 10, "unit": "mm/s"}
    requirements["functions"][0]["motion_phases"].append(second)
    issues = requirements_quality_issues(
        requirements,
        "The slide extends rapidly and then continues more slowly.",
    )
    assert any("distinct speeds" in issue for issue in issues)


def test_answered_semantic_clarification_is_not_reasked_under_a_new_id() -> None:
    repeated = {
        "id": "cq_load_character_999",
        "topic": "load_character",
        "question": "Can the horizontal slide load overrun either actuator?",
        "why_it_matters": "It changes load control.",
        "blocking": True,
        "related": ["slide"],
    }
    workflow = Workflow(
        settings=_settings(),
        agents=SimpleNamespace(
            critic=StaticRunnable(
                CritiqueResult(
                    completeness_status="needs_clarification",
                    rationale="A repeated question with a new id.",
                    blocking_questions=[repeated],
                )
            )
        ),
        search_client=NoopSearch(),
    )
    state = {
        "user_query": "Move one horizontal slide.",
        "requirements": p1_requirements(),
        "interactive": True,
        "clarification_records": [
            {
                "id": "cq_load_character_001",
                "topic": "load_character",
                "question": "Can the load overrun?",
                "related": ["slide"],
                "answer": "No. It is horizontal and resistive.",
            }
        ],
        "clarification_answers": ["cq_load_character_001: No. It is horizontal and resistive."],
        "resolved_clarification_keys": ["load_character|slide", "id|cq_load_character_001"],
        "requirements_round": 1,
        "max_requirements_rounds": 2,
        "requirements_repair_attempted": False,
    }
    state.update(workflow.critique_requirements(state))
    assert workflow._blocking_questions(state) == []
    assert workflow.route_after_requirements_critique(state) == "finalize_requirements"
    result = workflow.finalize_requirements(state)
    assert result["requirements_unresolved"] is False
    assert result["requirements_gate"]["decision"] == "proceed"


def test_operator_command_cannot_become_phantom_hydraulic_sequence() -> None:
    raw = deepcopy(p1_requirements())
    step = raw["operational_logic"]["sequence"][0]
    step["trigger"] = "external_command"
    step["hydraulically_enforced"] = True
    normalized, notes = normalize_requirements(
        raw,
        "The operator must be able to stop the carriage; releasing the command returns it to neutral.",
    )
    assert normalized.operational_logic.sequence[0].hydraulically_enforced is False
    assert any("command logic" in note for note in notes)


def _single_phase_requirements(*, strategy: str = "none", actuator_count: int = 1) -> dict:
    raw = _sizing_only_requirements()
    raw["functions"][0]["synchronization"] = {
        "required": strategy != "none",
        "actuator_count": actuator_count,
        "strategy": strategy,
        "series_displacement_compatibility": (
            "explicitly_matched" if strategy == "hydraulic_series" else "not_applicable"
        ),
        "justification": "Explicit regression topology.",
        "source": "explicit",
    }
    if strategy == "hydraulic_series":
        raw["derived_design_drivers"].append(
            {
                "capability": "synchronization",
                "evidence": "Explicit matched hydraulic series operation.",
                "related_function_ids": ["slide"],
            }
        )
    return raw


def _direct_topology(requirements: dict, cylinders: list[str]) -> dict:
    motion, sync = canonical_decision_payload(requirements)
    component_rows = [
        ("Tank", "GENERIC_TANK", "tank", None),
        ("Pump", "GENERIC_FIXED_DISPLACEMENT_PUMP", "pump", None),
        ("ReliefValve", "GENERIC_RELIEF_VALVE", "relief_valve", None),
        ("DCV", "GENERIC_4_3_SOLENOID_TANDEM_DCV", "dcv", "slide"),
        *[(item, "GENERIC_DOUBLE_ACTING_CYLINDER", "cylinder", "slide") for item in cylinders],
    ]
    return {
        "design_narrative": "Regression circuit.",
        "components": [
            {
                "id": component_id,
                "catalog_key": key,
                "comp_type": comp_type,
                "role": "Regression topology function.",
                "function_id": function_id,
                "configuration": [],
                "selection_basis": "Required by the fixture.",
            }
            for component_id, key, comp_type, function_id in component_rows
        ],
        "connections": [
            {"from_component": "Tank", "from_port": "S", "to_component": "Pump", "to_port": "S", "line": "suction"},
            {"from_component": "Pump", "from_port": "P", "to_component": "ReliefValve", "to_port": "P", "line": "pressure"},
            {"from_component": "ReliefValve", "from_port": "T", "to_component": "Tank", "to_port": "R", "line": "return"},
            {"from_component": "Pump", "from_port": "P", "to_component": "DCV", "to_port": "P", "line": "pressure"},
            {"from_component": "DCV", "from_port": "T", "to_component": "Tank", "to_port": "R", "line": "return"},
        ],
        "motion_control_decisions": motion,
        "synchronization_decisions": sync,
        "phase_configurations": [
            {
                "phase_id": "advance",
                "function_id": "slide",
                "motion": "extend",
                "component_states": [{"component_id": "DCV", "state": "extend"}],
                "expected_active_function_ids": ["slide"],
            }
        ],
        "function_implementations": [
            {
                "function_id": "slide",
                "component_ids": ["DCV", *cylinders],
                "how_requirements_met": "The directed phase graph proves motion.",
            }
        ],
    }


def test_explicit_matched_series_cylinders_are_recognized_as_controlled() -> None:
    requirements = _single_phase_requirements(strategy="hydraulic_series", actuator_count=2)
    topology = _direct_topology(requirements, ["Cylinder1", "Cylinder2"])
    topology["connections"].extend(
        [
            {"from_component": "DCV", "from_port": "A", "to_component": "Cylinder1", "to_port": "Cap", "line": "work"},
            {"from_component": "Cylinder1", "from_port": "Rod", "to_component": "Cylinder2", "to_port": "Cap", "line": "work"},
            {"from_component": "Cylinder2", "from_port": "Rod", "to_component": "DCV", "to_port": "B", "line": "work"},
        ]
    )
    report = validate_topology(topology, requirements)
    assert report.verdict == "valid", [item.model_dump() for item in report.issues]


def test_regenerative_phase_accepts_chamber_recirculation_instead_of_tank_exhaust() -> None:
    requirements = _single_phase_requirements()
    phase = requirements["functions"][0]["motion_phases"][0]
    phase.update(
        {
            "speed_realization": "regenerative",
            "motion_control_justification": "Explicit regenerative fast approach.",
        }
    )
    requirements["derived_design_drivers"].append(
        {
            "capability": "regeneration_fast_approach",
            "evidence": "The phase explicitly requires regeneration.",
            "related_function_ids": ["slide"],
        }
    )
    topology = _direct_topology(requirements, ["Cylinder"])
    topology["components"].append(
        {
            "id": "RegenCheck",
            "catalog_key": "GENERIC_CHECK_VALVE",
            "comp_type": "check_valve",
            "role": "Rod-to-cap regeneration path.",
            "function_id": "slide",
            "configuration": [],
            "selection_basis": "Required by regenerative speed realization.",
        }
    )
    topology["connections"].extend(
        [
            {"from_component": "DCV", "from_port": "A", "to_component": "Cylinder", "to_port": "Cap", "line": "work"},
            {"from_component": "Cylinder", "from_port": "Rod", "to_component": "RegenCheck", "to_port": "P", "line": "work"},
            {"from_component": "RegenCheck", "from_port": "A", "to_component": "Cylinder", "to_port": "Cap", "line": "work"},
            {"from_component": "DCV", "from_port": "B", "to_component": "Tank", "to_port": "R", "line": "return"},
        ]
    )
    motion, sync = canonical_decision_payload(requirements)
    topology["motion_control_decisions"] = motion
    topology["synchronization_decisions"] = sync
    topology["function_implementations"][0]["component_ids"].append("RegenCheck")
    report = validate_topology(topology, requirements)
    codes = {item.code for item in report.issues}
    assert "PHASE_EXHAUST_PATH_MISSING" not in codes
    assert "REGENERATIVE_RECIRCULATION_PATH_MISSING" not in codes
    assert report.verdict == "valid", [item.model_dump() for item in report.issues]


def test_lower_function_pressure_ceiling_requires_pressure_reduction() -> None:
    requirements = deepcopy(p1_requirements())
    requirements["global_constraints"]["max_system_pressure"] = {"value": 70, "unit": "bar"}
    requirements["functions"][0]["max_working_pressure"] = {"value": 40, "unit": "bar"}
    requirements["functions"][0]["branch_pressure_limit_required"] = True
    report = validate_topology(valid_topology(), requirements)
    assert "MISSING_BRANCH_PRESSURE_REDUCTION" in {item.code for item in report.issues}


def test_real_absent_catalog_class_still_blocks() -> None:
    topology = deepcopy(valid_topology())
    topology["catalog_gaps"] = [
        {
            "capability": "priority flow sharing",
            "needed_component_class": "priority flow-sharing valve",
            "reason": "No generic priority class exists in the supplied catalog.",
            "scope": "topology",
            "blocking": False,
        }
    ]
    assert "BLOCKING_TOPOLOGY_CATALOG_GAP" in {
        item.code for item in validate_topology(topology, p1_requirements()).issues
    }


def test_identical_invalid_topology_stops_instead_of_repeating_four_repairs() -> None:
    workflow = Workflow(settings=_settings(), agents=SimpleNamespace(), search_client=NoopSearch())
    route = workflow.route_after_topology_validation(
        {
            "topology_validation": {"verdict": "invalid", "repair_scope": "selection"},
            "topology_round": 2,
            "max_topology_rounds": 4,
            "topology_no_progress": True,
            "topology_validation_fingerprints": ["same", "same"],
        }
    )
    assert route == "finalize_topology"
