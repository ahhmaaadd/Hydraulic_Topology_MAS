from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent

from .catalog import CATALOG_TOOLS, catalog_type_reference
from .prompts import (
    COMPONENT_PLANNER_PROMPT,
    CRITIC_PROMPT,
    DESIGN_BRIEF_PROMPT,
    EXTRACTOR_PROMPT,
    NETLIST_BUILDER_PROMPT,
    RESEARCH_COVERAGE_PROMPT,
    RESEARCH_DISTILLER_PROMPT,
    RESEARCH_PLANNER_PROMPT,
    RESEARCH_SYNTHESIZER_PROMPT,
    TOPOLOGY_REVIEWER_PROMPT,
)
from .schemas import (
    ComponentPlanSet,
    CritiqueResult,
    DesignBrief,
    RequirementsSpec,
    ResearchCoverage,
    ResearchPlan,
    ResearchSynthesis,
    TaskFinding,
    TopologyDesign,
    TopologyReview,
)


@dataclass
class AgentSuite:
    extractor: Any
    critic: Any
    research_planner: Any
    research_distiller: Any
    research_coverage: Any
    research_synthesizer: Any
    design_brief: Any
    component_planner: Any
    netlist_builder: Any
    topology_reviewer: Any


def _structured(model: Any, schema: type[Any]):
    return model.with_structured_output(schema, method="json_schema").with_retry(stop_after_attempt=3)


def build_agent_suite(design_model: Any, fast_model: Any) -> AgentSuite:
    catalog_system_prompt = (
        COMPONENT_PLANNER_PROMPT
        + "\n\nTHE ONLY CATALOG TYPES AVAILABLE ARE:\n"
        + catalog_type_reference()
    )
    return AgentSuite(
        extractor=_structured(design_model, RequirementsSpec),
        critic=_structured(design_model, CritiqueResult),
        research_planner=_structured(design_model, ResearchPlan),
        research_distiller=_structured(design_model, TaskFinding),
        research_coverage=_structured(design_model, ResearchCoverage),
        research_synthesizer=_structured(design_model, ResearchSynthesis),
        design_brief=_structured(design_model, DesignBrief),
        component_planner=create_agent(
            model=design_model,
            tools=CATALOG_TOOLS,
            system_prompt=catalog_system_prompt,
            response_format=ComponentPlanSet,
        ),
        netlist_builder=_structured(design_model, TopologyDesign),
        topology_reviewer=_structured(design_model, TopologyReview),
    )


PROMPT_BY_AGENT = {
    "extractor": EXTRACTOR_PROMPT,
    "critic": CRITIC_PROMPT,
    "research_planner": RESEARCH_PLANNER_PROMPT,
    "research_distiller": RESEARCH_DISTILLER_PROMPT,
    "research_coverage": RESEARCH_COVERAGE_PROMPT,
    "research_synthesizer": RESEARCH_SYNTHESIZER_PROMPT,
    "design_brief": DESIGN_BRIEF_PROMPT,
    "netlist_builder": NETLIST_BUILDER_PROMPT,
    "topology_reviewer": TOPOLOGY_REVIEWER_PROMPT,
}
