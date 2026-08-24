"""The LLM sizing planner: a tool-using agent bound to one problem.

Built per problem rather than once, because the interesting tool - the one that
applies a policy and returns its certificate - has to be bound to the specific
topology and requirements being sized. That binding is what lets the model test a
design instead of guessing at one.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from .contract import AcceptanceContract
from .prompts_sizing import SIZING_PLANNER_PROMPT, SIZING_REPAIRER_PROMPT
from .schemas_sizing import SizingPlanSet, SizingRepairPlan
from .tools import build_sizing_tools


def build_sizing_planner(model: Any, topology: dict, requirements: dict,
                         contract: AcceptanceContract, load_tolerance: float = 0.0):
    return create_agent(
        model=model,
        tools=build_sizing_tools(topology, requirements, contract, load_tolerance),
        system_prompt=SIZING_PLANNER_PROMPT,
        response_format=ToolStrategy(
            SizingPlanSet,
            handle_errors=(
                "Return a valid SizingPlanSet with two or three candidates. Every "
                "actuator_decision needs a cylinder_id that exists, a design_pressure_bar "
                "and a governing_phase_id that names a real phase. Use rod_strategy="
                "'area_ratio' with a target_area_ratio only when one orifice must serve "
                "two speeds in the same direction."
            ),
        ),
    )


def build_sizing_repairer(model: Any, topology: dict, requirements: dict,
                          contract: AcceptanceContract, load_tolerance: float = 0.0):
    return create_agent(
        model=model,
        tools=build_sizing_tools(topology, requirements, contract, load_tolerance),
        system_prompt=SIZING_REPAIRER_PROMPT,
        response_format=ToolStrategy(
            SizingRepairPlan,
            handle_errors=(
                "Return a valid SizingRepairPlan containing one revised candidate, the "
                "criterion ids it addresses, and a rationale."
            ),
        ),
    )
