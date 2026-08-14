from __future__ import annotations

from types import SimpleNamespace

from hydraulic_mas.config import Settings
from hydraulic_mas.graph import build_graph


class NoopSearch:
    def search(self, query: str, *, max_results: int):
        return []


def test_graph_compiles_with_injected_dependencies() -> None:
    settings = Settings(
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
    dummy_agents = SimpleNamespace()
    graph = build_graph(settings=settings, agents=dummy_agents, search_client=NoopSearch())
    nodes = set(graph.get_graph().nodes)
    assert {"plan_research", "web_research_worker", "assess_research_coverage", "plan_components", "validate_topology"} <= nodes

