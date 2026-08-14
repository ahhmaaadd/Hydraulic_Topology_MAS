from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

from .catalog import CATALOG, flow_rating, pressure_rating
from .schemas import (
    DeterministicValidation,
    TopologyDesign,
    TopologyIssue,
    ValidationCheck,
)


EXTERNAL_PORT_TYPES = {
    "electric_in",
    "electrical_out",
    "visual_or_signal",
    "atmosphere",
    "mechanical_input",
}
OPTIONAL_EXTERNAL_PORT_TYPES = {"electrical_out", "visual_or_signal", "atmosphere"}
BLOCKABLE_BY_TYPE: dict[str, set[str]] = {
    "manifold": {"A", "B", "X", "Y"},
    "filter": {"IND"},
    "level_temperature_gauge": {"OUT"},
}


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _max_pressure(requirements: dict[str, Any]) -> float | None:
    quantity = (requirements.get("global_constraints") or {}).get("max_system_pressure") or {}
    value = quantity.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _function_ids(requirements: dict[str, Any]) -> set[str]:
    return {item.get("id") for item in requirements.get("functions", []) if item.get("id")}


def _issue(
    code: str,
    description: str,
    *,
    severity: str = "error",
    scope: str = "wiring",
    related: Iterable[str] = (),
) -> TopologyIssue:
    return TopologyIssue(
        code=code,
        severity=severity,
        scope=scope,
        description=description,
        related=sorted(set(str(value) for value in related if value)),
    )


def _add_edge(graph: dict[tuple[str, str], set[tuple[str, str]]], a: tuple[str, str], b: tuple[str, str]) -> None:
    graph[a].add(b)
    graph[b].add(a)


def _internal_pairs(component: dict[str, Any], entry: dict[str, Any]) -> list[tuple[str, str]]:
    comp_type = component.get("comp_type")
    ports = set(entry.get("ports", []))
    pairs: list[tuple[str, str]] = []

    def add(a: str, b: str) -> None:
        if a in ports and b in ports:
            pairs.append((a, b))

    if comp_type in {"pipe", "filter", "cooler", "suction_strainer"}:
        add("IN", "OUT")
    elif comp_type in {"check_valve", "sequence_valve"}:
        add("P", "A")
    elif comp_type == "pressure_comp_flow_control":
        add("A", "B")
    elif comp_type == "pressure_reducing_valve":
        add("P", "A")
    elif comp_type == "pilot_check_valve":
        add("A1", "A2")
        add("B1", "B2")
    elif comp_type == "flow_divider_combiner":
        add("P", "A")
        add("P", "B")
    elif comp_type == "sequence_manifold":
        add("P", "C_A")
        add("P", "W_A")
        add("T", "C_B")
        add("T", "W_B")
    elif comp_type == "manifold":
        add("P", "A")
        add("P", "B")
        add("T", "Y")
    elif comp_type == "dcv":
        # Possible-state connectivity, not simultaneous connectivity.
        for a, b in (("P", "A"), ("P", "B"), ("T", "A"), ("T", "B")):
            add(a, b)
    elif comp_type == "position_valve":
        add("P", "A")
        add("A", "T")
    elif comp_type == "unloading_valve":
        add("P", "T")
    return pairs


def _reachable(
    graph: dict[tuple[str, str], set[tuple[str, str]]],
    starts: Iterable[tuple[str, str]],
) -> set[tuple[str, str]]:
    queue = deque(starts)
    seen = set(starts)
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _rough_cylinder_force_kN(entry: dict[str, Any], pressure_bar: float | None) -> float | None:
    if pressure_bar is None:
        return None
    area_cm2 = entry.get("params", {}).get("cap_area_cm2")
    if not isinstance(area_cm2, (int, float)):
        return None
    return 0.01 * pressure_bar * float(area_cm2) * 0.90


def validate_topology(
    topology: TopologyDesign | dict[str, Any],
    requirements: dict[str, Any],
) -> DeterministicValidation:
    design = _as_dict(topology)
    requirements = _as_dict(requirements)
    components = design.get("components") or []
    connections = design.get("connections") or []
    external = design.get("external_interfaces") or []
    terminations = design.get("port_terminations") or []
    issues: list[TopologyIssue] = []
    checks: list[ValidationCheck] = []

    ids = [component.get("id") for component in components]
    component_by_id = {component.get("id"): component for component in components if component.get("id")}
    duplicate_ids = sorted({component_id for component_id in ids if ids.count(component_id) > 1})
    if duplicate_ids:
        issues.append(_issue("DUPLICATE_COMPONENT_ID", f"Duplicate component ids: {duplicate_ids}", related=duplicate_ids))
    checks.append(ValidationCheck(name="unique_component_ids", passed=not duplicate_ids, details=f"{len(ids)} component instances checked."))

    entry_by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        component_id = component.get("id")
        key = component.get("catalog_key")
        if key not in CATALOG:
            issues.append(
                _issue(
                    "UNKNOWN_CATALOG_KEY",
                    f"Component {component_id!r} selects unknown catalog key {key!r}.",
                    scope="selection",
                    related=[component_id, key],
                )
            )
            continue
        entry = CATALOG[key]
        entry_by_id[component_id] = entry
        if component.get("comp_type") != entry.get("type"):
            issues.append(
                _issue(
                    "CATALOG_TYPE_MISMATCH",
                    f"{component_id!r} says type {component.get('comp_type')!r}, but {key!r} is {entry.get('type')!r}.",
                    scope="selection",
                    related=[component_id, key],
                )
            )
    checks.append(
        ValidationCheck(
            name="catalog_identity",
            passed=not any(item.code in {"UNKNOWN_CATALOG_KEY", "CATALOG_TYPE_MISMATCH"} for item in issues),
            details="Every component must resolve to one exact supplied catalog entry.",
        )
    )

    used_ports: dict[str, set[str]] = defaultdict(set)
    endpoint_lines: dict[str, set[str]] = defaultdict(set)
    port_graph: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    edge_fingerprints: set[tuple[tuple[str, str], tuple[str, str], str]] = set()
    for index, connection in enumerate(connections):
        endpoints: list[tuple[str, str]] = []
        valid_connection = True
        for side in ("from", "to"):
            component_id = connection.get(f"{side}_component")
            port = connection.get(f"{side}_port")
            if component_id not in component_by_id:
                issues.append(
                    _issue(
                        "UNKNOWN_CONNECTION_COMPONENT",
                        f"Connection {index} references unknown component {component_id!r}.",
                        related=[component_id],
                    )
                )
                valid_connection = False
                continue
            entry = entry_by_id.get(component_id)
            if entry is None:
                valid_connection = False
                continue
            if port not in entry.get("ports", []):
                issues.append(
                    _issue(
                        "INVALID_CATALOG_PORT",
                        f"Connection {index}: {component_id!r} has no catalog port {port!r}; valid ports are {entry.get('ports', [])}.",
                        related=[component_id, port],
                    )
                )
                valid_connection = False
                continue
            used_ports[component_id].add(port)
            endpoint_lines[component_id].add(connection.get("line", "unspecified"))
            endpoints.append((component_id, port))
        if valid_connection and len(endpoints) == 2:
            if endpoints[0] == endpoints[1]:
                issues.append(_issue("SELF_CONNECTION", f"Connection {index} connects a port to itself.", related=[endpoints[0][0]]))
                continue
            fingerprint = tuple(sorted(endpoints)) + (connection.get("line", "unspecified"),)
            if fingerprint in edge_fingerprints:
                issues.append(
                    _issue(
                        "DUPLICATE_CONNECTION",
                        f"Connection {index} duplicates an existing edge {endpoints}.",
                        severity="warning",
                        related=[endpoints[0][0], endpoints[1][0]],
                    )
                )
            edge_fingerprints.add(fingerprint)
            _add_edge(port_graph, endpoints[0], endpoints[1])

    external_ports: dict[str, set[str]] = defaultdict(set)
    for item in external:
        component_id, port = item.get("component_id"), item.get("port")
        entry = entry_by_id.get(component_id)
        if entry is None or port not in entry.get("ports", []):
            issues.append(_issue("INVALID_EXTERNAL_INTERFACE", f"Invalid external interface {component_id}.{port}.", related=[component_id, port]))
            continue
        port_type = entry.get("port_types", {}).get(port)
        if port_type not in EXTERNAL_PORT_TYPES:
            issues.append(
                _issue(
                    "HYDRAULIC_PORT_MARKED_EXTERNAL",
                    f"{component_id}.{port} ({port_type}) cannot be satisfied by an external non-hydraulic interface.",
                    related=[component_id, port],
                )
            )
        external_ports[component_id].add(port)

    terminated_ports: dict[str, set[str]] = defaultdict(set)
    for item in terminations:
        component_id, port = item.get("component_id"), item.get("port")
        entry = entry_by_id.get(component_id)
        if entry is None or port not in entry.get("ports", []):
            issues.append(_issue("INVALID_PORT_TERMINATION", f"Invalid port termination {component_id}.{port}.", related=[component_id, port]))
            continue
        if port not in BLOCKABLE_BY_TYPE.get(entry.get("type"), set()):
            issues.append(
                _issue(
                    "FUNCTIONAL_PORT_TERMINATED",
                    f"{component_id}.{port} is a functional catalog port and may not be hidden with a termination.",
                    related=[component_id, port],
                )
            )
        terminated_ports[component_id].add(port)

    for component_id, entry in entry_by_id.items():
        for a, b in _internal_pairs(component_by_id[component_id], entry):
            _add_edge(port_graph, (component_id, a), (component_id, b))
        for port in entry.get("ports", []):
            dispositions = sum(
                port in values
                for values in (used_ports[component_id], external_ports[component_id], terminated_ports[component_id])
            )
            if dispositions > 1:
                issues.append(
                    _issue(
                        "MULTIPLE_PORT_DISPOSITIONS",
                        f"{component_id}.{port} is both connected/external/terminated; choose exactly one disposition.",
                        related=[component_id, port],
                    )
                )
            elif dispositions == 0:
                port_type = entry.get("port_types", {}).get(port)
                if port_type in OPTIONAL_EXTERNAL_PORT_TYPES:
                    issues.append(
                        _issue(
                            "OPTIONAL_PORT_UNDECLARED",
                            f"Optional {component_id}.{port} ({port_type}) has no declared disposition.",
                            severity="warning",
                            related=[component_id, port],
                        )
                    )
                else:
                    issues.append(
                        _issue(
                            "UNCONNECTED_REQUIRED_PORT",
                            f"Required catalog port {component_id}.{port} ({port_type}) is not connected or assigned an external interface.",
                            related=[component_id, port],
                        )
                    )
    port_errors = {
        "UNKNOWN_CONNECTION_COMPONENT",
        "INVALID_CATALOG_PORT",
        "INVALID_EXTERNAL_INTERFACE",
        "HYDRAULIC_PORT_MARKED_EXTERNAL",
        "INVALID_PORT_TERMINATION",
        "FUNCTIONAL_PORT_TERMINATED",
        "MULTIPLE_PORT_DISPOSITIONS",
        "UNCONNECTED_REQUIRED_PORT",
    }
    checks.append(
        ValidationCheck(
            name="exact_port_accounting",
            passed=not any(item.code in port_errors and item.severity == "error" for item in issues),
            details="Every catalog port must be connected, external, or explicitly and physically terminable.",
        )
    )

    types_present = {component.get("comp_type") for component in components}
    for required_type in ("tank", "pump", "relief_valve"):
        if required_type not in types_present:
            issues.append(
                _issue(
                    "MISSING_POWER_UNIT_COMPONENT",
                    f"No {required_type!r} is selected; the hydraulic power unit is incomplete.",
                    scope="selection",
                    related=[required_type],
                )
            )
    if any(function.get("actuator_type") == "linear" for function in requirements.get("functions", [])):
        if "cylinder" not in types_present:
            issues.append(_issue("MISSING_ACTUATOR", "A linear function exists but no cylinder is selected.", scope="selection"))
        if "dcv" not in types_present:
            issues.append(_issue("MISSING_DIRECTIONAL_CONTROL", "A linear actuator exists but no DCV is selected.", scope="selection"))
    checks.append(
        ValidationCheck(
            name="minimum_power_and_control_inventory",
            passed=not any(item.code.startswith("MISSING_") and item.severity == "error" for item in issues),
            details="Tank, pump, relief, actuator, and directional-control inventory checked.",
        )
    )

    pump_pressure_nodes = [(cid, "P") for cid, entry in entry_by_id.items() if entry.get("type") == "pump"]
    tank_return_nodes = [
        (cid, port)
        for cid, entry in entry_by_id.items()
        if entry.get("type") == "tank"
        for port in ("R", "D")
        if port in entry.get("ports", [])
    ]
    tank_suction_nodes = [(cid, "S") for cid, entry in entry_by_id.items() if entry.get("type") == "tank" and "S" in entry.get("ports", [])]
    pressure_reach = _reachable(port_graph, pump_pressure_nodes)
    return_reach = _reachable(port_graph, tank_return_nodes)
    suction_reach = _reachable(port_graph, tank_suction_nodes)

    for cid, entry in entry_by_id.items():
        comp_type = entry.get("type")
        if comp_type == "relief_valve":
            if (cid, "P") not in pressure_reach:
                issues.append(_issue("RELIEF_NOT_ON_PRESSURE_LINE", f"Relief valve {cid} P is not reachable from pump pressure.", scope="safety", related=[cid]))
            if (cid, "T") not in return_reach:
                issues.append(_issue("RELIEF_NO_TANK_RETURN", f"Relief valve {cid} T has no return path to tank.", scope="safety", related=[cid]))
        elif comp_type == "dcv":
            if (cid, "P") not in pressure_reach:
                issues.append(_issue("DCV_NO_PRESSURE_SUPPLY", f"DCV {cid} P is not reachable from pump pressure.", related=[cid]))
            if (cid, "T") not in return_reach:
                issues.append(_issue("DCV_NO_TANK_RETURN", f"DCV {cid} T has no return path to tank.", related=[cid]))
        elif comp_type == "pump":
            if (cid, "S") not in suction_reach:
                issues.append(_issue("PUMP_NO_SUCTION_PATH", f"Pump {cid} S is not reachable from tank suction.", related=[cid]))
            if "L" in entry.get("ports", []) and (cid, "L") not in return_reach:
                issues.append(_issue("PUMP_CASE_DRAIN_NOT_TO_TANK", f"Pump {cid} case drain L is not reachable to tank return/drain.", related=[cid]))

    dcv_work_nodes = [
        (cid, port)
        for cid, entry in entry_by_id.items()
        if entry.get("type") == "dcv"
        for port in ("A", "B")
    ]
    work_reach = _reachable(port_graph, dcv_work_nodes)
    for cid, entry in entry_by_id.items():
        if entry.get("type") != "cylinder":
            continue
        for port in ("A", "B"):
            if (cid, port) not in work_reach:
                issues.append(_issue("ACTUATOR_PORT_NOT_CONTROLLED", f"Cylinder {cid}.{port} is not reachable from a DCV work port.", related=[cid, port]))
    connectivity_codes = {
        "RELIEF_NOT_ON_PRESSURE_LINE",
        "RELIEF_NO_TANK_RETURN",
        "DCV_NO_PRESSURE_SUPPLY",
        "DCV_NO_TANK_RETURN",
        "PUMP_NO_SUCTION_PATH",
        "PUMP_CASE_DRAIN_NOT_TO_TANK",
        "ACTUATOR_PORT_NOT_CONTROLLED",
    }
    checks.append(
        ValidationCheck(
            name="hydraulic_path_reachability",
            passed=not any(item.code in connectivity_codes for item in issues),
            details="Suction, pressure, relief, return, drain, and both actuator work paths checked.",
        )
    )

    max_pressure = _max_pressure(requirements)
    high_pressure_lines = {"pressure", "work", "pilot"}
    if max_pressure is not None:
        for cid, entry in entry_by_id.items():
            rating = pressure_rating(entry)
            if rating is None or not endpoint_lines[cid].intersection(high_pressure_lines):
                continue
            if rating + 1e-9 < max_pressure:
                issues.append(
                    _issue(
                        "PRESSURE_RATING_TOO_LOW",
                        f"{cid} is exposed to the {max_pressure:g} bar envelope but its catalog rating is {rating:g} bar.",
                        scope="selection",
                        related=[cid],
                    )
                )
    checks.append(
        ValidationCheck(
            name="catalog_pressure_envelope",
            passed=not any(item.code == "PRESSURE_RATING_TOO_LOW" for item in issues),
            details="Known catalog pressure ratings were compared with the stated system ceiling on high-pressure paths.",
        )
    )

    pump_flow = sum(
        float(entry.get("params", {}).get("flow_lpm", 0) or 0)
        for entry in entry_by_id.values()
        if entry.get("type") == "pump"
    )
    if pump_flow > 0:
        for cid, entry in entry_by_id.items():
            if entry.get("type") not in {"dcv", "relief_valve", "pilot_check_valve", "sequence_valve", "flow_divider_combiner"}:
                continue
            rating = flow_rating(entry)
            if rating is not None and rating + 1e-9 < pump_flow:
                issues.append(
                    _issue(
                        "FLOW_RATING_TOO_LOW",
                        f"{cid} is rated {rating:g} L/min but selected pump flow can reach {pump_flow:g} L/min.",
                        scope="selection",
                        related=[cid],
                    )
                )
    checks.append(
        ValidationCheck(
            name="basic_catalog_flow_compatibility",
            passed=not any(item.code == "FLOW_RATING_TOO_LOW" for item in issues),
            details="Known controlling-valve flow ratings were compared with selected catalog pump flow.",
        )
    )

    max_system_pressure = _max_pressure(requirements)
    cylinders_by_function: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for component in components:
        cid = component.get("id")
        entry = entry_by_id.get(cid)
        if entry and entry.get("type") == "cylinder" and component.get("function_id"):
            cylinders_by_function[component["function_id"]].append((cid, entry))
    for function in requirements.get("functions", []):
        fid = function.get("id")
        selected = cylinders_by_function.get(fid, [])
        if function.get("actuator_type") == "linear" and not selected:
            issues.append(_issue("FUNCTION_HAS_NO_ACTUATOR", f"Function {fid!r} has no cylinder assigned by function_id.", scope="selection", related=[fid]))
            continue
        travel = (function.get("total_travel") or {}).get("value")
        if isinstance(travel, (int, float)):
            short = [cid for cid, entry in selected if isinstance(entry.get("params", {}).get("stroke_mm"), (int, float)) and entry["params"]["stroke_mm"] < travel]
            if short:
                issues.append(_issue("CYLINDER_STROKE_TOO_SHORT", f"Function {fid!r} needs {travel:g} mm but selected cylinder(s) are too short: {short}.", scope="selection", related=[fid, *short]))
        force = (function.get("peak_force") or {}).get("value")
        if isinstance(force, (int, float)) and selected:
            capacity = sum(filter(None, (_rough_cylinder_force_kN(entry, max_system_pressure) for _, entry in selected)))
            if capacity and capacity + 1e-9 < force:
                issues.append(_issue("CYLINDER_FORCE_CAPACITY_TOO_LOW", f"Function {fid!r} needs {force:g} kN; rough selected cap-end capacity at the pressure ceiling is {capacity:.2f} kN.", scope="selection", related=[fid, *(cid for cid, _ in selected)]))
    checks.append(
        ValidationCheck(
            name="basic_actuator_catalog_compatibility",
            passed=not any(item.code in {"FUNCTION_HAS_NO_ACTUATOR", "CYLINDER_STROKE_TOO_SHORT", "CYLINDER_FORCE_CAPACITY_TOO_LOW"} for item in issues),
            details="Function assignment, stroke, and rough cap-end force were checked where data were available.",
        )
    )

    drivers = {item.get("capability") for item in requirements.get("derived_design_drivers", [])}
    required_capability_types = {
        "synchronization": {"flow_divider_combiner"},
        "pressure_compensation_load_independence": {"pressure_comp_flow_control"},
        "load_holding": {"pilot_check_valve"},
        "counterbalance_overrunning": {"pilot_check_valve"},
        "two_speed_force_switching": {"unloading_valve", "position_valve", "sequence_valve", "sequence_manifold"},
        "pressure_limiting_stall": {"relief_valve"},
    }
    for capability, acceptable_types in required_capability_types.items():
        if capability in drivers and not types_present.intersection(acceptable_types):
            issues.append(
                _issue(
                    "MISSING_DRIVER_CAPABILITY",
                    f"Derived driver {capability!r} has none of the available implementing types {sorted(acceptable_types)}.",
                    scope="selection",
                    related=[capability],
                )
            )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    interlocks = (requirements.get("operational_logic") or {}).get("interlocks") or []
    if (len(sequence) > 1 or interlocks) and not types_present.intersection({"sequence_valve", "sequence_manifold", "position_valve"}):
        issues.append(_issue("SEQUENCE_NOT_IMPLEMENTED", "The requirements contain ordered/interlocked functions but no hydraulic sequencing component is selected.", scope="selection"))

    original_text = (requirements.get("restated_problem") or "").casefold()
    no_electrical_sensing = "no electrical" in original_text or "without using an electrical pressure switch" in original_text
    if no_electrical_sensing:
        offenders = [
            cid
            for cid, entry in entry_by_id.items()
            if entry.get("params", {}).get("electrical_pressure_switch") is True
            or "pressure switch" in str(entry.get("summary", "")).casefold()
        ]
        if offenders:
            issues.append(_issue("FORBIDDEN_ELECTRICAL_PRESSURE_SENSING", f"Electrical pressure sensing is forbidden, but selected entries imply it: {offenders}.", scope="selection", related=offenders))

    implemented_ids = {item.get("function_id") for item in design.get("function_implementations", [])}
    missing_implementations = sorted(_function_ids(requirements) - implemented_ids)
    if missing_implementations:
        issues.append(_issue("MISSING_FUNCTION_TRACEABILITY", f"No function_implementation is provided for: {missing_implementations}.", scope="requirements", related=missing_implementations))
    blocking_gaps = [gap for gap in design.get("catalog_gaps", []) if gap.get("blocking")]
    if blocking_gaps:
        issues.append(_issue("BLOCKING_CATALOG_GAP", "The design records at least one blocking catalog gap.", scope="selection", related=[gap.get("capability") for gap in blocking_gaps]))

    checks.append(
        ValidationCheck(
            name="requirement_and_driver_coverage",
            passed=not any(item.code in {"MISSING_DRIVER_CAPABILITY", "SEQUENCE_NOT_IMPLEMENTED", "FORBIDDEN_ELECTRICAL_PRESSURE_SENSING", "MISSING_FUNCTION_TRACEABILITY", "BLOCKING_CATALOG_GAP"} for item in issues),
            details="Derived drivers, sequence constraints, explicit prohibitions, function traceability, and catalog gaps checked.",
        )
    )

    # Deduplicate exact repeated issues while retaining stable order.
    unique: list[TopologyIssue] = []
    seen_issues: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in issues:
        key = (item.code, item.description, tuple(item.related))
        if key not in seen_issues:
            unique.append(item)
            seen_issues.add(key)
    verdict = "invalid" if any(item.severity == "error" for item in unique) else "valid"
    return DeterministicValidation(verdict=verdict, issues=unique, checks=checks)


def repair_scope(issues: list[dict[str, Any]] | list[TopologyIssue]) -> str:
    normalized = [item.model_dump() if hasattr(item, "model_dump") else item for item in issues]
    errors = [item for item in normalized if item.get("severity") == "error"]
    if not errors:
        return "none"
    if all(item.get("scope") == "wiring" for item in errors):
        return "wiring"
    return "selection"

