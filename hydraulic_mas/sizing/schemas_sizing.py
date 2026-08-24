"""Typed contracts for the LLM sizing stage.

The model's job here is engineering *intent*, not arithmetic. It decides which
phase governs a dimension, what pressure to design against, whether a rod is
chosen for force or for area ratio, and how much margin to carry. Every number
that follows from those decisions is computed by a tool, so a design can be wrong
about strategy but never wrong about multiplication.

That split is deliberate and is what the ablation measures: the deterministic
planner in ``planner.py`` makes the same class of decisions with fixed policy
constants, so the difference between the two arms is exactly the value of the
model's judgement.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ActuatorDecision(BaseModel):
    """How one cylinder is to be dimensioned."""

    cylinder_id: str
    design_pressure_bar: float = Field(
        ...,
        description=(
            "Pressure to size the piston area against. Must leave room under the "
            "applicable ceiling for line losses and the relief margin."
        ),
    )
    governing_phase_id: str = Field(..., description="The phase whose load sets the area.")
    rod_strategy: Literal["force", "area_ratio"] = Field(
        ...,
        description=(
            "force: pick a conventional rod proportion. area_ratio: pick the rod to "
            "hit a cap:annulus ratio, which is what a load-actuated meter-out "
            "circuit needs because the ratio governs the achievable speed swing."
        ),
    )
    target_area_ratio: float | None = Field(
        None, description="Required when rod_strategy is area_ratio."
    )
    justification: str


class SupplyDecision(BaseModel):
    """How the power unit is to be dimensioned."""

    governing_phase_id: str = Field(..., description="Phase that sets the delivered flow.")
    relief_margin: float = Field(
        1.12, ge=1.0, le=1.6,
        description="Relief setting as a multiple of the worst working pressure.",
    )
    speed_aim_off: float = Field(
        0.06, ge=0.0, le=0.30,
        description=(
            "Fraction by which to overshoot a one-sided speed bound. Aiming exactly "
            "at a bound leaves the achievable band straddling it once efficiency "
            "and load spread are admitted."
        ),
    )
    justification: str


class SettingDecision(BaseModel):
    """A pressure setting for one valve."""

    component_id: str
    setting_bar: float
    basis: str = Field(..., description="Why this value, relative to what it must sit between.")


class SizingCandidate(BaseModel):
    candidate_id: str
    approach: str = Field(..., description="One line naming the strategy this candidate follows.")
    actuator_decisions: list[ActuatorDecision]
    supply_decision: SupplyDecision
    setting_decisions: list[SettingDecision] = Field(default_factory=list)
    engineering_notes: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)


class SizingPlanSet(BaseModel):
    """Two or three genuinely different sizing strategies, to be scored."""

    candidates: list[SizingCandidate] = Field(..., min_length=2, max_length=3)
    preferred_candidate_id: str
    comparison_summary: str


class SizingRepairPlan(BaseModel):
    """A revised candidate after verification refuted something."""

    candidate: SizingCandidate
    addressed_criteria: list[str] = Field(
        default_factory=list, description="Criterion ids this revision is meant to fix."
    )
    rationale: str


class SizingCandidateScore(BaseModel):
    candidate_id: str
    eligible: bool
    score: float
    proved: int = 0
    undecided: int = 0
    refuted: int = 0
    oversizing_index: float = 0.0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    selected: bool = False
