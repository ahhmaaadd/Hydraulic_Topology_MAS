"""Typed contracts for the workflow.

The requirements, research, and topology contracts are adapted from
``Paper_2_Current/schema.py`` at commit d33920a.  Sizing and simulation models
are intentionally omitted because this project stops after topology validation.
The topology contract is tightened so every selected component has one exact
generic class key and every catalog port has an explicit disposition. Sizing,
manufacturer and procurement fields are intentionally absent.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class MotionDirection(str, Enum):
    extend = "extend"
    retract = "retract"
    hold = "hold"
    unspecified = "unspecified"


class MeteringSide(str, Enum):
    none = "none"
    meter_in = "meter_in"
    meter_out = "meter_out"
    undecided = "undecided"


class ActuatorChamber(str, Enum):
    cap = "cap"
    rod = "rod"
    none = "none"
    unspecified = "unspecified"


class MeteredFlow(str, Enum):
    supply = "supply"
    exhaust = "exhaust"
    none = "none"
    unspecified = "unspecified"


class FlowCompensation(str, Enum):
    none = "none"
    non_compensated = "non_compensated"
    pressure_compensated = "pressure_compensated"
    unspecified = "unspecified"


class LoadControlStrategy(str, Enum):
    none = "none"
    pilot_check = "pilot_check"
    counterbalance = "counterbalance"
    unspecified = "unspecified"


class SynchronizationStrategy(str, Enum):
    none = "none"
    rigid_platen_parallel = "rigid_platen_parallel"
    hydraulic_series = "hydraulic_series"
    flow_divider_parallel = "flow_divider_parallel"
    unspecified = "unspecified"


class SequenceTrigger(str, Enum):
    initial_command = "initial_command"
    external_command = "external_command"
    pressure = "pressure"
    position = "position"
    mechanical_coupling = "mechanical_coupling"
    simultaneous = "simultaneous"
    completion_of_previous = "completion_of_previous"
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
    id: str = Field(..., description="Globally stable snake_case phase identifier.")
    name: str
    description: str | None = None
    motion: MotionDirection
    distance: Quantity | None = None
    speed: Quantity | None = None
    force: Quantity | None = None
    duration: Quantity | None = None
    load_type: LoadType = LoadType.unspecified
    speed_adjustable: bool = False
    speed_load_independent: bool = False
    metering_side: MeteringSide
    metered_chamber: ActuatorChamber
    metered_flow: MeteredFlow
    flow_compensation: FlowCompensation
    load_control: LoadControlStrategy = LoadControlStrategy.none
    motion_control_justification: str = Field(
        ...,
        description="Concise auditable basis for the metering and load-control decision.",
    )
    motion_control_source: Source = Source.inferred

    @model_validator(mode="after")
    def validate_motion_control_fields(self) -> "MotionPhase":
        if self.metering_side == MeteringSide.none:
            if self.metered_chamber != ActuatorChamber.none or self.metered_flow != MeteredFlow.none:
                raise ValueError("metering_side=none requires metered_chamber=none and metered_flow=none")
            if self.flow_compensation != FlowCompensation.none:
                raise ValueError("metering_side=none requires flow_compensation=none")
        elif self.metering_side == MeteringSide.meter_in:
            if self.metered_flow != MeteredFlow.supply:
                raise ValueError("meter_in requires metered_flow=supply")
        elif self.metering_side == MeteringSide.meter_out:
            if self.metered_flow != MeteredFlow.exhaust:
                raise ValueError("meter_out requires metered_flow=exhaust")
        return self


class MotionControlDecision(BaseModel):
    function_id: str
    phase_id: str
    phase_name: str
    motion: MotionDirection
    load_type: LoadType
    metering_side: MeteringSide
    metered_chamber: ActuatorChamber
    metered_flow: MeteredFlow
    flow_compensation: FlowCompensation
    load_control: LoadControlStrategy
    justification: str
    source: Source


class SynchronizationDecision(BaseModel):
    required: bool = False
    actuator_count: int = Field(1, ge=1)
    strategy: SynchronizationStrategy = SynchronizationStrategy.none
    series_displacement_compatibility: Literal[
        "not_applicable",
        "explicitly_matched",
        "required_in_sizing",
        "unknown",
    ] = "not_applicable"
    justification: str = "No synchronization requirement."
    source: Source = Source.inferred


class FunctionSynchronizationDecision(SynchronizationDecision):
    function_id: str


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
    physical_actuator_id: str | None = Field(
        None,
        description="Stable id for the physical actuator; all phases of one actuator stay in this function.",
    )
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
    synchronization: SynchronizationDecision = Field(default_factory=SynchronizationDecision)
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
    phase_id: str | None = None
    description: str
    function_ids: list[str] = Field(default_factory=list)
    predecessor_phase_id: str | None = None
    precondition: str | None = None
    trigger: SequenceTrigger = SequenceTrigger.unspecified
    hydraulically_enforced: bool = False
    forbidden_overlap_function_ids: list[str] = Field(default_factory=list)


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


class EvidenceClaim(BaseModel):
    id: str
    claim: str
    excerpt: str = Field(..., description="Short verbatim excerpt from the supplied source content.")
    source_url: str
    source_title: str | None = None
    source_kind: Literal["manufacturer", "standard", "textbook", "paper", "technical", "other"] = "other"
    claim_type: Literal["component", "connection", "operating_principle", "safety", "standard", "other"] = "other"
    verified: bool = False


class KnowledgeNeed(BaseModel):
    id: str
    decision: str = Field(..., description="The topology decision that research must support.")
    why_needed: str
    related_function_ids: list[str] = Field(default_factory=list)
    critical: bool = True
    critical_reason: str | None = None


class ResearchTask(BaseModel):
    id: str
    category: ResearchCategory
    query: str
    objective: str
    need_ids: list[str] = Field(default_factory=list)
    related_function_ids: list[str] = Field(default_factory=list)
    action: Literal["search", "extract"] = "search"
    target_urls: list[str] = Field(default_factory=list)
    source_preference: str | None = None
    round_number: int = 1


class ResearchPlan(BaseModel):
    rationale: str | None = None
    knowledge_needs: list[KnowledgeNeed]
    tasks: list[ResearchTask]


class SearchResult(BaseModel):
    title: str | None = None
    url: str
    content: str = ""
    score: float | None = None
    source_kind: Literal["manufacturer", "standard", "textbook", "paper", "technical", "other"] = "other"
    quality_score: float = 0.0
    is_full_content: bool = False
    selection_reason: str | None = None


class SearchRecord(BaseModel):
    task_id: str
    query: str
    round_number: int = 1
    results: list[SearchResult] = Field(default_factory=list)
    error: str | None = None
    candidate_count: int = 0
    duplicate_count: int = 0
    selected_count: int = 0
    extracted_count: int = 0
    rejected_results: list[dict[str, str]] = Field(default_factory=list)


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
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list)
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
    motion_control_decisions: list[MotionControlDecision] = Field(default_factory=list)
    synchronization: SynchronizationDecision = Field(default_factory=SynchronizationDecision)
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
    scope: Literal["topology", "sizing"] = "topology"
    blocking: bool = True


class PlannedComponent(BaseModel):
    id: str
    catalog_key: str = Field(..., description="Exact generic component-class key returned by a catalog tool.")
    comp_type: str
    role: str
    function_id: str | None = None
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str = Field(..., description="Functional topology reason for selecting this class.")


class ConnectionIntent(BaseModel):
    description: str
    from_hint: str
    to_hint: str
    line: str = "unspecified"


class RepairAction(BaseModel):
    action: Literal[
        "add_component",
        "delete_component",
        "replace_component",
        "add_connection",
        "delete_connection",
        "replace_connection",
    ]
    issue_codes: list[str] = Field(default_factory=list)
    rationale: str
    target_component_id: str | None = None
    component: PlannedComponent | None = None
    target_connection: ConnectionIntent | None = Field(
        None,
        description="Existing connection to delete or replace; not used by add_connection.",
    )
    replacement_connection: ConnectionIntent | None = Field(
        None,
        description="New connection required by add_connection and replace_connection.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_connection_payload(cls, value):
        """Recover the common add-connection field mix-up before validation.

        The unified action schema exposes both target and replacement fields.
        Models therefore sometimes put a newly added connection in
        ``target_connection`` (or an intuitive extra ``connection`` field).
        For an add operation those values are unambiguously the new connection.
        """
        if not isinstance(value, dict) or value.get("action") != "add_connection":
            return value
        if value.get("replacement_connection") is not None:
            return value
        replacement = value.get("connection") or value.get("target_connection")
        if replacement is None:
            return value
        normalized = dict(value)
        normalized["replacement_connection"] = replacement
        normalized["target_connection"] = None
        return normalized

    @model_validator(mode="after")
    def validate_action_payload(self) -> "RepairAction":
        if self.action == "add_component" and self.component is None:
            raise ValueError("add_component requires component")
        if self.action == "delete_component" and not self.target_component_id:
            raise ValueError("delete_component requires target_component_id")
        if self.action == "replace_component" and (not self.target_component_id or self.component is None):
            raise ValueError("replace_component requires target_component_id and component")
        if self.action == "add_connection" and self.replacement_connection is None:
            raise ValueError("add_connection requires replacement_connection")
        if self.action == "delete_connection" and self.target_connection is None:
            raise ValueError("delete_connection requires target_connection")
        if self.action == "replace_connection" and (
            self.target_connection is None or self.replacement_connection is None
        ):
            raise ValueError("replace_connection requires target_connection and replacement_connection")
        return self


class LedgerEntry(BaseModel):
    function_id: str | None = None
    component_id: str | None = None
    realized_by: list[str] = Field(default_factory=list)
    how: str | None = None
    reason: str | None = None


class ComponentPlan(BaseModel):
    candidate_id: str = "candidate_1"
    approach: str = "minimal conventional topology"
    planning_summary: str
    engineering_decision_log: list[str] = Field(
        default_factory=list,
        description="Concise, auditable design decisions; not hidden chain-of-thought.",
    )
    components: list[PlannedComponent]
    connection_intent: list[ConnectionIntent]
    motion_control_decisions: list[MotionControlDecision] = Field(default_factory=list)
    synchronization_decisions: list[FunctionSynchronizationDecision] = Field(default_factory=list)
    design_ledger: list[LedgerEntry] = Field(default_factory=list)
    catalog_gaps: list[CatalogGap] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    repair_actions: list[RepairAction] = Field(default_factory=list)


class ComponentPlanSet(BaseModel):
    candidates: list[ComponentPlan] = Field(..., min_length=2, max_length=3)
    preferred_candidate_id: str
    comparison_summary: str


class CandidateEvaluation(BaseModel):
    candidate_id: str
    eligible: bool
    score: float
    component_count: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    selected: bool = False


class ComponentPlannerRecovery(BaseModel):
    attempt: int = Field(..., ge=1)
    error_type: str
    error: str
    outcome: Literal["retry"] = "retry"


class TopologyComponent(BaseModel):
    id: str
    catalog_key: str
    comp_type: str
    role: str
    function_id: str | None = None
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str


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


class ComponentStateSelection(BaseModel):
    component_id: str
    state: str
    reason: str | None = None


class PhaseConfiguration(BaseModel):
    phase_id: str
    function_id: str
    motion: MotionDirection
    component_states: list[ComponentStateSelection] = Field(default_factory=list)
    expected_active_function_ids: list[str] = Field(default_factory=list)
    forbidden_active_function_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class TopologyDesign(BaseModel):
    design_narrative: str
    components: list[TopologyComponent]
    connections: list[Connection]
    motion_control_decisions: list[MotionControlDecision] = Field(default_factory=list)
    synchronization_decisions: list[FunctionSynchronizationDecision] = Field(default_factory=list)
    phase_configurations: list[PhaseConfiguration] = Field(default_factory=list)
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
    repair_scope: Literal["none", "wiring", "selection", "research"]
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
    configuration: list[str] = Field(default_factory=list)
    selection_basis: str


class ResearchAudit(BaseModel):
    searches_used: int = 0
    rounds_used: int = 0
    selected_unique_sources: int = 0
    extracted_documents: int = 0
    duplicate_urls_skipped: int = 0
    verified_evidence_claims: int = 0
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)


class FinalTopologyOutput(BaseModel):
    problem_id: str
    title: str
    status: Literal["validated", "validated_with_warnings", "unresolved"]
    scope_statement: str
    design_narrative: str
    selected_components: list[FinalComponent]
    connections: list[Connection]
    motion_control_decisions: list[MotionControlDecision] = Field(default_factory=list)
    synchronization_decisions: list[FunctionSynchronizationDecision] = Field(default_factory=list)
    phase_configurations: list[PhaseConfiguration] = Field(default_factory=list)
    candidate_evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    component_planner_recovery: list[ComponentPlannerRecovery] = Field(default_factory=list)
    repair_history: list[RepairAction] = Field(default_factory=list)
    external_interfaces: list[ExternalInterface] = Field(default_factory=list)
    port_terminations: list[PortTermination] = Field(default_factory=list)
    function_implementations: list[FunctionImplementation] = Field(default_factory=list)
    design_decisions: list[DesignDecision] = Field(default_factory=list)
    catalog_gaps: list[CatalogGap] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    research_audit: ResearchAudit = Field(default_factory=ResearchAudit)
    research_coverage: ResearchCoverage
    validation: CombinedTopologyValidation
