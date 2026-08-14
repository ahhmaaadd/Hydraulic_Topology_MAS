from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

from .catalog import CATALOG
from .data.component_catalog_problems_7 import FORBIDDEN_TOPOLOGY_TYPES
from .schemas import DeterministicValidation, TopologyDesign, TopologyIssue, ValidationCheck


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


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


def _add_edge(
    graph: dict[tuple[str, str], set[tuple[str, str]]],
    a: tuple[str, str],
    b: tuple[str, str],
) -> None:
    graph[a].add(b)
    graph[b].add(a)


def _internal_pairs(component: dict[str, Any], entry: dict[str, Any]) -> list[tuple[str, str]]:
    """Possible functional flow paths used only for reachability checks."""
    comp_type = component.get("comp_type")
    ports = set(entry.get("ports", []))
    pairs: list[tuple[str, str]] = []

    def add(a: str, b: str) -> None:
        if a in ports and b in ports:
            pairs.append((a, b))

    if comp_type in {"pressure_comp_flow_control", "one_way_flow_control"}:
        add("A", "B")
    elif comp_type == "position_valve":
        add("P", "A")
    elif comp_type == "sequence_valve":
        add("P", "A")
    elif comp_type == "pressure_reducing_valve":
        add("P", "A")
        add("A", "T")
    elif comp_type == "pilot_check_valve":
        add("A1", "A2")
        add("B1", "B2")
    elif comp_type == "single_pilot_check_valve":
        add("V", "C")
    elif comp_type == "dcv":
        # Possible-state connectivity, never simultaneous-state connectivity.
        for a, b in (("P", "A"), ("P", "B"), ("T", "A"), ("T", "B")):
            add(a, b)
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


def _rigid_mechanical_synchronization(
    design: dict[str, Any],
    requirements: dict[str, Any],
    components: list[dict[str, Any]],
) -> bool:
    text = " ".join(
        [
            str(requirements.get("restated_problem", "")),
            str(design.get("design_narrative", "")),
            str(design.get("function_implementations", [])),
            str(design.get("design_decisions", [])),
        ]
    ).casefold()
    if "rigid" not in text or not any(word in text for word in ("platen", "coupl", "structure")):
        return False
    return sum(component.get("comp_type") == "cylinder" for component in components) >= 2


def validate_topology(
    topology: TopologyDesign | dict[str, Any],
    requirements: dict[str, Any],
) -> DeterministicValidation:
    """Validate component classes and port topology, never sizing suitability."""
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
    duplicate_ids = sorted({component_id for component_id in ids if component_id and ids.count(component_id) > 1})
    missing_ids = [str(index) for index, component_id in enumerate(ids) if not component_id]
    if duplicate_ids:
        issues.append(_issue("DUPLICATE_COMPONENT_ID", f"Duplicate component ids: {duplicate_ids}", related=duplicate_ids))
    if missing_ids:
        issues.append(_issue("MISSING_COMPONENT_ID", f"Components at indexes {missing_ids} have no id."))
    checks.append(
        ValidationCheck(
            name="unique_component_ids",
            passed=not duplicate_ids and not missing_ids,
            details=f"{len(ids)} generic component instances checked.",
        )
    )

    entry_by_id: dict[str, dict[str, Any]] = {}
    forbidden: list[str] = []
    for component in components:
        component_id = component.get("id")
        key = component.get("catalog_key")
        comp_type = component.get("comp_type")
        if comp_type in FORBIDDEN_TOPOLOGY_TYPES:
            forbidden.append(str(component_id))
            issues.append(
                _issue(
                    "FORBIDDEN_TOPOLOGY_COMPONENT",
                    f"{component_id!r} is type {comp_type!r}, which belongs to sizing/layout rather than topology.",
                    scope="selection",
                    related=[component_id, comp_type],
                )
            )
        if key not in CATALOG:
            issues.append(
                _issue(
                    "UNKNOWN_CATALOG_KEY",
                    f"Component {component_id!r} selects unknown generic class key {key!r}.",
                    scope="selection",
                    related=[component_id, key],
                )
            )
            continue
        entry = CATALOG[key]
        entry_by_id[component_id] = entry
        if comp_type != entry.get("type"):
            issues.append(
                _issue(
                    "CATALOG_TYPE_MISMATCH",
                    f"{component_id!r} says type {comp_type!r}, but {key!r} is {entry.get('type')!r}.",
                    scope="selection",
                    related=[component_id, key],
                )
            )
    checks.append(
        ValidationCheck(
            name="generic_catalog_identity_and_scope",
            passed=not any(
                item.code in {"UNKNOWN_CATALOG_KEY", "CATALOG_TYPE_MISMATCH", "FORBIDDEN_TOPOLOGY_COMPONENT"}
                for item in issues
            ),
            details="Every instance must resolve to an allowed generic functional class; accessories and sizing hardware are forbidden.",
        )
    )

    used_ports: dict[str, set[str]] = defaultdict(set)
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
                        f"Connection {index}: {component_id!r} has no port {port!r}; valid ports are {entry.get('ports', [])}.",
                        related=[component_id, port],
                    )
                )
                valid_connection = False
                continue
            used_ports[component_id].add(port)
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

    # All current generic catalog ports are hydraulic. External interfaces and
    # terminations would hide an incomplete topology and are therefore errors.
    for item in external:
        component_id, port = item.get("component_id"), item.get("port")
        issues.append(
            _issue(
                "NON_HYDRAULIC_INTERFACE_IN_TOPOLOGY",
                f"External interface {component_id}.{port} is outside the topology-only output boundary.",
                related=[component_id, port],
            )
        )
    for item in terminations:
        component_id, port = item.get("component_id"), item.get("port")
        issues.append(
            _issue(
                "FUNCTIONAL_PORT_TERMINATED",
                f"Generic functional port {component_id}.{port} must be connected, not hidden by a termination.",
                related=[component_id, port],
            )
        )

    for component_id, entry in entry_by_id.items():
        for a, b in _internal_pairs(component_by_id[component_id], entry):
            _add_edge(port_graph, (component_id, a), (component_id, b))
        for port in entry.get("ports", []):
            if port not in used_ports[component_id]:
                issues.append(
                    _issue(
                        "UNCONNECTED_REQUIRED_PORT",
                        f"Required generic port {component_id}.{port} ({entry.get('port_types', {}).get(port)}) is unconnected.",
                        related=[component_id, port],
                    )
                )
    port_errors = {
        "UNKNOWN_CONNECTION_COMPONENT",
        "INVALID_CATALOG_PORT",
        "SELF_CONNECTION",
        "NON_HYDRAULIC_INTERFACE_IN_TOPOLOGY",
        "FUNCTIONAL_PORT_TERMINATED",
        "UNCONNECTED_REQUIRED_PORT",
    }
    checks.append(
        ValidationCheck(
            name="direct_port_accounting",
            passed=not any(item.code in port_errors and item.severity == "error" for item in issues),
            details="Every generic hydraulic port must participate in at least one direct edge; repeated ports represent branches.",
        )
    )

    types_present = {component.get("comp_type") for component in components}
    for required_type in ("tank", "pump", "relief_valve"):
        if required_type not in types_present:
            issues.append(
                _issue(
                    "MISSING_POWER_UNIT_COMPONENT",
                    f"No {required_type!r} is selected; the functional power supply is incomplete.",
                    scope="selection",
                    related=[required_type],
                )
            )
    linear_functions = [
        function for function in requirements.get("functions", []) if function.get("actuator_type") == "linear"
    ]
    if linear_functions:
        if "cylinder" not in types_present:
            issues.append(_issue("MISSING_ACTUATOR", "A linear function exists but no cylinder is selected.", scope="selection"))
        if "dcv" not in types_present:
            issues.append(_issue("MISSING_DIRECTIONAL_CONTROL", "A linear function exists but no DCV is selected.", scope="selection"))
    checks.append(
        ValidationCheck(
            name="minimum_functional_inventory",
            passed=not any(
                item.code in {"MISSING_POWER_UNIT_COMPONENT", "MISSING_ACTUATOR", "MISSING_DIRECTIONAL_CONTROL"}
                for item in issues
            ),
            details="Tank, pump, relief, actuator and directional-control functions checked without accessories.",
        )
    )

    pump_pressure_nodes = [(cid, "P") for cid, entry in entry_by_id.items() if entry.get("type") == "pump"]
    tank_return_nodes = [(cid, "R") for cid, entry in entry_by_id.items() if entry.get("type") == "tank"]
    tank_suction_nodes = [(cid, "S") for cid, entry in entry_by_id.items() if entry.get("type") == "tank"]
    pressure_reach = _reachable(port_graph, pump_pressure_nodes)
    return_reach = _reachable(port_graph, tank_return_nodes)
    suction_reach = _reachable(port_graph, tank_suction_nodes)

    for cid, entry in entry_by_id.items():
        comp_type = entry.get("type")
        if comp_type == "relief_valve":
            if (cid, "P") not in pressure_reach:
                issues.append(_issue("RELIEF_NOT_ON_PRESSURE_LINE", f"Relief valve {cid}.P is not reachable from Pump.P.", scope="safety", related=[cid]))
            if (cid, "T") not in return_reach:
                issues.append(_issue("RELIEF_NO_TANK_RETURN", f"Relief valve {cid}.T has no path to Tank.R.", scope="safety", related=[cid]))
        elif comp_type == "dcv":
            if (cid, "P") not in pressure_reach:
                issues.append(_issue("DCV_NO_PRESSURE_SUPPLY", f"DCV {cid}.P is not reachable from Pump.P.", related=[cid]))
            if (cid, "T") not in return_reach:
                issues.append(_issue("DCV_NO_TANK_RETURN", f"DCV {cid}.T has no path to Tank.R.", related=[cid]))
        elif comp_type == "pump" and (cid, "S") not in suction_reach:
            issues.append(_issue("PUMP_NO_SUCTION_PATH", f"Pump {cid}.S is not reachable from Tank.S.", related=[cid]))

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
        for port in ("Cap", "Rod"):
            if (cid, port) not in work_reach:
                issues.append(_issue("ACTUATOR_PORT_NOT_CONTROLLED", f"Cylinder {cid}.{port} is not reachable from a DCV work port.", related=[cid, port]))
    connectivity_codes = {
        "RELIEF_NOT_ON_PRESSURE_LINE",
        "RELIEF_NO_TANK_RETURN",
        "DCV_NO_PRESSURE_SUPPLY",
        "DCV_NO_TANK_RETURN",
        "PUMP_NO_SUCTION_PATH",
        "ACTUATOR_PORT_NOT_CONTROLLED",
    }
    checks.append(
        ValidationCheck(
            name="functional_path_reachability",
            passed=not any(item.code in connectivity_codes for item in issues),
            details="Suction, pressure, relief, return and both actuator work paths checked across possible valve states.",
        )
    )

    cylinders_by_function: dict[str, list[str]] = defaultdict(list)
    for component in components:
        if component.get("comp_type") == "cylinder" and component.get("function_id"):
            cylinders_by_function[component["function_id"]].append(component["id"])
    for function in linear_functions:
        fid = function.get("id")
        if not cylinders_by_function.get(fid):
            issues.append(
                _issue(
                    "FUNCTION_HAS_NO_ACTUATOR",
                    f"Linear function {fid!r} has no generic cylinder assigned by function_id.",
                    scope="selection",
                    related=[fid],
                )
            )

    drivers = {item.get("capability") for item in requirements.get("derived_design_drivers", [])}
    required_capability_types = {
        "pressure_compensation_load_independence": {"pressure_comp_flow_control"},
        "load_holding": {"pilot_check_valve", "single_pilot_check_valve"},
        "counterbalance_overrunning": {"pilot_check_valve", "single_pilot_check_valve"},
        "two_speed_force_switching": {
            "position_valve",
            "sequence_valve",
            "one_way_flow_control",
            "pressure_comp_flow_control",
        },
        "pressure_limiting_stall": {"relief_valve"},
    }
    for capability, acceptable_types in required_capability_types.items():
        if capability in drivers and not types_present.intersection(acceptable_types):
            issues.append(
                _issue(
                    "MISSING_DRIVER_CAPABILITY",
                    f"Derived topology driver {capability!r} has none of the implementing classes {sorted(acceptable_types)}.",
                    scope="selection",
                    related=[capability],
                )
            )
    if "synchronization" in drivers and not _rigid_mechanical_synchronization(design, requirements, components):
        issues.append(
            _issue(
                "MISSING_SYNCHRONIZATION_MECHANISM",
                "Synchronization is required but the topology does not document two cylinders acting on the stated rigid shared structure.",
                scope="selection",
                related=["synchronization"],
            )
        )

    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    interlocks = (requirements.get("operational_logic") or {}).get("interlocks") or []
    sequenced_function_ids = {
        function_id
        for step in sequence
        for function_id in (step.get("function_ids") or [])
        if function_id
    }
    if (len(sequenced_function_ids) > 1 or interlocks) and not types_present.intersection({"sequence_valve", "position_valve"}):
        issues.append(
            _issue(
                "SEQUENCE_NOT_IMPLEMENTED",
                "Multiple functions are ordered/interlocked but no hydraulic sequence or position-changeover valve is selected.",
                scope="selection",
            )
        )

    implemented_ids = {item.get("function_id") for item in design.get("function_implementations", [])}
    missing_implementations = sorted(_function_ids(requirements) - implemented_ids)
    if missing_implementations:
        issues.append(
            _issue(
                "MISSING_FUNCTION_TRACEABILITY",
                f"No function_implementation is provided for: {missing_implementations}.",
                scope="requirements",
                related=missing_implementations,
            )
        )
    blocking_gaps = [
        gap
        for gap in design.get("catalog_gaps", [])
        if gap.get("blocking") and gap.get("scope", "topology") == "topology"
    ]
    if blocking_gaps:
        issues.append(
            _issue(
                "BLOCKING_TOPOLOGY_CATALOG_GAP",
                "The design records at least one blocking generic topology-class gap.",
                scope="selection",
                related=[gap.get("capability") for gap in blocking_gaps],
            )
        )
    coverage_codes = {
        "FUNCTION_HAS_NO_ACTUATOR",
        "MISSING_DRIVER_CAPABILITY",
        "MISSING_SYNCHRONIZATION_MECHANISM",
        "SEQUENCE_NOT_IMPLEMENTED",
        "MISSING_FUNCTION_TRACEABILITY",
        "BLOCKING_TOPOLOGY_CATALOG_GAP",
    }
    checks.append(
        ValidationCheck(
            name="topology_requirement_coverage",
            passed=not any(item.code in coverage_codes for item in issues),
            details="Functional assignment, behavior drivers, hydraulic sequencing and topology-scoped gaps checked; all sizing checks are deferred.",
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
