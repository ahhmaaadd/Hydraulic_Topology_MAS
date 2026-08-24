"""Calculator tools for the sizing planner.

The model calls these instead of doing arithmetic. That is not a stylistic
preference: the reference audit's defects were unit and convention errors -
hydraulic power quoted as shaft power, force divided by area without the
mechanical efficiency - and those are precisely the mistakes a language model
makes fluently and confidently. Moving every calculation behind a tool makes
them impossible rather than unlikely.

``evaluate_sizing_policy`` is the important one. It lets the planner try a policy
and read back the certificate before committing to it, which turns the model from
a guesser into a searcher.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from .apply import apply_candidate, oversizing_index
from .catalog_sizing import (
    BORE_SERIES,
    DISPLACEMENT_SERIES,
    MOTOR_SERIES,
    NoStandardSizeError,
    ROD_SERIES,
    select_bore,
    select_displacement,
    select_motor,
    select_rod,
    select_rod_for_ratio,
    select_valve_size,
)
from .contract import AcceptanceContract, compile_contract
from .quantities import Q, annulus_area, area_of_bore
from .schemas_sizing import SizingCandidate


def build_sizing_tools(topology: dict, requirements: dict, contract: AcceptanceContract,
                       load_tolerance: float = 0.0) -> list[Any]:
    """Tools bound to one problem, so the model can test against the real circuit."""

    @tool
    def list_phases() -> str:
        """List every motion phase with its load, speed, metering and pressure ceiling.

        Use this first. It is the authoritative statement of what has to be met.
        """
        rows = []
        ceilings = {
            criterion.function_id: criterion.target.to("bar")
            for criterion in contract.of_kind("pressure_ceiling")
        }
        for function in requirements.get("functions", []):
            function_id = str(function.get("id"))
            for phase in function.get("motion_phases") or []:
                rows.append({
                    "function_id": function_id,
                    "phase_id": str(phase.get("id")),
                    "motion": phase.get("motion"),
                    "force": phase.get("force"),
                    "speed": phase.get("speed"),
                    "distance": phase.get("distance"),
                    "speed_realization": phase.get("speed_realization"),
                    "metering_side": phase.get("metering_side"),
                    "pressure_ceiling_bar": ceilings.get(function_id, ceilings.get(None)),
                })
        return json.dumps(rows, indent=1, default=str)

    @tool
    def list_actuators() -> str:
        """List the cylinders in the validated topology and the function each serves."""
        rows = [
            {
                "cylinder_id": str(component["id"]),
                "function_id": component.get("function_id"),
                "catalog_key": component.get("catalog_key"),
            }
            for component in topology.get("components", [])
            if component.get("comp_type") == "cylinder"
        ]
        return json.dumps(rows, indent=1)

    @tool
    def area_for_force(force_n: float, design_pressure_bar: float,
                       mechanical_efficiency: float = 0.90) -> str:
        """Piston area needed to develop a force at a pressure, in mm2.

        Applies the mechanical efficiency, which is the step the reference
        solutions omitted and the reason several of them exceed their ceilings.
        """
        area = force_n / (design_pressure_bar * 1e5 * mechanical_efficiency)
        return json.dumps({
            "required_area_mm2": round(area * 1e6, 1),
            "note": "includes the mechanical efficiency; F/(p*A*eta)",
        })

    @tool
    def choose_bore(required_area_mm2: float) -> str:
        """Smallest ISO 3320 preferred bore covering an area, with what it gives."""
        try:
            bore = select_bore(Q.of(required_area_mm2, "mm2"))
        except NoStandardSizeError as error:
            return json.dumps({"error": str(error), "series": list(BORE_SERIES)})
        return json.dumps({
            "bore_mm": bore,
            "cap_area_mm2": round(area_of_bore(bore).to("mm2"), 1),
            "note": "hand calculations often land on non-preferred bores such as 70 or 90 mm",
        })

    @tool
    def choose_rod(bore_mm: float, strategy: str = "force",
                   target_area_ratio: float | None = None) -> str:
        """Pick a rod. strategy is 'force' for a normal proportion, or 'area_ratio'.

        Use 'area_ratio' when one fixed orifice must serve two speeds in the same
        direction: the achievable speed swing is governed by the cap:annulus
        ratio, so choosing the rod for force alone can make the requirement
        unreachable no matter how the throttle is set.
        """
        try:
            rod = (select_rod_for_ratio(bore_mm, float(target_area_ratio))
                   if strategy == "area_ratio" and target_area_ratio else select_rod(bore_mm))
        except NoStandardSizeError as error:
            return json.dumps({"error": str(error), "series": list(ROD_SERIES)})
        cap, annulus = area_of_bore(bore_mm), annulus_area(bore_mm, rod)
        return json.dumps({
            "rod_mm": rod,
            "annulus_area_mm2": round(annulus.to("mm2"), 1),
            "area_ratio": round(cap.value / annulus.value, 3),
        })

    @tool
    def flow_for_speed(area_mm2: float, speed_m_per_min: float) -> str:
        """Flow in L/min needed to move an area at a speed."""
        flow = Q.of(area_mm2, "mm2").value * Q.of(speed_m_per_min, "m/min").value
        return json.dumps({"flow_l_per_min": round(Q(flow, (3, 0, -1)).to("l/min"), 3)})

    @tool
    def choose_pump(flow_l_per_min: float, speed_rpm: float = 1500.0,
                    volumetric_efficiency: float = 0.90) -> str:
        """Smallest standard displacement delivering a flow, and what it actually gives."""
        try:
            displacement = select_displacement(
                Q.of(flow_l_per_min, "l/min"), speed_rpm, volumetric_efficiency)
        except NoStandardSizeError as error:
            return json.dumps({"error": str(error), "series": list(DISPLACEMENT_SERIES)})
        delivered = displacement * speed_rpm * volumetric_efficiency / 1000.0
        return json.dumps({
            "displacement_cm3_per_rev": displacement,
            "delivered_l_per_min": round(delivered, 3),
            "note": "snapping up overshoots the target speed; check the fast phases",
        })

    @tool
    def choose_motor(flow_l_per_min: float, pressure_bar: float,
                     overall_efficiency: float = 0.85) -> str:
        """Standard motor rating for the SHAFT power at an operating point.

        The pump turns against its outlet pressure carrying its whole delivery,
        not just the share the actuator accepts. Sizing on the actuator's share
        is the hydraulic-power error that appears twice in the reference set.
        """
        shaft = (Q.of(flow_l_per_min, "l/min").value * pressure_bar * 1e5) / overall_efficiency
        try:
            rating = select_motor(Q(shaft, (2, 1, -3)))
        except NoStandardSizeError as error:
            return json.dumps({"error": str(error), "series": list(MOTOR_SERIES)})
        return json.dumps({
            "shaft_power_kw": round(shaft / 1000.0, 3),
            "motor_rating_kw": rating,
        })

    @tool
    def choose_valve(flow_l_per_min: float, margin: float = 1.25) -> str:
        """Nominal valve size for a flow, including the rating margin."""
        try:
            name, rated = select_valve_size(Q.of(flow_l_per_min, "l/min"), margin)
        except NoStandardSizeError as error:
            return json.dumps({"error": str(error)})
        return json.dumps({"valve_size": name, "rated_flow_l_per_min": rated})

    @tool
    def evaluate_sizing_policy(policy_json: str) -> str:
        """Apply a candidate policy and return its verification certificate.

        Pass one SizingCandidate as JSON. The circuit is solved phase by phase and
        every acceptance criterion is bounded over the operating envelope, so this
        reports what the design would actually do rather than what it was intended
        to do. Try a policy here before committing to it.
        """
        try:
            candidate = SizingCandidate.model_validate_json(policy_json)
        except Exception as error:  # noqa: BLE001 - surfaced to the model verbatim
            return json.dumps({"error": f"not a valid SizingCandidate: {error}"})
        try:
            sizing, _, certificate, problems = apply_candidate(
                candidate, topology, requirements, contract, load_tolerance=load_tolerance)
        except Exception as error:  # noqa: BLE001
            return json.dumps({"error": f"{type(error).__name__}: {error}"})
        return json.dumps({
            "verdict": certificate.verdict,
            "counts": certificate.counts(),
            "candidate_problems": problems,
            "oversizing_index": round(
                oversizing_index(sizing, contract, topology, requirements), 3),
            "not_proved": [
                {
                    "criterion": item.criterion_id,
                    "required": item.required,
                    "achieved": item.achieved,
                    "verdict": item.verdict,
                }
                for item in certificate.certificates if item.verdict != "PROVED"
            ],
            "findings": [
                {"layer": finding.layer, "code": finding.code, "message": finding.message}
                for finding in certificate.findings
            ],
            "operating_points": certificate.operating_points,
        }, indent=1, default=str)

    return [
        list_phases, list_actuators, area_for_force, choose_bore, choose_rod,
        flow_for_speed, choose_pump, choose_motor, choose_valve, evaluate_sizing_policy,
    ]
