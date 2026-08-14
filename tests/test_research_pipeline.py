from __future__ import annotations

from hydraulic_mas.requirements_quality import requirements_quality_issues
from hydraulic_mas.research_guards import calibrate_finding, derive_research_need_hints, enforce_minimum_plan
from hydraulic_mas.schemas import EvidenceClaim, ResearchPlan, ResearchTask, TaskFinding
from hydraulic_mas.search import TavilySearchClient, queries_are_similar


def _split_slide_requirements() -> dict:
    function = {
        "actuator_type": "linear",
        "orientation": "horizontal",
        "load_type": "resistive",
        "motion_phases": [],
        "holding": {},
    }
    return {
        "title": "Machine slide",
        "restated_problem": "One slide advances, feeds, and retracts.",
        "functions": [
            {**function, "id": "approach", "name": "Slide approach", "description": "The slide approaches."},
            {**function, "id": "feed", "name": "Slide feed", "description": "The slide cuts."},
            {**function, "id": "return", "name": "Slide return", "description": "The slide retracts."},
        ],
        "global_constraints": {},
        "operational_logic": {},
    }


def test_requirements_quality_detects_one_slide_split_into_phases() -> None:
    issues = requirements_quality_issues(
        _split_slide_requirements(),
        "The slide approaches, performs a cutting feed, and then the same slide retracts.",
    )
    assert any("one slide" in issue.casefold() for issue in issues)


def test_model_cannot_make_an_inferred_standard_critical() -> None:
    requirements = {
        "functions": [
            {
                "id": "slide",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "motion_phases": [{"name": "approach"}, {"name": "feed", "speed_load_independent": True}],
                "holding": {},
            }
        ],
        "operational_logic": {},
        "safety_requirements": [],
        "derived_design_drivers": [],
    }
    model_plan = ResearchPlan(
        knowledge_needs=[
            {
                "id": "standard_iso_4413",
                "decision": "ISO 4413 clause research",
                "why_needed": "generally applicable",
                "critical": True,
            }
        ],
        tasks=[
            ResearchTask(
                id="iso",
                category="standard",
                query="hydraulic ISO 4413 clauses",
                objective="Find clauses",
                need_ids=["standard_iso_4413"],
            )
        ],
    )
    guarded = enforce_minimum_plan(model_plan, requirements, max_initial_tasks=6)
    assert "standard_iso_4413" not in {need.id for need in guarded.knowledge_needs}
    assert len(guarded.tasks) <= 6
    assert {need.id for need in guarded.knowledge_needs} == {
        "system_power_and_relief",
        "function_slide_directional_control",
        "function_slide_motion_profile",
    }


def test_calibration_requires_matching_full_content_excerpt() -> None:
    finding = TaskFinding(
        task_id="task",
        need_ids=["function_slide_directional_control"],
        category="circuit_pattern",
        summary="A valve controls the cylinder.",
        evidence_claims=[
            EvidenceClaim(
                id="valid",
                claim="Connect P to the pump and A/B to the cylinder.",
                excerpt="Connect port P to the pump and ports A and B to the cylinder.",
                source_url="https://sunhydraulics.com/manual",
                claim_type="connection",
            ),
            EvidenceClaim(
                id="invented",
                claim="Unsupported claim.",
                excerpt="This text does not occur in the source.",
                source_url="https://sunhydraulics.com/manual",
                claim_type="connection",
            ),
        ],
    )
    calibrated = calibrate_finding(
        finding,
        [
            {
                "title": "Application manual",
                "url": "https://sunhydraulics.com/manual",
                "content": "Connect port P to the pump and ports A and B to the cylinder.",
                "source_kind": "manufacturer",
                "quality_score": 0.9,
                "is_full_content": True,
            }
        ],
    )
    assert [claim.id for claim in calibrated.evidence_claims] == ["valid"]
    assert calibrated.evidence_claims[0].verified
    assert calibrated.confidence == "high"


class _FakeTavily:
    def search(self, **_kwargs):
        return {
            "results": [
                {"title": "Solved homework", "url": "https://chegg.com/problem", "content": "weak", "score": 0.99},
                {"title": "Hydraulic circuit manual", "url": "https://sunhydraulics.com/manual", "content": "snippet", "score": 0.8},
                {"title": "University lecture", "url": "https://example.edu/hydraulics", "content": "snippet", "score": 0.7},
                {"title": "Circuit article", "url": "https://powermotiontech.com/circuit", "content": "snippet", "score": 0.9},
            ]
        }

    def extract(self, urls, **_kwargs):
        return {"results": [{"url": url, "raw_content": f"Full hydraulic evidence from {url}."} for url in urls]}


def test_search_ranks_extracts_and_deduplicates_sources(monkeypatch) -> None:
    monkeypatch.setattr("hydraulic_mas.search.TavilyClient", lambda api_key: _FakeTavily())
    client = TavilySearchClient("test", max_documents_per_search=2)

    first = client.search("hydraulic rapid feed circuit", max_results=6)
    second = client.search("hydraulic automatic feed circuit", max_results=6)

    first_urls = {item["url"] for item in first.results}
    second_urls = {item["url"] for item in second.results}
    assert first.extracted_count == 2
    assert first_urls.isdisjoint(second_urls)
    assert all(item["is_full_content"] for item in first.results)
    assert "https://chegg.com/problem" not in first_urls | second_urls
    assert any(item["reason"] == "low_quality_source" for item in first.rejected + second.rejected)


def test_semantic_query_duplicate_detection() -> None:
    assert queries_are_similar(
        "hydraulic rapid traverse slow feed circuit schematic",
        "hydraulic circuit schematic for rapid traverse and slow feed",
    )


def test_explicit_standard_remains_a_critical_need() -> None:
    requirements = {
        "functions": [],
        "operational_logic": {},
        "derived_design_drivers": [],
        "safety_requirements": [
            {
                "id": "iso",
                "category": "standard_compliance",
                "description": "Comply with ISO 4413.",
                "standard": "ISO 4413",
                "source": "explicit",
            }
        ],
    }
    needs = derive_research_need_hints(requirements)
    standard = next(need for need in needs if need.id.startswith("standard_"))
    assert standard.critical
    assert "Explicit" in (standard.critical_reason or "")


def test_p7_01_corrected_requirements_produce_four_research_decisions() -> None:
    requirements = {
        "functions": [
            {
                "id": "slide",
                "physical_actuator_id": "slide_cylinder",
                "actuator_type": "linear",
                "orientation": "horizontal",
                "load_type": "resistive",
                "motion_phases": [
                    {"name": "rapid_approach"},
                    {"name": "cutting_feed", "speed_adjustable": True, "speed_load_independent": True},
                    {"name": "retraction"},
                ],
                "holding": {},
            }
        ],
        "operational_logic": {
            "sequence": [
                {"order": 1, "description": "Approach", "function_ids": ["slide"]},
                {"order": 2, "description": "Feed", "function_ids": ["slide"]},
                {"order": 3, "description": "Retract", "function_ids": ["slide"]},
            ]
        },
        "safety_requirements": [],
        "derived_design_drivers": [
            {
                "capability": "pressure_compensation_load_independence",
                "evidence": "Cutting load variation must not change feed speed.",
                "related_function_ids": ["slide"],
            }
        ],
    }
    needs = derive_research_need_hints(requirements)
    assert [need.id for need in needs] == [
        "system_power_and_relief",
        "function_slide_directional_control",
        "function_slide_motion_profile",
        "driver_pressure_compensation_load_independence",
    ]
