"""Read-only generic component-class catalog and designer tools.

The catalog is deliberately limited to choices that can change hydraulic
topology. It does not expose sizing values, OEM parts, accessories, lines,
manifolds or prime-mover hardware.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from langchain_core.tools import tool

from .data.component_catalog_problems_7 import CATALOG, FORBIDDEN_TOPOLOGY_TYPES


def component_types() -> list[str]:
    return sorted({entry["type"] for entry in CATALOG.values()})


def catalog_entry(key: str) -> dict[str, Any]:
    if key not in CATALOG:
        raise KeyError(f"Unknown generic catalog key {key!r}")
    return CATALOG[key]


def compact_entry(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Return only topology-relevant component-class metadata."""
    return {
        "catalog_key": key,
        "name": entry.get("name"),
        "type": entry.get("type"),
        "ports": entry.get("ports", []),
        "port_types": entry.get("port_types", {}),
        "summary": entry.get("summary"),
        "capabilities": entry.get("capabilities", []),
        "states": entry.get("states", []),
        "fixed_paths": entry.get("fixed_paths", []),
        "state_paths": entry.get("state_paths", {}),
        "default_state": entry.get("default_state"),
        "requires_phase_state": entry.get("requires_phase_state", False),
        "conditional_paths": entry.get("conditional_paths", []),
        "actuation": entry.get("actuation"),
    }


@tool
def list_component_types() -> str:
    """List the allowed generic hydraulic topology classes and exact port variants."""
    counts = Counter(entry["type"] for entry in CATALOG.values())
    ports: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for entry in CATALOG.values():
        ports[entry["type"]].add(tuple(entry.get("ports", [])))
    payload = [
        {
            "type": comp_type,
            "class_count": counts[comp_type],
            "port_variants": [list(value) for value in sorted(ports[comp_type])],
        }
        for comp_type in sorted(counts)
    ]
    return json.dumps(payload, indent=2)


@tool
def list_components(comp_type: str, limit: int = 20) -> str:
    """List generic component classes of one exact topology type."""
    if comp_type not in component_types():
        return json.dumps({"error": "invalid component type", "valid_types": component_types()}, indent=2)
    matches = [
        compact_entry(key, entry)
        for key, entry in CATALOG.items()
        if entry["type"] == comp_type
    ]
    return json.dumps(matches[: max(1, min(limit, len(CATALOG)))], indent=2)


@tool
def search_catalog(query: str, comp_type: str | None = None, limit: int = 20) -> str:
    """Search generic class names, capabilities, states, summaries and ports."""
    if comp_type and comp_type not in component_types():
        return json.dumps({"error": "invalid component type", "valid_types": component_types()}, indent=2)
    words = [word.casefold() for word in query.split() if word.strip()]
    hits: list[tuple[int, str, dict[str, Any]]] = []
    for key, entry in CATALOG.items():
        if comp_type and entry["type"] != comp_type:
            continue
        blob = " ".join(
            [
                key,
                str(entry.get("name", "")),
                entry.get("type", ""),
                str(entry.get("summary", "")),
                str(entry.get("capabilities", [])),
                str(entry.get("states", [])),
                str(entry.get("ports", [])),
            ]
        ).casefold()
        score = sum(blob.count(word) for word in words)
        if not words or score:
            hits.append((score, key, entry))
    hits.sort(key=lambda item: (-item[0], item[1]))
    payload = [compact_entry(key, entry) for _, key, entry in hits[: max(1, min(limit, len(CATALOG)))]]
    return json.dumps(payload, indent=2)


@tool
def get_component_details(catalog_key: str) -> str:
    """Get exact ports, states and capabilities for one generic topology class."""
    if catalog_key not in CATALOG:
        return json.dumps({"error": "unknown generic catalog key", "catalog_key": catalog_key}, indent=2)
    return json.dumps(compact_entry(catalog_key, CATALOG[catalog_key]), indent=2)


@tool
def compare_components(catalog_keys: list[str]) -> str:
    """Compare two to six generic topology classes using consistent fields."""
    if not 2 <= len(catalog_keys) <= 6:
        return json.dumps({"error": "provide two to six catalog keys"}, indent=2)
    unknown = [key for key in catalog_keys if key not in CATALOG]
    if unknown:
        return json.dumps({"error": "unknown generic catalog keys", "keys": unknown}, indent=2)
    return json.dumps([compact_entry(key, CATALOG[key]) for key in catalog_keys], indent=2)


@tool
def get_port_reference(catalog_keys: list[str]) -> str:
    """Return exact hydraulic ports and state behavior before netlist construction."""
    payload: dict[str, Any] = {}
    for key in catalog_keys:
        if key not in CATALOG:
            payload[key] = {"error": "unknown generic catalog key"}
        else:
            entry = CATALOG[key]
            payload[key] = {
                "type": entry["type"],
                "ports": entry.get("ports", []),
                "port_types": entry.get("port_types", {}),
                "states": entry.get("states", []),
                "fixed_paths": entry.get("fixed_paths", []),
                "state_paths": entry.get("state_paths", {}),
                "default_state": entry.get("default_state"),
                "requires_phase_state": entry.get("requires_phase_state", False),
                "conditional_paths": entry.get("conditional_paths", []),
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
    payload = {key: compact_entry(key, CATALOG[key]) for key in dict.fromkeys(keys) if key in CATALOG}
    return json.dumps(payload, indent=2)


def forbidden_type_reference() -> str:
    return ", ".join(sorted(FORBIDDEN_TOPOLOGY_TYPES))
