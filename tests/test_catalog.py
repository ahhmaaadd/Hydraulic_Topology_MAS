from __future__ import annotations

import json

from hydraulic_mas.catalog import CATALOG, component_types, get_component_details, list_component_types, search_catalog
from hydraulic_mas.data import component_catalog_problems_7 as catalog_data


FORBIDDEN_FIELDS = {
    "manufacturer",
    "part_number",
    "source_id",
    "source_url",
    "params",
    "equations",
    "pressure_rating_bar",
    "flow_rating_lpm",
}


def _all_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_keys(nested)


def test_catalog_is_small_generic_and_topology_only() -> None:
    assert 10 <= len(CATALOG) <= 25
    assert not hasattr(catalog_data, "PROBLEM_SOLUTIONS")
    assert not catalog_data.SOURCES
    assert {"pump", "tank", "relief_valve", "dcv", "cylinder"} <= set(component_types())
    assert set(component_types()).isdisjoint(catalog_data.FORBIDDEN_TOPOLOGY_TYPES)
    for key, entry in CATALOG.items():
        assert key.startswith("GENERIC_")
        assert entry["ports"]
        assert set(entry["ports"]) == set(entry["port_types"]), key
        assert not (FORBIDDEN_FIELDS & set(_all_keys(entry))), key


def test_catalog_tools_return_classes_without_sizing_or_procurement_fields() -> None:
    types_output = list_component_types.invoke({})
    pump_output = search_catalog.invoke({"query": "pump", "comp_type": "pump"})
    details_output = get_component_details.invoke({"catalog_key": "GENERIC_FIXED_DISPLACEMENT_PUMP"})
    assert isinstance(json.loads(types_output), list)
    assert "GENERIC_FIXED_DISPLACEMENT_PUMP" in pump_output
    details = json.loads(details_output)
    assert details["ports"] == ["S", "P"]
    assert not (FORBIDDEN_FIELDS & set(_all_keys(json.loads(types_output))))
    assert not (FORBIDDEN_FIELDS & set(_all_keys(json.loads(pump_output))))
    assert not (FORBIDDEN_FIELDS & set(_all_keys(details)))


def test_catalog_has_problem_one_functional_classes() -> None:
    assert {
        "GENERIC_TANK",
        "GENERIC_FIXED_DISPLACEMENT_PUMP",
        "GENERIC_RELIEF_VALVE",
        "GENERIC_4_3_SOLENOID_TANDEM_DCV",
        "GENERIC_DOUBLE_ACTING_CYLINDER",
        "GENERIC_PRESSURE_COMPENSATED_ONE_WAY_FLOW_CONTROL",
        "GENERIC_POSITION_OPERATED_BYPASS",
    } <= set(CATALOG)


def test_catalog_has_missing_behavioral_classes_without_workarounds() -> None:
    assert {
        "GENERIC_CHECK_VALVE",
        "GENERIC_EXTERNALLY_PILOTED_UNLOADING_VALVE",
        "GENERIC_COUNTERBALANCE_VALVE",
        "GENERIC_FLOW_DIVIDER_COMBINER",
        "GENERIC_PRESSURE_REDUCING_VALVE_WITH_REVERSE_CHECK",
        "GENERIC_EXTERNALLY_PILOTED_SEQUENCE_VALVE",
    } <= set(CATALOG)
    assert CATALOG["GENERIC_COUNTERBALANCE_VALVE"]["type"] == "counterbalance_valve"
    assert CATALOG["GENERIC_SINGLE_PILOT_OPERATED_CHECK"]["type"] == "single_pilot_check_valve"
    assert CATALOG["GENERIC_COUNTERBALANCE_VALVE"]["type"] != CATALOG[
        "GENERIC_SINGLE_PILOT_OPERATED_CHECK"
    ]["type"]
