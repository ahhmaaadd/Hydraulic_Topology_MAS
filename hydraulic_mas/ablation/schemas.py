"""The output a *direct* sizing arm returns: numbers, not policy.

The A1 arm in ``sizing/schemas_sizing.py`` deliberately never asks the model for
a number - it asks for intent and computes everything from it. That is the
design being defended, so the arms it is defended against must be free of it.
Here the model states the finished design outright: this bore, this rod, this
displacement, this relief setting.

It is also asked what its design will *do* - the speed each phase will run at and
the force it will develop. Nothing depends on the answer; it exists so the same
verifier that judges the design can also measure how far the model's own
arithmetic was out. A pass rate says an arm failed. A signed error distribution
says why, and that is the claim the paper is actually making.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DirectCylinder(BaseModel):
    cylinder_id: str
    bore_mm: float = Field(..., description="Piston bore, from the ISO 3320 preferred series.")
    rod_mm: float = Field(..., description="Rod diameter, from the ISO 4395 preferred series.")


class DirectFlowControl(BaseModel):
    component_id: str
    phase_id: str = Field(..., description="The phase this setting is for.")
    set_flow_lpm: float = Field(
        ..., description="Flow the control is to pass in that phase, L/min.")


class DirectSetting(BaseModel):
    component_id: str
    setting_bar: float


class PhasePrediction(BaseModel):
    """What the arm believes its own design achieves.

    Not used to build anything. Compared against the verifier's computation to
    measure arithmetic error directly, rather than inferring it from a verdict.
    """

    phase_id: str
    predicted_velocity_m_min: float | None = None
    predicted_force_kn: float | None = None
    predicted_pressure_bar: float | None = None


class DirectSizing(BaseModel):
    """A complete sizing, stated as finished numbers."""

    cylinders: list[DirectCylinder]
    displacement_cm3: float = Field(
        ..., description="Pump displacement per revolution, from the standard series.")
    pump_speed_rpm: float = Field(1500.0, description="Prime mover speed.")
    relief_bar: float = Field(..., description="Relief valve setting.")
    motor_kw: float = Field(..., description="Prime mover rating, from the IEC series.")
    reservoir_l: float = Field(..., description="Reservoir volume.")
    valve_size: str = Field("NG6", description="Nominal size for the directional valves.")
    flow_controls: list[DirectFlowControl] = Field(default_factory=list)
    settings: list[DirectSetting] = Field(default_factory=list)
    predictions: list[PhasePrediction] = Field(default_factory=list)
    reasoning: str = Field("", description="Brief account of how the sizes were arrived at.")
