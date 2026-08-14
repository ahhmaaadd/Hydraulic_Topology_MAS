"""Read-only catalog index and the tools available to the designer agent.

The supplied attachment also contained solved benchmark mappings. The packaged
data copy strips that section, and this module imports/exposes only ``CATALOG``
and ``SOURCES``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from langchain_core.tools import tool

from .data.component_catalog_problems_7 import CATALOG, SOURCES


PRESSURE_KEYS = ("P_max_bar", "WP_bar")
FLOW_KEYS = ("max_flow_lpm", "rated_flow_lpm", "design_flow_lpm", "flow_lpm")


def pressure_rating(entry: dict[str, Any]) -> float | None:
    params = entry.get("params", {})
    for key in PRESSURE_KEYS:
        value = params.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def flow_rating(entry: dict[str, Any]) -> float | None:
    params = entry.get("params", {})
    values = [params.get(key) for key in FLOW_KEYS]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return max(numeric) if numeric else None


def component_types() -> list[str]:
    return sorted({entry["type"] for entry in CATALOG.values()})


def catalog_entry(key: str) -> dict[str, Any]:
    if key not in CATALOG:
        raise KeyError(f"Unknown catalog key {key!r}")
    return CATALOG[key]


def source_for_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    source_id = entry.get("params", {}).get("source_id")
    value = SOURCES.get(source_id)
    return value if isinstance(value, dict) else None


def compact_entry(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    params = entry.get("params", {})
    source = source_for_entry(entry) or {}
    return {
        "catalog_key": key,
        "name": entry.get("name"),
        "type": entry.get("type"),
        "ports": entry.get("ports", []),
        "port_types": entry.get("port_types", {}),
        "summary": entry.get("summary"),
        "pressure_rating_bar": pressure_rating(entry),
        "flow_rating_lpm": flow_rating(entry),
        "manufacturer": params.get("manufacturer"),
        "part_number": params.get("part_number"),
        "selection_status": params.get("selection_status"),
        "source_id": params.get("source_id"),
        "source_url": source.get("url"),
        "key_params": {
            k: v
            for k, v in params.items()
            if k
            in {
                "P_max_bar",
                "WP_bar",
                "max_flow_lpm",
                "rated_flow_lpm",
                "design_flow_lpm",
                "flow_lpm",
                "displacement_cc_rev",
                "bore_mm",
                "rod_mm",
                "stroke_mm",
                "rated_power_kW",
                "spool_center",
                "setting_range_bar",
                "ratio",
            }
        },
    }


@tool
def list_component_types() -> str:
    """List every component type available in the design catalog, counts, and exact port variants."""
    counts = Counter(entry["type"] for entry in CATALOG.values())
    ports: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for entry in CATALOG.values():
        ports[entry["type"]].add(tuple(entry.get("ports", [])))
    payload = [
        {
            "type": comp_type,
            "count": counts[comp_type],
            "port_variants": [list(value) for value in sorted(ports[comp_type])],
        }
        for comp_type in sorted(counts)
    ]
    return json.dumps(payload, indent=2)


@tool
def list_components(
    comp_type: str,
    min_pressure_bar: float | None = None,
    min_flow_lpm: float | None = None,
    limit: int = 20,
) -> str:
    """List catalog parts of one exact type, optionally filtering known pressure/flow ratings."""
    if comp_type not in component_types():
        return json.dumps({"error": "invalid component type", "valid_types": component_types()}, indent=2)
    matches: list[dict[str, Any]] = []
    for key, entry in CATALOG.items():
        if entry["type"] != comp_type:
            continue
        p_rating = pressure_rating(entry)
        q_rating = flow_rating(entry)
        if min_pressure_bar is not None and p_rating is not None and p_rating < min_pressure_bar:
            continue
        if min_flow_lpm is not None and q_rating is not None and q_rating < min_flow_lpm:
            continue
        matches.append(compact_entry(key, entry))
    return json.dumps(matches[: max(1, min(limit, 58))], indent=2, default=str)


@tool
def search_catalog(
    query: str,
    comp_type: str | None = None,
    min_pressure_bar: float | None = None,
    min_flow_lpm: float | None = None,
    limit: int = 20,
) -> str:
    """Search names, summaries, types, equations, and parameters in the component catalog."""
    words = [word.casefold() for word in query.split() if word.strip()]
    hits: list[tuple[int, str, dict[str, Any]]] = []
    for key, entry in CATALOG.items():
        if comp_type and entry["type"] != comp_type:
            continue
        p_rating = pressure_rating(entry)
        q_rating = flow_rating(entry)
        if min_pressure_bar is not None and p_rating is not None and p_rating < min_pressure_bar:
            continue
        if min_flow_lpm is not None and q_rating is not None and q_rating < min_flow_lpm:
            continue
        blob = " ".join(
            [key, str(entry.get("name", "")), entry.get("type", ""), str(entry.get("summary", "")),
             str(entry.get("params", {})), str(entry.get("equations", ""))]
        ).casefold()
        score = sum(blob.count(word) for word in words)
        if not words or score:
            hits.append((score, key, entry))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return json.dumps([compact_entry(key, entry) for _, key, entry in hits[: max(1, min(limit, 58))]], indent=2, default=str)


@tool
def get_component_details(catalog_key: str) -> str:
    """Get exact ports, port semantics, parameters, equations, and source for one catalog key."""
    if catalog_key not in CATALOG:
        return json.dumps({"error": "unknown catalog key", "catalog_key": catalog_key}, indent=2)
    entry = CATALOG[catalog_key]
    payload = compact_entry(catalog_key, entry)
    payload["params"] = entry.get("params", {})
    payload["equations"] = entry.get("equations")
    payload["notes"] = entry.get("notes")
    payload["source"] = source_for_entry(entry)
    return json.dumps(payload, indent=2, default=str)


@tool
def compare_components(catalog_keys: list[str]) -> str:
    """Compare two to six exact catalog keys using consistent fields and full key parameters."""
    if not 2 <= len(catalog_keys) <= 6:
        return json.dumps({"error": "provide two to six catalog keys"}, indent=2)
    unknown = [key for key in catalog_keys if key not in CATALOG]
    if unknown:
        return json.dumps({"error": "unknown catalog keys", "keys": unknown}, indent=2)
    return json.dumps([compact_entry(key, CATALOG[key]) for key in catalog_keys], indent=2, default=str)


@tool
def get_port_reference(catalog_keys: list[str]) -> str:
    """Return exact ports and port semantics for selected catalog keys before netlist construction."""
    payload: dict[str, Any] = {}
    for key in catalog_keys:
        if key not in CATALOG:
            payload[key] = {"error": "unknown catalog key"}
        else:
            payload[key] = {
                "type": CATALOG[key]["type"],
                "ports": CATALOG[key].get("ports", []),
                "port_types": CATALOG[key].get("port_types", {}),
            }
    return json.dumps(payload, indent=2)


CATALOG_TOOLS = [
    list_component_types,
    list_components,
    search_catalog,
    get_component_details,
    compare_components,
    get_port_reference,
]


def catalog_type_reference() -> str:
    return ", ".join(component_types())


def selected_catalog_reference(keys: list[str]) -> str:
    payload = {key: compact_entry(key, CATALOG[key]) for key in keys if key in CATALOG}
    return json.dumps(payload, indent=2, default=str)

