"""Typed contracts for the workflow.

The requirements, research, and topology contracts are adapted from
``Paper_2_Current/schema.py`` at commit d33920a.  Sizing and simulation models
are intentionally omitted because this project stops after topology validation.
The topology contract is tightened so every selected component has one exact
catalog key and every catalog port has an explicit disposition.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Source(str, Enum):
    explicit = "explicit"
    inferred = "inferred"
    assumed = "assumed"


class ActuatorType(str, Enum):
    linear = "linear"
    rotary = "rotary"
    unspecified = "unspecified"


class Orientation(str, Enum):
    horizontal = "horizontal"
    vertical = "vertical"
    inclined = "inclined"
    rotary = "rotary"
    unspecified = "unspecified"


class LoadType(str, Enum):
    resistive = "resistive"
    overrunning = "overrunning"
    both = "both"
    varies = "varies"
    unspecified = "unspecified"


class Comparator(str, Enum):
    le = "<="
    ge = ">="
    eq = "=="
    lt = "<"
    gt = ">"
    approx = "approx"
    range = "range"


class Quantity(BaseModel):
    value: float | None = Field(None, description="Numeric value in the canonical unit.")
    unit: str = Field("", description="Canonical SI-derived unit used by the workflow.")
    raw_text: str | None = None
    qualifier: str | None = None
    source: Source = Source.explicit
    notes: str | None = None


class MotionPhase(BaseModel):
    name: str
    description: str | None = None
    distance: Quantity | None = None
    speed: Quantity | None = None
    force: Quantity | None = None
    duration: Quantity | None = None
    load_type: LoadType = LoadType.unspecified


class HoldingRequirement(BaseModel):
    must_hold_position: bool = False
    no_creep: bool = False
    drift_tolerance: Quantity | None = None
    hold_on_power_loss: bool = False
    hold_duration: Quantity | None = None
    retain_on_hose_burst: bool = False
    notes: str | None = None


class FunctionRequirement(BaseModel):
    id: str = Field(..., description="Stable snake_case identifier.")
    name: str
    description: str
    actuator_type: ActuatorType = ActuatorType.unspecified
    orientation: Orientation = Orientation.unspecified
    primary_load: str | None = None
    load_type: LoadType = LoadType.unspecified
    motion_phases: list[MotionPhase] = Field(default_factory=list)
    total_travel: Quantity | None = None
    peak_force: Quantity | None = None
    holding_force: Quantity | None = None
    speeds_adjustable: bool = False
    speed_load_independent: bool = False
    cycle_time: Quantity | None = None
    duty_cycle: Quantity | None = None
    holding: HoldingRequirement = Field(default_factory=HoldingRequirement)
    special_modes: list[str] = Field(default_factory=list)
    notes: str | None = None


class GlobalConstraints(BaseModel):
    max_system_pressure: Quantity | None = None
    power_source: Literal["electric_motor", "ic_engine", "pto", "unspecified"] = "unspecified"
    installation_environment: Literal["stationary_industrial", "mobile", "unspecified"] = "unspecified"
    pump_count_preference: str | None = None
    fluid_cleanliness: str | None = None
    energy_efficiency_intent: str | None = None
    simplicity_intent: str | None = None
    other_constraints: list[str] = Field(default_factory=list)


class SequenceStep(BaseModel):
    order: int
    description: str
    function_ids: list[str] = Field(default_factory=list)
    precondition: str | None = None


class OperationalLogic(BaseModel):
    trigger: str | None = None
    modes: list[str] = Field(default_factory=list)
    sequence: list[SequenceStep] = Field(default_factory=list)
    interlocks: list[str] = Field(default_factory=list)
    notes: str | None = None


class SafetyRequirement(BaseModel):
    id: str
    category: Literal[
        "overpressure",
        "power_loss_hold",
        "hose_burst",
        "emergency_stop",
        "emergency_manual_operation",
        "uncommanded_motion",
        "standard_compliance",
        "other",
    ]
    description: str
    related_function_ids: list[str] = Field(default_factory=list)
    parameter: Quantity | None = None
    standard: str | None = None
    source: Source = Source.explicit


class DerivedDesignDriver(BaseModel):
    capability: Literal[
        "load_holding",
        "counterbalance_overrunning",
        "regeneration_fast_approach",
        "energy_storage_peak_flow",
        "synchronization",
        "pressure_compensation_load_independence",
        "flow_priority_sharing",
        "two_speed_force_switching",
        "pressure_limiting_stall",
    ]
    evidence: str
    related_function_ids: list[str] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    related_function_id: str | None = None
    metric: str
    comparator: Comparator
    target: Quantity
    source: Source = Source.explicit


class Assumption(BaseModel):
    id: str
    statement: str
    rationale: str
    affects: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    needs_human_approval: bool = False


class Clarification(BaseModel):
    id: str
    question: str
    why_it_matters: str
    blocking: bool
    related: list[str] = Field(default_factory=list)


class RequirementsSpec(BaseModel):
    title: str
    restated_problem: str
    application: str | None = None
    functions: list[FunctionRequirement]
    global_constraints: GlobalConstraints
    operational_logic: OperationalLogic = Field(default_factory=OperationalLogic)
    safety_requirements: list[SafetyRequirement] = Field(default_factory=list)
    derived_design_drivers: list[DerivedDesignDriver] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[Clarification] = Field(default_factory=list)


class CritiqueResult(BaseModel):
    completeness_status: Literal["sufficient", "proceed_with_assumptions", "needs_clarification"]
    rationale: str
    blocking_questions: list[Clarification] = Field(default_factory=list)
    proposed_assumptions: list[Assumption] = Field(default_factory=list)
    consistency_issues: list[str] = Field(default_factory=list)
    physical_sanity_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Research contracts
# ---------------------------------------------------------------------------

ResearchCategory = Literal[
    "component_class",
    "circuit_pattern",
    "control_sequence",
    "safety",
    "standard",
    "general",
]


class Citation(BaseModel):
    title: str | None = None
    url: str
    source_kind: Literal["manufacturer", "standard", "textbook", "paper", "technical", "other"] = "other"


class KnowledgeNeed(BaseModel):
    id: str
    decision: str = Field(..., description="The topology decision that research must support.")
    why_needed: str
    related_function_ids: list[str] = Field(default_factory=list)
    critical: bool = True


class ResearchTask(BaseModel):
    id: str
    category: ResearchCategory
    query: str
    objective: str
    need_ids: list[str] = Field(default_factory=list)
    related_function_ids: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    rationale: str | None = None
    knowledge_needs: list[KnowledgeNeed]
    tasks: list[ResearchTask]


class SearchResult(BaseModel):
    title: str | None = None
    url: str
    content: str = ""
    score: float | None = None


class SearchRecord(BaseModel):
    task_id: str
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    error: str | None = None


class TaskFinding(BaseModel):
    task_id: str
    need_ids: list[str] = Field(default_factory=list)
    category: ResearchCategory
    summary: str
    key_points: list[str] = Field(default_factory=list)
    component_candidates: list[str] = Field(default_factory=list)
    connection_guidance: list[str] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    source_quality_notes: str | None = None


class CoverageItem(BaseModel):
    need_id: str
    status: Literal["covered", "partial", "missing"]
    rationale: str
    evidence_task_ids: list[str] = Field(default_factory=list)


class ResearchCoverage(BaseModel):
    status: Literal["sufficient", "needs_more", "budget_exhausted"]
    can_proceed: bool
    summary: str
    items: list[CoverageItem]
    missing_or_weak_information: list[str] = Field(default_factory=list)
    follow_up_tasks: list[ResearchTask] = Field(default_factory=list)


class ComponentClassEvidence(BaseModel):
    capability: str
    recommended_catalog_types: list[str]
    rationale: str
    related_function_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class CircuitPattern(BaseModel):
    name: str
    description: str
    required_catalog_types: list[str] = Field(default_factory=list)
    connection_skeleton: list[str] = Field(default_factory=list)
    applies_to: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class StandardRef(BaseModel):
    name: str
    relevance: str
    citations: list[Citation] = Field(default_factory=list)


class ResearchSynthesis(BaseModel):
    summary: str
    component_class_evidence: list[ComponentClassEvidence] = Field(default_factory=list)
    circuit_patterns: list[CircuitPattern] = Field(default_factory=list)
    standards: list[StandardRef] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Topology contracts
# ---------------------------------------------------------------------------


class FunctionBrief(BaseModel):
    function_id: str
    summary: str
    actuator: str
    load_type: str
    holding: str
    speed_control: str
    phase_change_or_sequence: str | None = None
    key_criteria: list[str] = Field(default_factory=list)


class DesignBrief(BaseModel):
    application: str
    simplicity_priority: bool = False
    hard_constraints: list[str] = Field(default_factory=list)
    safety_essentials: list[str] = Field(default_factory=list)
    design_directives: list[str] = Field(default_factory=list)
    functions: list[FunctionBrief]
    notes: str | None = None


class CatalogGap(BaseModel):
    capability: str
    needed_component_class: str
    reason: str
    related_function_ids: list[str] = Field(default_factory=list)
    chosen_workaround: str | None = None
    blocking: bool = False


class PlannedComponent(BaseModel):
    id: str
    catalog_key: str = Field(..., description="Exact key returned by a catalog tool.")
    comp_type: str
    role: str
    function_id: str | None = None
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str
    requires_sizing_verification: bool = True


class ConnectionIntent(BaseModel):
    description: str
    from_hint: str
    to_hint: str
    line: str = "unspecified"


class LedgerEntry(BaseModel):
    function_id: str | None = None
    component_id: str | None = None
    realized_by: list[str] = Field(default_factory=list)
    how: str | None = None
    reason: str | None = None


class ComponentPlan(BaseModel):
    planning_summary: str
    engineering_decision_log: list[str] = Field(
        default_factory=list,
        description="Concise, auditable design decisions; not hidden chain-of-thought.",
    )
    components: list[PlannedComponent]
    connection_intent: list[ConnectionIntent]
    design_ledger: list[LedgerEntry] = Field(default_factory=list)
    catalog_gaps: list[CatalogGap] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)


class TopologyComponent(BaseModel):
    id: str
    catalog_key: str
    comp_type: str
    role: str
    function_id: str | None = None
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str
    requires_sizing_verification: bool = True


class Connection(BaseModel):
    from_component: str
    from_port: str
    to_component: str
    to_port: str
    line: Literal[
        "pressure",
        "return",
        "work",
        "pilot",
        "suction",
        "drain",
        "mechanical",
        "electrical",
        "signal",
        "vent",
        "unspecified",
    ] = "unspecified"
    notes: str | None = None


class ExternalInterface(BaseModel):
    component_id: str
    port: str
    external_system: str
    domain: Literal["electrical", "mechanical", "signal", "atmosphere", "other"]
    notes: str | None = None


class PortTermination(BaseModel):
    component_id: str
    port: str
    disposition: Literal["capped", "internally_blocked", "not_used"]
    reason: str


class FunctionImplementation(BaseModel):
    function_id: str
    component_ids: list[str]
    circuit_pattern: str | None = None
    how_requirements_met: str


class DesignDecision(BaseModel):
    decision: str
    rationale: str
    driven_by: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)


class TopologyDesign(BaseModel):
    design_narrative: str
    components: list[TopologyComponent]
    connections: list[Connection]
    external_interfaces: list[ExternalInterface] = Field(default_factory=list)
    port_terminations: list[PortTermination] = Field(default_factory=list)
    function_implementations: list[FunctionImplementation] = Field(default_factory=list)
    design_decisions: list[DesignDecision] = Field(default_factory=list)
    catalog_gaps: list[CatalogGap] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)


class TopologyIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    scope: Literal["selection", "wiring", "safety", "requirements", "research"]
    description: str
    related: list[str] = Field(default_factory=list)


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    details: str


class DeterministicValidation(BaseModel):
    verdict: Literal["valid", "invalid"]
    issues: list[TopologyIssue] = Field(default_factory=list)
    checks: list[ValidationCheck] = Field(default_factory=list)


class TopologyReview(BaseModel):
    verdict: Literal["valid", "invalid"]
    design_issues: list[TopologyIssue] = Field(default_factory=list)
    summary: str


class CombinedTopologyValidation(BaseModel):
    verdict: Literal["valid", "invalid"]
    deterministic: DeterministicValidation
    design_review: TopologyReview
    repair_scope: Literal["none", "wiring", "selection"]
    topology_round: int
    summary: str


class FinalComponent(BaseModel):
    id: str
    catalog_key: str
    name: str
    comp_type: str
    role: str
    function_id: str | None = None
    ports: list[str]
    manufacturer: str | None = None
    part_number: str | None = None
    source_url: str | None = None
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str
    requires_sizing_verification: bool = True


class FinalTopologyOutput(BaseModel):
    problem_id: str
    title: str
    status: Literal["validated", "validated_with_warnings", "unresolved"]
    scope_statement: str
    design_narrative: str
    selected_components: list[FinalComponent]
    connections: list[Connection]
    external_interfaces: list[ExternalInterface] = Field(default_factory=list)
    port_terminations: list[PortTermination] = Field(default_factory=list)
    function_implementations: list[FunctionImplementation] = Field(default_factory=list)
    design_decisions: list[DesignDecision] = Field(default_factory=list)
    catalog_gaps: list[CatalogGap] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    research_coverage: ResearchCoverage
    validation: CombinedTopologyValidation

