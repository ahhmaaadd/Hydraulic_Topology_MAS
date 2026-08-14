from __future__ import annotations

import json

from hydraulic_mas.data import component_catalog_problems_7 as catalog_data
from hydraulic_mas.catalog import (
    CATALOG,
    component_types,
    get_component_details,
    list_component_types,
    search_catalog,
)


def test_supplied_catalog_shape() -> None:
    assert len(CATALOG) == 58
    assert not hasattr(catalog_data, "PROBLEM_SOLUTIONS")
    assert {"pump", "tank", "relief_valve", "dcv", "cylinder"} <= set(component_types())
    for key, entry in CATALOG.items():
        assert entry["ports"]
        assert set(entry["ports"]) == set(entry["port_types"]), key


def test_catalog_tools_return_exact_keys_and_never_solved_mappings() -> None:
    types_output = list_component_types.invoke({})
    pump_output = search_catalog.invoke({"query": "gear pump", "comp_type": "pump"})
    details_output = get_component_details.invoke({"catalog_key": "Danfoss_GearMe_GR1_3p2cc_1450"})
    assert isinstance(json.loads(types_output), list)
    assert "Danfoss_GearMe_GR1_3p2cc_1450" in pump_output
    assert json.loads(details_output)["ports"] == ["S", "P", "L"]
    combined = types_output + pump_output + details_output
    assert "PROBLEM_SOLUTIONS" not in combined
    assert "P7-01" not in combined

