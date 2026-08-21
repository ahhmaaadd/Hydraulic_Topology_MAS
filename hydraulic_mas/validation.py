"""Deterministic topology validation with directed, phase-specific flow graphs.

Connections describe physical hydraulic lines and are bidirectional. Direction
is introduced only by component behavior (pump, DCV state, check, metering,
sequence, counterbalance, and so on). Each declared motion phase is validated
independently; mutually exclusive DCV states are never merged to prove motion.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .catalog import CATALOG
from .data.component_catalog_problems_7 import FORBIDDEN_TOPOLOGY_TYPES
from .decision_flow import (
    expected_metered_chamber,
    motion_decisions_from_requirements,
    synchronization_decisions_from_requirements,
)
from .gap_policy import (
    classify_catalog_gap,
    classify_evidence_gap,
    interfunction_sequence_steps,
    requires_pressure_sequence_valve,
)
from .schemas import (
    DeterministicValidation,
    MotionControlDecision,
    RequirementsSpec,
    TopologyDesign,
    TopologyIssue,
    ValidationCheck,
)


Node = tuple[str, str]
METER_KINDS = {"metered", "metered_compensated"}


@dataclass(frozen=True)
class FlowEdge:
    source: Node
    target: Node
    owner: str | None
    kind: str
    internal: bool


FlowGraph = dict[Node, list[FlowEdge]]


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


def _add_flow_edge(
    graph: FlowGraph,
    source: Node,
    target: Node,
    *,
    owner: str | None,
    kind: str,
    internal: bool,
) -> bool:
    edge = FlowEdge(source=source, target=target, owner=owner, kind=kind, internal=internal)
    if edge in graph[source]:
        return False
    graph[source].append(edge)
    return True


def _add_line(graph: FlowGraph, a: Node, b: Node, line: str) -> None:
    _add_flow_edge(graph, a, b, owner=None, kind=f"line:{line}", internal=False)
    _add_flow_edge(graph, b, a, owner=None, kind=f"line:{line}", internal=False)


def _find_path(
    graph: FlowGraph,
    starts: Iterable[Node],
    targets: Iterable[Node],
    *,
    edge_allowed: Callable[[FlowEdge], bool] | None = None,
) -> list[FlowEdge] | None:
    starts = list(starts)
    target_set = set(targets)
    queue = deque(starts)
    predecessor: dict[Node, FlowEdge | None] = {node: None for node in starts}
    reached: Node | None = None
    while queue:
        node = queue.popleft()
        if node in target_set:
            reached = node
            break
        for edge in graph.get(node, []):
            if edge_allowed is not None and not edge_allowed(edge):
                continue
            if edge.target in predecessor:
                continue
            predecessor[edge.target] = edge
            queue.append(edge.target)
    if reached is None:
        return None
    path: list[FlowEdge] = []
    cursor = reached
    while predecessor[cursor] is not None:
        edge = predecessor[cursor]
        assert edge is not None
        path.append(edge)
        cursor = edge.source
    path.reverse()
    return path


def _reachable(graph: FlowGraph, starts: Iterable[Node]) -> set[Node]:
    starts = list(starts)
    queue = deque(starts)
    seen = set(starts)
    while queue:
        node = queue.popleft()
        for edge in graph.get(node, []):
            if edge.target not in seen:
                seen.add(edge.target)
                queue.append(edge.target)
    return seen


def _undirected_reachable(graph: dict[Node, set[Node]], starts: Iterable[Node]) -> set[Node]:
    starts = list(starts)
    queue = deque(starts)
    seen = set(starts)
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _append_catalog_path(graph: FlowGraph, component_id: str, raw: dict[str, Any]) -> bool:
    return _add_flow_edge(
        graph,
        (component_id, str(raw["from"])),
        (component_id, str(raw["to"])),
        owner=component_id,
        kind=str(raw.get("kind") or "internal"),
        internal=True,
    )


def _component_state_map(configuration: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    states: dict[str, str] = {}
    duplicates: list[str] = []
    for item in configuration.get("component_states", []):
        component_id = item.get("component_id")
        if not component_id:
            continue
        if component_id in states:
            duplicates.append(str(component_id))
        states[str(component_id)] = str(item.get("state"))
    return states, duplicates


def _selected_phase_state(
    component: dict[str, Any],
    entry: dict[str, Any],
    configuration: dict[str, Any],
    explicit_states: dict[str, str],
    issues: list[TopologyIssue],
) -> str | None:
    component_id = str(component.get("id"))
    valid_states = set((entry.get("state_paths") or {}).keys())
    if not valid_states:
        if component_id in explicit_states:
            issues.append(
                _issue(
                    "STATE_DECLARED_FOR_STATELESS_COMPONENT",
                    f"Phase {configuration.get('phase_id')!r} assigns state {explicit_states[component_id]!r} "
                    f"to stateless component {component_id!r}.",
                    related=[configuration.get("phase_id"), component_id],
                )
            )
        return None

    selected = explicit_states.get(component_id)
    if selected is not None and selected not in valid_states:
        issues.append(
            _issue(
                "INVALID_PHASE_COMPONENT_STATE",
                f"Phase {configuration.get('phase_id')!r}: {component_id!r} state {selected!r} is invalid; "
                f"allowed states are {sorted(valid_states)}.",
                related=[configuration.get("phase_id"), component_id, selected],
            )
        )
        selected = None
    if selected is None and entry.get("requires_phase_state"):
        issues.append(
            _issue(
                "MISSING_PHASE_COMPONENT_STATE",
                f"Phase {configuration.get('phase_id')!r} does not declare a state for {component_id!r}.",
                related=[configuration.get("phase_id"), component_id],
            )
        )
        if entry.get("type") == "dcv":
            motion = str(configuration.get("motion"))
            function_id = configuration.get("function_id")
            if component.get("function_id") == function_id and motion in valid_states:
                selected = motion
            elif "neutral" in valid_states:
                selected = "neutral"
        selected = selected or entry.get("default_state")
    return selected or entry.get("default_state")


def _build_phase_graph(
    *,
    valid_lines: list[tuple[Node, Node, str]],
    components: list[dict[str, Any]],
    entry_by_id: dict[str, dict[str, Any]],
    configuration: dict[str, Any],
    pump_nodes: list[Node],
    issues: list[TopologyIssue],
) -> tuple[FlowGraph, dict[str, str]]:
    graph: FlowGraph = defaultdict(list)
    for a, b, line in valid_lines:
        _add_line(graph, a, b, line)
    explicit_states, duplicates = _component_state_map(configuration)
    for component_id in duplicates:
        issues.append(
            _issue(
                "DUPLICATE_PHASE_COMPONENT_STATE",
                f"Phase {configuration.get('phase_id')!r} assigns more than one state to {component_id!r}.",
                related=[configuration.get("phase_id"), component_id],
            )
        )

    selected_states: dict[str, str] = {}
    deferred_counterbalance: list[tuple[str, dict[str, Any]]] = []
    for component in components:
        component_id = component.get("id")
        entry = entry_by_id.get(str(component_id))
        if entry is None:
            continue
        for raw in entry.get("fixed_paths", []):
            _append_catalog_path(graph, str(component_id), raw)
        state = _selected_phase_state(component, entry, configuration, explicit_states, issues)
        if state:
            selected_states[str(component_id)] = state
            for raw in (entry.get("state_paths") or {}).get(state, []):
                if raw.get("kind") == "counterbalance_controlled":
                    deferred_counterbalance.append((str(component_id), raw))
                else:
                    _append_catalog_path(graph, str(component_id), raw)

    changed = True
    while changed:
        changed = False
        pressure_reach = _reachable(graph, pump_nodes)
        for component in components:
            component_id = str(component.get("id"))
            entry = entry_by_id.get(component_id)
            if entry is None:
                continue
            for raw in entry.get("conditional_paths", []):
                required = (component_id, str(raw.get("requires_pressure_at")))
                if required in pressure_reach:
                    changed = _append_catalog_path(graph, component_id, raw) or changed

    pressure_reach = _reachable(graph, pump_nodes)
    for component_id, raw in deferred_counterbalance:
        if (component_id, "X") not in pressure_reach:
            issues.append(
                _issue(
                    "COUNTERBALANCE_PILOT_NOT_PRESSURIZED",
                    f"Phase {configuration.get('phase_id')!r} commands controlled release of {component_id!r}, "
                    "but its X pilot has no pressure path.",
                    scope="safety",
                    related=[configuration.get("phase_id"), component_id],
                )
            )
            continue
        _append_catalog_path(graph, component_id, raw)

    pressure_reach = _reachable(graph, pump_nodes)
    for component_id, state in selected_states.items():
        entry = entry_by_id[component_id]
        if "X" not in entry.get("ports", []):
            continue
        if entry.get("type") == "unloading_valve" and state == "unloaded":
            if (component_id, "X") not in pressure_reach:
                issues.append(
                    _issue(
                        "UNLOADING_PILOT_NOT_PRESSURIZED",
                        f"Phase {configuration.get('phase_id')!r} selects {component_id}.unloaded without pressure at X.",
                        related=[configuration.get("phase_id"), component_id],
                    )
                )
        if entry.get("type") == "sequence_valve" and state == "open" and "X" in entry.get("ports", []):
            if (component_id, "X") not in pressure_reach:
                issues.append(
                    _issue(
                        "SEQUENCE_PILOT_NOT_PRESSURIZED",
                        f"Phase {configuration.get('phase_id')!r} selects {component_id}.open without pressure at X.",
                        related=[configuration.get("phase_id"), component_id],
                    )
                )
    return graph, selected_states


def _build_union_graph(
    valid_lines: list[tuple[Node, Node, str]],
    entry_by_id: dict[str, dict[str, Any]],
) -> FlowGraph:
    graph: FlowGraph = defaultdict(list)
    for a, b, line in valid_lines:
        _add_line(graph, a, b, line)
    for component_id, entry in entry_by_id.items():
        for raw in entry.get("fixed_paths", []):
            _append_catalog_path(graph, component_id, raw)
        for paths in (entry.get("state_paths") or {}).values():
            for raw in paths:
                _append_catalog_path(graph, component_id, raw)
        for raw in entry.get("conditional_paths", []):
            _append_catalog_path(graph, component_id, raw)
    return graph


def _decision_signature(value: MotionControlDecision) -> tuple[Any, ...]:
    return (
        value.function_id,
        value.phase_id,
        value.phase_name,
        value.motion.value,
        value.load_type.value,
        value.speed_realization.value,
        value.metering_side.value,
        value.metered_chamber.value,
        value.metered_flow.value,
        value.flow_compensation.value,
        value.load_control.value,
        value.justification,
        value.source.value,
    )


def _path_owner_types(path: list[FlowEdge] | None, entry_by_id: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(entry_by_id[edge.owner]["type"])
        for edge in (path or [])
        if edge.internal and edge.owner in entry_by_id
    }


def _path_kinds(path: list[FlowEdge] | None) -> set[str]:
    return {edge.kind for edge in (path or []) if edge.internal}


LOAD_LOCK_TYPES = {"pilot_check_valve", "single_pilot_check_valve"}


def _load_lock_ids_for_function(components: list[dict[str, Any]], function_id: str) -> list[str]:
    """Load locks assigned to this function, or shared locks with no function id."""
    return [
        str(component.get("id"))
        for component in components
        if component.get("comp_type") in LOAD_LOCK_TYPES
        and component.get("function_id") in {None, "", function_id}
    ]


def _branch_graph(
    valid_lines: list[tuple[Node, Node, str]],
    entry_by_id: dict[str, dict[str, Any]],
) -> dict[Node, set[Node]]:
    graph: dict[Node, set[Node]] = defaultdict(set)
    for a, b, _line in valid_lines:
        graph[a].add(b)
        graph[b].add(a)
    excluded = {"dcv", "pump", "tank", "relief_valve", "unloading_valve", "cylinder"}
    for component_id, entry in entry_by_id.items():
        if entry.get("type") in excluded:
            continue
        raw_paths = list(entry.get("fixed_paths", []))
        for paths in (entry.get("state_paths") or {}).values():
            raw_paths.extend(paths)
        raw_paths.extend(entry.get("conditional_paths", []))
        for raw in raw_paths:
            a = (component_id, str(raw["from"]))
            b = (component_id, str(raw["to"]))
            graph[a].add(b)
            graph[b].add(a)
    return graph


def _validate_synchronization(
    *,
    requirements: dict[str, Any],
    components: list[dict[str, Any]],
    entry_by_id: dict[str, dict[str, Any]],
    valid_lines: list[tuple[Node, Node, str]],
    issues: list[TopologyIssue],
) -> None:
    cylinder_ids = {
        component.get("id")
        for component in components
        if component.get("comp_type") == "cylinder"
    }
    direct_actuator_links = [
        (a, b)
        for a, b, _line in valid_lines
        if a[0] in cylinder_ids and b[0] in cylinder_ids
    ]
    sync_by_function = {
        item.get("id"): item.get("synchronization") or {}
        for item in requirements.get("functions", [])
    }
    any_series_allowed = any(
        item.get("strategy") == "hydraulic_series" for item in sync_by_function.values()
    )
    if direct_actuator_links and not any_series_allowed:
        related = [node[0] for pair in direct_actuator_links for node in pair]
        issues.append(
            _issue(
                "UNAUTHORIZED_SERIES_ACTUATORS",
                "One cylinder chamber is connected directly to another cylinder chamber, but hydraulic_series "
                "was not explicitly selected.",
                scope="selection",
                related=related,
            )
        )

    branch_graph = _branch_graph(valid_lines, entry_by_id)
    dcv_work_nodes = {
        (component_id, port)
        for component_id, entry in entry_by_id.items()
        if entry.get("type") == "dcv"
        for port in ("A", "B")
    }
    for function in requirements.get("functions", []):
        function_id = function.get("id")
        decision = function.get("synchronization") or {}
        strategy = decision.get("strategy", "none")
        assigned = [
            str(component.get("id"))
            for component in components
            if component.get("comp_type") == "cylinder" and component.get("function_id") == function_id
        ]
        required_count = int(decision.get("actuator_count", 1) or 1)
        if len(assigned) < required_count:
            issues.append(
                _issue(
                    "SYNCHRONIZED_ACTUATOR_COUNT_MISMATCH",
                    f"Function {function_id!r} requires {required_count} actuators but has {len(assigned)} assigned cylinders.",
                    scope="selection",
                    related=[function_id, *assigned],
                )
            )
        if strategy == "rigid_platen_parallel" and len(assigned) >= 2:
            if direct_actuator_links:
                issues.append(
                    _issue(
                        "RIGID_PLATEN_REQUIRES_PARALLEL_CYLINDERS",
                        f"Rigid shared structure for {function_id!r} requires parallel work branches, not cylinder-to-cylinder series plumbing.",
                        scope="selection",
                        related=[function_id, *assigned],
                    )
                )
            chamber_work_sets: dict[str, list[set[Node]]] = {"Cap": [], "Rod": []}
            for chamber in ("Cap", "Rod"):
                for cylinder_id in assigned:
                    reach = _undirected_reachable(branch_graph, [(cylinder_id, chamber)])
                    chamber_work_sets[chamber].append(reach.intersection(dcv_work_nodes))
            cap_common = set.intersection(*chamber_work_sets["Cap"]) if chamber_work_sets["Cap"] else set()
            rod_common = set.intersection(*chamber_work_sets["Rod"]) if chamber_work_sets["Rod"] else set()
            if not cap_common or not rod_common or not any(cap != rod for cap in cap_common for rod in rod_common):
                issues.append(
                    _issue(
                        "RIGID_PLATEN_PARALLEL_BRANCHES_NOT_PROVEN",
                        f"Both cap chambers and both rod chambers for {function_id!r} do not resolve to two common, opposite DCV work branches.",
                        related=[function_id, *assigned],
                    )
                )
        elif strategy == "flow_divider_parallel":
            dividers = [
                component.get("id")
                for component in components
                if component.get("comp_type") == "flow_divider_combiner"
                and component.get("function_id") in {None, function_id}
            ]
            if not dividers:
                issues.append(
                    _issue(
                        "FLOW_DIVIDER_COMBINER_MISSING",
                        f"Function {function_id!r} selects flow_divider_parallel without a divider/combiner.",
                        scope="selection",
                        related=[function_id],
                    )
                )
            if direct_actuator_links:
                issues.append(
                    _issue(
                        "FLOW_DIVIDER_REQUIRES_PARALLEL_ACTUATORS",
                        f"Function {function_id!r} has a divider strategy but also directly series-connects actuator chambers.",
                        related=[function_id, *assigned],
                    )
                )
        elif strategy == "hydraulic_series":
            if not direct_actuator_links:
                issues.append(
                    _issue(
                        "SERIES_ACTUATOR_CHAIN_MISSING",
                        f"Function {function_id!r} selects hydraulic_series but no actuator-to-actuator chamber link exists.",
                        related=[function_id, *assigned],
                    )
                )
            if decision.get("series_displacement_compatibility") != "explicitly_matched":
                issues.append(
                    _issue(
                        "SERIES_CYLINDER_RATIO_NOT_PROVEN",
                        f"Function {function_id!r} uses hydraulic_series without explicitly matched displacement compatibility.",
                        scope="requirements",
                        related=[function_id],
                    )
                )


def validate_topology(
    topology: TopologyDesign | dict[str, Any],
    requirements: dict[str, Any],
) -> DeterministicValidation:
    """Validate generic classes and prove each directed operating phase."""
    design = _as_dict(topology)
    requirements = RequirementsSpec.model_validate(_as_dict(requirements)).model_dump(mode="json")
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
    checks.append(ValidationCheck(name="unique_component_ids", passed=not duplicate_ids and not missing_ids, details=f"{len(ids)} generic component instances checked."))

    entry_by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        component_id = component.get("id")
        key = component.get("catalog_key")
        comp_type = component.get("comp_type")
        if comp_type in FORBIDDEN_TOPOLOGY_TYPES:
            issues.append(_issue("FORBIDDEN_TOPOLOGY_COMPONENT", f"{component_id!r} is type {comp_type!r}, which belongs to sizing/layout rather than topology.", scope="selection", related=[component_id, comp_type]))
        if key not in CATALOG:
            issues.append(_issue("UNKNOWN_CATALOG_KEY", f"Component {component_id!r} selects unknown generic class key {key!r}.", scope="selection", related=[component_id, key]))
            continue
        entry = CATALOG[key]
        entry_by_id[str(component_id)] = entry
        if comp_type != entry.get("type"):
            issues.append(_issue("CATALOG_TYPE_MISMATCH", f"{component_id!r} says type {comp_type!r}, but {key!r} is {entry.get('type')!r}.", scope="selection", related=[component_id, key]))
    catalog_codes = {"UNKNOWN_CATALOG_KEY", "CATALOG_TYPE_MISMATCH", "FORBIDDEN_TOPOLOGY_COMPONENT"}
    checks.append(ValidationCheck(name="generic_catalog_identity_and_scope", passed=not any(item.code in catalog_codes for item in issues), details="Every instance must resolve to an allowed generic functional class."))

    used_ports: dict[str, set[str]] = defaultdict(set)
    valid_lines: list[tuple[Node, Node, str]] = []
    fingerprints: set[tuple[Node, Node, str]] = set()
    for index, connection in enumerate(connections):
        endpoints: list[Node] = []
        valid = True
        for side in ("from", "to"):
            component_id = connection.get(f"{side}_component")
            port = connection.get(f"{side}_port")
            if component_id not in component_by_id:
                issues.append(_issue("UNKNOWN_CONNECTION_COMPONENT", f"Connection {index} references unknown component {component_id!r}.", related=[component_id]))
                valid = False
                continue
            entry = entry_by_id.get(str(component_id))
            if entry is None:
                valid = False
                continue
            if port not in entry.get("ports", []):
                issues.append(_issue("INVALID_CATALOG_PORT", f"Connection {index}: {component_id!r} has no port {port!r}; valid ports are {entry.get('ports', [])}.", related=[component_id, port]))
                valid = False
                continue
            endpoints.append((str(component_id), str(port)))
            used_ports[str(component_id)].add(str(port))
        if valid and len(endpoints) == 2:
            if endpoints[0] == endpoints[1]:
                issues.append(_issue("SELF_CONNECTION", f"Connection {index} connects a port to itself."))
                continue
            line = str(connection.get("line", "unspecified"))
            fingerprint = (*sorted(endpoints), line)
            if fingerprint in fingerprints:
                issues.append(_issue("DUPLICATE_CONNECTION", f"Connection {index} duplicates an existing edge {endpoints}.", severity="warning", related=[endpoints[0][0], endpoints[1][0]]))
            fingerprints.add(fingerprint)
            valid_lines.append((endpoints[0], endpoints[1], line))

    for item in external:
        issues.append(_issue("NON_HYDRAULIC_INTERFACE_IN_TOPOLOGY", f"External interface {item.get('component_id')}.{item.get('port')} is outside the topology-only boundary.", related=[item.get("component_id"), item.get("port")]))
    for item in terminations:
        issues.append(_issue("FUNCTIONAL_PORT_TERMINATED", f"Generic functional port {item.get('component_id')}.{item.get('port')} must be connected.", related=[item.get("component_id"), item.get("port")]))
    for component_id, entry in entry_by_id.items():
        for port in entry.get("ports", []):
            if port not in used_ports[component_id]:
                issues.append(_issue("UNCONNECTED_REQUIRED_PORT", f"Required generic port {component_id}.{port} ({entry.get('port_types', {}).get(port)}) is unconnected.", related=[component_id, port]))
    port_codes = {"UNKNOWN_CONNECTION_COMPONENT", "INVALID_CATALOG_PORT", "SELF_CONNECTION", "NON_HYDRAULIC_INTERFACE_IN_TOPOLOGY", "FUNCTIONAL_PORT_TERMINATED", "UNCONNECTED_REQUIRED_PORT"}
    checks.append(ValidationCheck(name="direct_port_accounting", passed=not any(item.code in port_codes and item.severity == "error" for item in issues), details="Every hydraulic port participates in a direct physical line; shared endpoints represent branches."))

    types_present = {component.get("comp_type") for component in components}
    for required_type in ("tank", "pump", "relief_valve"):
        if required_type not in types_present:
            issues.append(_issue("MISSING_POWER_UNIT_COMPONENT", f"No {required_type!r} is selected; the functional power supply is incomplete.", scope="selection", related=[required_type]))
    linear_functions = [function for function in requirements.get("functions", []) if function.get("actuator_type") == "linear"]
    if linear_functions and "cylinder" not in types_present:
        issues.append(_issue("MISSING_ACTUATOR", "A linear function exists but no cylinder is selected.", scope="selection"))
    if linear_functions and "dcv" not in types_present:
        issues.append(_issue("MISSING_DIRECTIONAL_CONTROL", "A linear function exists but no DCV is selected.", scope="selection"))
    inventory_codes = {"MISSING_POWER_UNIT_COMPONENT", "MISSING_ACTUATOR", "MISSING_DIRECTIONAL_CONTROL"}
    checks.append(ValidationCheck(name="minimum_functional_inventory", passed=not any(item.code in inventory_codes for item in issues), details="Tank, pump, relief, actuator, and directional-control functions checked."))

    pump_nodes = [(component_id, "P") for component_id, entry in entry_by_id.items() if entry.get("type") == "pump"]
    tank_return_nodes = [(component_id, "R") for component_id, entry in entry_by_id.items() if entry.get("type") == "tank"]
    tank_suction_nodes = [(component_id, "S") for component_id, entry in entry_by_id.items() if entry.get("type") == "tank"]
    union_graph = _build_union_graph(valid_lines, entry_by_id)
    pressure_reach = _reachable(union_graph, pump_nodes)
    suction_reach = _reachable(union_graph, tank_suction_nodes)
    for component_id, entry in entry_by_id.items():
        comp_type = entry.get("type")
        if comp_type == "relief_valve":
            if (component_id, "P") not in pressure_reach:
                issues.append(_issue("RELIEF_NOT_ON_PRESSURE_LINE", f"Relief valve {component_id}.P has no directed path from a pump pressure port.", scope="safety", related=[component_id]))
            if _find_path(union_graph, [(component_id, "T")], tank_return_nodes) is None:
                issues.append(_issue("RELIEF_NO_TANK_RETURN", f"Relief valve {component_id}.T has no path to Tank.R.", scope="safety", related=[component_id]))
        elif comp_type == "dcv":
            if (component_id, "P") not in pressure_reach:
                issues.append(_issue("DCV_NO_PRESSURE_SUPPLY", f"DCV {component_id}.P has no supply path.", related=[component_id]))
            if _find_path(union_graph, [(component_id, "T")], tank_return_nodes) is None:
                issues.append(_issue("DCV_NO_TANK_RETURN", f"DCV {component_id}.T has no tank-return path.", related=[component_id]))
        elif comp_type == "pump" and (component_id, "S") not in suction_reach:
            issues.append(_issue("PUMP_NO_SUCTION_PATH", f"Pump {component_id}.S has no path from Tank.S.", related=[component_id]))
    system_path_codes = {"RELIEF_NOT_ON_PRESSURE_LINE", "RELIEF_NO_TANK_RETURN", "DCV_NO_PRESSURE_SUPPLY", "DCV_NO_TANK_RETURN", "PUMP_NO_SUCTION_PATH"}
    checks.append(ValidationCheck(name="directed_system_paths", passed=not any(item.code in system_path_codes for item in issues), details="Suction, pump supply, relief, DCV pressure, and return directions checked without merging actuator states."))

    cylinders_by_function: dict[str, list[str]] = defaultdict(list)
    for component in components:
        if component.get("comp_type") == "cylinder" and component.get("function_id"):
            cylinders_by_function[str(component["function_id"])].append(str(component["id"]))
    for function in linear_functions:
        function_id = str(function.get("id"))
        if not cylinders_by_function.get(function_id):
            issues.append(_issue("FUNCTION_HAS_NO_ACTUATOR", f"Linear function {function_id!r} has no cylinder assigned by function_id.", scope="selection", related=[function_id]))

    try:
        expected_motion = motion_decisions_from_requirements(requirements)
        actual_motion = [MotionControlDecision.model_validate(item) for item in design.get("motion_control_decisions", [])]
        expected_by_phase = {item.phase_id: item for item in expected_motion}
        actual_by_phase = {item.phase_id: item for item in actual_motion}
        for phase_id in sorted(expected_by_phase.keys() - actual_by_phase.keys()):
            issues.append(_issue("MOTION_DECISION_NOT_PROPAGATED", f"Canonical motion-control decision for phase {phase_id!r} is missing from the topology.", scope="requirements", related=[phase_id]))
        for phase_id in sorted(actual_by_phase.keys() - expected_by_phase.keys()):
            issues.append(_issue("EXTRA_MOTION_DECISION", f"Topology contains motion-control decision for unknown phase {phase_id!r}.", scope="requirements", related=[phase_id]))
        for phase_id in sorted(expected_by_phase.keys() & actual_by_phase.keys()):
            if _decision_signature(expected_by_phase[phase_id]) != _decision_signature(actual_by_phase[phase_id]):
                issues.append(_issue("MOTION_DECISION_MUTATED", f"Topology changed the canonical metering/load-control decision for phase {phase_id!r}.", scope="requirements", related=[phase_id]))
    except Exception as exc:
        issues.append(_issue("INVALID_MOTION_DECISION_PAYLOAD", f"Topology motion-control decisions cannot be parsed: {type(exc).__name__}: {exc}", scope="requirements"))
        expected_motion = []

    expected_sync = [item.model_dump(mode="json") for item in synchronization_decisions_from_requirements(requirements)]
    actual_sync = design.get("synchronization_decisions", [])
    if expected_sync != actual_sync:
        issues.append(_issue("SYNCHRONIZATION_DECISION_NOT_PROPAGATED", "Topology synchronization decisions differ from the finalized requirements.", scope="requirements"))
    decision_codes = {"MOTION_DECISION_NOT_PROPAGATED", "EXTRA_MOTION_DECISION", "MOTION_DECISION_MUTATED", "INVALID_MOTION_DECISION_PAYLOAD", "SYNCHRONIZATION_DECISION_NOT_PROPAGATED"}
    checks.append(ValidationCheck(name="typed_decision_propagation", passed=not any(item.code in decision_codes for item in issues), details="Metering, chamber, flow direction, compensation, load control, and synchronization are immutable across stages."))

    configurations = design.get("phase_configurations", [])
    config_by_phase: dict[str, dict[str, Any]] = {}
    for configuration in configurations:
        phase_id = configuration.get("phase_id")
        if phase_id in config_by_phase:
            issues.append(_issue("DUPLICATE_PHASE_CONFIGURATION", f"More than one PhaseConfiguration exists for {phase_id!r}.", related=[phase_id]))
        elif phase_id:
            config_by_phase[str(phase_id)] = configuration

    sequence_steps = (requirements.get("operational_logic") or {}).get("sequence") or []
    sequence_by_phase = {step.get("phase_id"): step for step in sequence_steps if step.get("phase_id")}
    states_by_phase: dict[str, dict[str, str]] = {}
    # Which (function_id, motion) pairs an earlier *operational* phase has
    # already driven to an end position.  This has to follow the declared
    # sequence order rather than the order functions happen to be listed in, or
    # a later clamp-release phase would not see the drill retract that precedes
    # it in the real cycle.
    sequence_position = {
        str(step.get("phase_id")): int(step.get("order") or index)
        for index, step in enumerate(sequence_steps)
        if step.get("phase_id")
    }
    motion_by_phase = {item.phase_id: (item.function_id, item.motion.value) for item in expected_motion}

    def _completed_before(phase_id: str) -> set[tuple[str, str]]:
        cutoff = sequence_position.get(phase_id)
        if cutoff is None:
            return set()
        return {
            motion_by_phase[earlier]
            for earlier, position in sequence_position.items()
            if position < cutoff and earlier in motion_by_phase
        }

    for decision in expected_motion:
        phase_id = decision.phase_id
        configuration = config_by_phase.get(phase_id)
        if configuration is None:
            issues.append(_issue("MISSING_PHASE_CONFIGURATION", f"No directed PhaseConfiguration is provided for motion phase {phase_id!r}.", related=[phase_id, decision.function_id]))
            continue
        if configuration.get("function_id") != decision.function_id or configuration.get("motion") != decision.motion.value:
            issues.append(_issue("PHASE_CONFIGURATION_MISMATCH", f"PhaseConfiguration {phase_id!r} does not match function/motion in the canonical decision.", scope="requirements", related=[phase_id, decision.function_id]))
        expected_active = set(configuration.get("expected_active_function_ids") or [])
        if decision.motion.value != "hold" and decision.function_id not in expected_active:
            issues.append(_issue("PHASE_ACTIVE_FUNCTION_MISSING", f"Phase {phase_id!r} does not declare {decision.function_id!r} as active.", scope="requirements", related=[phase_id, decision.function_id]))
        phase_issues: list[TopologyIssue] = []
        graph, selected_states = _build_phase_graph(valid_lines=valid_lines, components=components, entry_by_id=entry_by_id, configuration=configuration, pump_nodes=pump_nodes, issues=phase_issues)
        states_by_phase[phase_id] = selected_states
        issues.extend(phase_issues)
        if decision.motion.value == "hold":
            # There are two physically different holds and they need opposite
            # things, so decide which one this is before checking anything.
            #
            #   Parked hold - the command is released and the spool centres over
            #   the load (P7-03's carriage). The lock must reseat, so its pilot
            #   has to decay to tank.
            #
            #   Maintained hold - the actuator stays commanded and supplied while
            #   something else happens (P7-05 and P7-07's clamp during the work
            #   stroke). Live supply pressure holds the load, the lock is
            #   deliberately held open by it, and venting the work line would
            #   release the very force the phase exists to maintain.
            #
            # Applying the parked-hold rule to a maintained hold made P7-05
            # unsatisfiable: centring the clamp valve killed the sequence pilot,
            # and keeping it commanded tripped the venting rule.
            held_cylinders = cylinders_by_function.get(decision.function_id, [])
            hold_is_supplied = any(
                _find_path(graph, pump_nodes, [(cylinder_id, port)]) is not None
                for cylinder_id in held_cylinders
                for port in ("Cap", "Rod")
            )
            for lock_id in [] if hold_is_supplied else _load_lock_ids_for_function(components, decision.function_id):
                entry = entry_by_id.get(lock_id) or {}
                port_types = entry.get("port_types") or {}
                pilot_ports = [port for port, kind in port_types.items() if kind == "pilot_in"]
                vent_ports = pilot_ports or [
                    port for port, kind in port_types.items() if kind == "valve_side"
                ]
                unvented = [
                    port
                    for port in vent_ports
                    if _find_path(graph, [(lock_id, port)], tank_return_nodes) is None
                ]
                if unvented:
                    issues.append(
                        _issue(
                            "LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD",
                            f"Hold phase {phase_id!r} leaves {lock_id} port(s) {sorted(unvented)} with no "
                            "path to tank, so the pilot-operated load lock cannot be proven to reseat. "
                            "Use a directional-valve neutral that vents both work lines to tank "
                            "(float or open centre) or route an explicit pilot drain.",
                            scope="selection",
                            related=[phase_id, lock_id, decision.function_id],
                        )
                    )
            # A hold that runs *concurrently with another function* is a
            # maintained force, not a parked load. P7-05's clamp must stay
            # applied while the drill feeds, and the run that failed centred the
            # clamp's directional valve and relied on oil checked in behind the
            # load lock. A checked-in volume holds only until it leaks, it is
            # not a proven supply, and anything piloted from it - here the
            # forward sequence valve gating the drill - dies with it.
            concurrent = sorted(
                {
                    str(value)
                    for value in (configuration.get("expected_active_function_ids") or [])
                    if str(value) != decision.function_id
                }
            )
            if concurrent and not hold_is_supplied:
                for cylinder_id in held_cylinders:
                    issues.append(
                        _issue(
                            "CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED",
                            f"Phase {phase_id!r} holds {decision.function_id!r} while {concurrent} "
                            f"are active, but {cylinder_id} has no directed pump path in that phase. "
                            "A force that must be maintained while another actuator cycles has to "
                            "stay commanded and supplied; keep the directional valve commanded "
                            "instead of centring it.",
                            scope="wiring",
                            related=[phase_id, decision.function_id, cylinder_id],
                        )
                    )
            continue
        supply_port = "Cap" if decision.motion.value == "extend" else "Rod"
        exhaust_port = "Rod" if decision.motion.value == "extend" else "Cap"
        cylinders = cylinders_by_function.get(decision.function_id, [])
        synchronization = next(
            (
                function.get("synchronization") or {}
                for function in requirements.get("functions", [])
                if function.get("id") == decision.function_id
            ),
            {},
        )
        if synchronization.get("strategy") == "hydraulic_series":
            # In a series circuit, motion of an upstream piston displaces oil
            # from its opposite chamber into the next actuator. Represent that
            # physical displacement only for the explicitly selected/matched
            # series strategy; it is never added to ordinary parallel circuits.
            series_graph: FlowGraph = defaultdict(list)
            for node, edges in graph.items():
                series_graph[node].extend(edges)
            for cylinder_id in cylinders:
                _add_flow_edge(
                    series_graph,
                    (cylinder_id, supply_port),
                    (cylinder_id, exhaust_port),
                    owner=cylinder_id,
                    kind="actuator_displacement",
                    internal=True,
                )
            graph = series_graph
        supply_paths: dict[str, list[FlowEdge] | None] = {}
        exhaust_paths: dict[str, list[FlowEdge] | None] = {}
        regenerative_paths: dict[str, list[FlowEdge] | None] = {}
        for cylinder_id in cylinders:
            supply_path = _find_path(graph, pump_nodes, [(cylinder_id, supply_port)])
            exhaust_path = _find_path(graph, [(cylinder_id, exhaust_port)], tank_return_nodes)
            regenerative_path = _find_path(
                graph,
                [(cylinder_id, exhaust_port)],
                [(cylinder_id, supply_port)],
            )
            supply_paths[cylinder_id] = supply_path
            exhaust_paths[cylinder_id] = exhaust_path
            regenerative_paths[cylinder_id] = regenerative_path
            if supply_path is None:
                issues.append(_issue("PHASE_SUPPLY_PATH_MISSING", f"Phase {phase_id!r} has no directed pump-to-{cylinder_id}.{supply_port} supply path.", related=[phase_id, cylinder_id, supply_port]))
            if (
                exhaust_path is None
                and decision.speed_realization.value != "regenerative"
            ):
                issues.append(_issue("PHASE_EXHAUST_PATH_MISSING", f"Phase {phase_id!r} has no directed {cylinder_id}.{exhaust_port}-to-tank exhaust path.", related=[phase_id, cylinder_id, exhaust_port]))
            if (
                decision.speed_realization.value == "regenerative"
                and regenerative_path is None
            ):
                issues.append(
                    _issue(
                        "REGENERATIVE_RECIRCULATION_PATH_MISSING",
                        f"Phase {phase_id!r} is regenerative but has no directed "
                        f"{cylinder_id}.{exhaust_port}-to-{cylinder_id}.{supply_port} recirculation path.",
                        related=[phase_id, cylinder_id, exhaust_port, supply_port],
                    )
                )

        expected_chamber = expected_metered_chamber(decision.motion, decision.metering_side)
        if decision.metered_chamber.value != expected_chamber.value:
            issues.append(_issue("METERING_CHAMBER_INCONSISTENT", f"Phase {phase_id!r} declares {decision.metered_chamber.value} but {decision.metering_side.value} for {decision.motion.value} requires {expected_chamber.value}.", scope="requirements", related=[phase_id]))
        for cylinder_id in cylinders:
            supply_path = supply_paths[cylinder_id]
            exhaust_path = exhaust_paths[cylinder_id]
            selected_path = supply_path if decision.metering_side.value == "meter_in" else exhaust_path
            selected_start = pump_nodes if decision.metering_side.value == "meter_in" else [(cylinder_id, exhaust_port)]
            selected_target = [(cylinder_id, supply_port)] if decision.metering_side.value == "meter_in" else tank_return_nodes
            if decision.metering_side.value in {"meter_in", "meter_out"}:
                kinds = _path_kinds(selected_path)
                owner_types = _path_owner_types(selected_path, entry_by_id)
                expected_type = "pressure_comp_flow_control" if decision.flow_compensation.value == "pressure_compensated" else "one_way_flow_control"
                if not kinds.intersection(METER_KINDS) or expected_type not in owner_types:
                    issues.append(_issue("METERING_PATH_NOT_REALIZED", f"Phase {phase_id!r} does not realize {decision.metering_side.value} through the required {expected_type} on {cylinder_id}'s controlled path.", related=[phase_id, cylinder_id]))
                unmetered = _find_path(graph, selected_start, selected_target, edge_allowed=lambda edge: edge.kind not in METER_KINDS)
                if unmetered is not None:
                    issues.append(_issue("METERING_BYPASSED_IN_PHASE", f"Phase {phase_id!r} has an active unmetered path parallel to its declared metering path.", related=[phase_id, cylinder_id]))
            elif decision.metering_side.value == "none":
                supply_unmetered = _find_path(graph, pump_nodes, [(cylinder_id, supply_port)], edge_allowed=lambda edge: edge.kind not in METER_KINDS)
                exhaust_unmetered = _find_path(graph, [(cylinder_id, exhaust_port)], tank_return_nodes, edge_allowed=lambda edge: edge.kind not in METER_KINDS)
                if (
                    decision.speed_realization.value != "regenerative"
                    and supply_path is not None
                    and exhaust_path is not None
                    and (supply_unmetered is None or exhaust_unmetered is None)
                ):
                    issues.append(_issue("UNDECLARED_METERING_IN_PHASE", f"Phase {phase_id!r} declares no metering but motion is forced through a metered direction.", related=[phase_id, cylinder_id]))

            if decision.load_type.value in {"overrunning", "both"} and decision.metering_side.value == "meter_in" and decision.load_control.value != "counterbalance":
                issues.append(_issue("OVERRUNNING_LOAD_METER_IN_ONLY", f"Phase {phase_id!r} uses meter-in-only control for an overrunning load.", scope="safety", related=[phase_id, cylinder_id]))
            if decision.load_control.value == "counterbalance" and "counterbalance_controlled" not in _path_kinds(exhaust_path):
                issues.append(_issue("COUNTERBALANCE_NOT_IN_EXHAUST_PATH", f"Phase {phase_id!r} requires counterbalance control, but {cylinder_id}'s exhaust path does not pass through it.", scope="safety", related=[phase_id, cylinder_id]))

        forbidden = set(configuration.get("forbidden_active_function_ids") or [])
        sequence_step = sequence_by_phase.get(phase_id) or {}
        forbidden.update(sequence_step.get("forbidden_overlap_function_ids") or [])
        # A function that has already been driven to its mechanical end position
        # in an earlier phase of the same command cannot move again in that same
        # direction, however much pressure remains on its port.  Requiring the
        # design to isolate an already-completed actuator forced spurious extra
        # valves.  The exemption is deliberately narrow: the design must declare
        # the completion, and an earlier validated phase must actually have
        # driven that function in that direction.
        declared_completed = {
            str(value) for value in (configuration.get("completed_function_ids") or [])
        }
        completed_motion = _completed_before(phase_id)
        unproven_completed = sorted(
            declared_completed - {function_id for function_id, _ in completed_motion}
        )
        if unproven_completed:
            issues.append(
                _issue(
                    "UNPROVEN_COMPLETED_FUNCTION",
                    f"Phase {phase_id!r} declares {unproven_completed} as already completed, but no "
                    "earlier phase in this topology drives those functions to an end position.",
                    scope="requirements",
                    related=[phase_id, *unproven_completed],
                )
            )
        for forbidden_function in forbidden:
            forbidden_function = str(forbidden_function)
            for cylinder_id in cylinders_by_function.get(forbidden_function, []):
                cap_supplied = _find_path(graph, pump_nodes, [(cylinder_id, "Cap")]) is not None
                rod_supplied = _find_path(graph, pump_nodes, [(cylinder_id, "Rod")]) is not None
                cap_exhaust = _find_path(graph, [(cylinder_id, "Cap")], tank_return_nodes) is not None
                rod_exhaust = _find_path(graph, [(cylinder_id, "Rod")], tank_return_nodes) is not None
                movable: set[str] = set()
                if cap_supplied and rod_exhaust:
                    movable.add("extend")
                if rod_supplied and cap_exhaust:
                    movable.add("retract")
                if forbidden_function in declared_completed:
                    movable -= {
                        motion
                        for function_id, motion in completed_motion
                        if function_id == forbidden_function
                    }
                if movable:
                    issues.append(
                        _issue(
                            "FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE",
                            f"Function {forbidden_function!r} can still {'/'.join(sorted(movable))} "
                            f"during phase {phase_id!r}, where overlap is forbidden.",
                            scope="safety",
                            related=[phase_id, forbidden_function, cylinder_id],
                        )
                    )

    unknown_configs = sorted(set(config_by_phase) - {item.phase_id for item in expected_motion})
    if unknown_configs:
        issues.append(_issue("UNKNOWN_PHASE_CONFIGURATION", f"Topology declares PhaseConfigurations not present in requirements: {unknown_configs}.", scope="requirements", related=unknown_configs))
    phase_codes = {"MISSING_PHASE_CONFIGURATION", "PHASE_CONFIGURATION_MISMATCH", "PHASE_ACTIVE_FUNCTION_MISSING", "MISSING_PHASE_COMPONENT_STATE", "INVALID_PHASE_COMPONENT_STATE", "DUPLICATE_PHASE_COMPONENT_STATE", "PHASE_SUPPLY_PATH_MISSING", "PHASE_EXHAUST_PATH_MISSING", "REGENERATIVE_RECIRCULATION_PATH_MISSING", "METERING_CHAMBER_INCONSISTENT", "METERING_PATH_NOT_REALIZED", "METERING_BYPASSED_IN_PHASE", "UNDECLARED_METERING_IN_PHASE", "OVERRUNNING_LOAD_METER_IN_ONLY", "COUNTERBALANCE_NOT_IN_EXHAUST_PATH", "COUNTERBALANCE_PILOT_NOT_PRESSURIZED", "FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE", "UNPROVEN_COMPLETED_FUNCTION", "LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD", "CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED", "UNKNOWN_PHASE_CONFIGURATION"}
    checks.append(ValidationCheck(name="directed_phase_state_behavior", passed=not any(item.code in phase_codes for item in issues), details=f"{len(expected_motion)} canonical motion phase(s) checked with separate directed component states."))

    _validate_synchronization(requirements=requirements, components=components, entry_by_id=entry_by_id, valid_lines=valid_lines, issues=issues)

    drivers = {item.get("capability") for item in requirements.get("derived_design_drivers", [])}
    required_capability_types = {
        "pressure_compensation_load_independence": {"pressure_comp_flow_control"},
        "regeneration_fast_approach": {"check_valve"},
        "load_holding": {"pilot_check_valve", "single_pilot_check_valve", "counterbalance_valve"},
        "counterbalance_overrunning": {"counterbalance_valve"},
        "two_speed_force_switching": {"position_valve", "sequence_valve", "one_way_flow_control", "pressure_comp_flow_control"},
        "pressure_limiting_stall": {"relief_valve"},
        "branch_pressure_reduction": {"pressure_reducing_valve"},
    }
    for capability, acceptable_types in required_capability_types.items():
        if capability in drivers and not types_present.intersection(acceptable_types):
            issues.append(_issue("MISSING_DRIVER_CAPABILITY", f"Derived driver {capability!r} has none of {sorted(acceptable_types)}.", scope="selection", related=[capability]))

    for function in requirements.get("functions", []):
        function_id = str(function.get("id"))
        holding = function.get("holding") or {}
        phases = function.get("motion_phases") or []
        needs_lock = any(
            holding.get(field)
            for field in ("must_hold_position", "no_creep", "hold_on_power_loss", "retain_on_hose_burst")
        ) or any(phase.get("load_control") == "pilot_check" for phase in phases)
        needs_counterbalance = any(phase.get("load_control") == "counterbalance" for phase in phases)
        assigned_types = {
            component.get("comp_type")
            for component in components
            if component.get("function_id") in {None, function_id}
        }
        if needs_counterbalance and "counterbalance_valve" not in assigned_types:
            issues.append(
                _issue(
                    "COUNTERBALANCE_COMPONENT_MISSING",
                    f"Function {function_id!r} has a typed counterbalance decision but no assigned counterbalance valve.",
                    scope="selection",
                    related=[function_id],
                )
            )
        elif needs_lock and not assigned_types.intersection(
            {"pilot_check_valve", "single_pilot_check_valve", "counterbalance_valve"}
        ):
            issues.append(
                _issue(
                    "LOAD_LOCK_COMPONENT_MISSING",
                    f"Function {function_id!r} requires holding/no-drift behavior but has no assigned load-lock valve.",
                    scope="selection",
                    related=[function_id],
                )
            )

    # Only a pressure trigger that crosses a function boundary implies a
    # sequence valve. A load-actuated speed change inside one actuator is
    # realized by the throttle itself. See gap_policy.interfunction_sequence_steps.
    pressure_sequence_required = requires_pressure_sequence_valve(requirements)
    interfunction_phase_ids = {
        str(step.get("phase_id")) for step in interfunction_sequence_steps(requirements)
    }
    position_sequence_required = any(step.get("trigger") == "position" for step in sequence_steps)
    sequenced_functions = {fid for item in sequence_steps for fid in item.get("function_ids", [])}
    for step in sequence_steps:
        if (
            step.get("hydraulically_enforced")
            and len(sequenced_functions) > 1
            and (not step.get("phase_id") or step.get("trigger", "unspecified") == "unspecified")
        ):
            issues.append(_issue("SEQUENCE_STEP_UNTYPED", f"Sequence step {step.get('order')} lacks a phase_id or typed trigger.", scope="requirements", related=step.get("function_ids", [])))
        if step.get("hydraulically_enforced") and step.get("trigger") in {"unspecified", "external_command"}:
            issues.append(_issue("HYDRAULIC_SEQUENCE_TRIGGER_UNDEFINED", f"Sequence step {step.get('order')} is hydraulically enforced but has no hydraulic trigger.", scope="requirements", related=step.get("function_ids", [])))
        phase_id = step.get("phase_id")
        predecessor = step.get("predecessor_phase_id")
        if phase_id and phase_id in config_by_phase:
            declared_active = set(config_by_phase[phase_id].get("expected_active_function_ids") or [])
            missing_active = set(step.get("function_ids") or []) - declared_active
            if missing_active:
                issues.append(
                    _issue(
                        "SEQUENCE_ACTIVE_FUNCTION_MISMATCH",
                        f"Sequence phase {phase_id!r} omits expected active functions {sorted(missing_active)}.",
                        scope="requirements",
                        related=[phase_id, *missing_active],
                    )
                )
        # A position trigger always needs something to change state at that
        # position - a cam-operated bypass cannot be a throttle characteristic -
        # so intra-function position steps still require proof. A *pressure*
        # trigger inside one actuator is realized by the throttle itself and
        # must not demand a valve transition.
        intra_function_pressure_step = (
            step.get("trigger") == "pressure"
            and str(step.get("phase_id")) not in interfunction_phase_ids
        )
        if (
            step.get("hydraulically_enforced")
            and step.get("trigger") in {"pressure", "position"}
            and not intra_function_pressure_step
        ):
            if not predecessor or predecessor not in states_by_phase or phase_id not in states_by_phase:
                issues.append(
                    _issue(
                        "SEQUENCE_PREDECESSOR_STATE_MISSING",
                        f"Hydraulically enforced phase {phase_id!r} lacks a validated predecessor phase state.",
                        scope="requirements",
                        related=[phase_id, predecessor],
                    )
                )
            else:
                valve_type = "sequence_valve" if step.get("trigger") == "pressure" else "position_valve"
                changed = False
                for component_id, entry in entry_by_id.items():
                    if entry.get("type") != valve_type:
                        continue
                    before = states_by_phase[predecessor].get(component_id)
                    after = states_by_phase[phase_id].get(component_id)
                    if step.get("trigger") == "pressure":
                        changed = changed or (before == "closed" and after == "open")
                    else:
                        changed = changed or (before != after and before is not None and after is not None)
                if not changed:
                    issues.append(
                        _issue(
                            "SEQUENCE_TRIGGER_STATE_CHANGE_NOT_PROVEN",
                            f"Phase transition {predecessor!r} -> {phase_id!r} does not change a {valve_type} in the required way.",
                            related=[predecessor, phase_id],
                        )
                    )
    if pressure_sequence_required and "sequence_valve" not in types_present:
        issues.append(_issue("PRESSURE_SEQUENCE_VALVE_MISSING", "A pressure-triggered phase is required but no sequence valve is selected.", scope="selection"))
    if position_sequence_required and "position_valve" not in types_present:
        issues.append(_issue("POSITION_SEQUENCE_VALVE_MISSING", "A position-triggered phase is required but no position-operated valve is selected.", scope="selection"))
    if "sequence_valve" in types_present and not pressure_sequence_required:
        issues.append(_issue("UNJUSTIFIED_SEQUENCE_VALVE", "A sequence valve is selected without any pressure-triggered sequence requirement.", scope="selection", related=[component.get("id") for component in components if component.get("comp_type") == "sequence_valve"]))
    if "position_valve" in types_present and not position_sequence_required and "two_speed_force_switching" not in drivers:
        issues.append(
            _issue(
                "UNJUSTIFIED_POSITION_VALVE",
                "A position-operated valve is selected without a position-triggered phase requirement.",
                scope="selection",
                related=[component.get("id") for component in components if component.get("comp_type") == "position_valve"],
            )
        )

    throttled_realizations = {
        "load_sensitive_throttled",
        "adjustable_throttled",
        "load_independent_throttled",
    }
    regulated_paths = {
        (
            str(function.get("id")),
            str(phase.get("metered_chamber")),
            "pressure_comp_flow_control"
            if phase.get("speed_realization") == "load_independent_throttled"
            else "one_way_flow_control",
        )
        for function in requirements.get("functions", [])
        for phase in function.get("motion_phases", [])
        if phase.get("speed_realization") in throttled_realizations
    }
    flow_controls = [
        component
        for component in components
        if component.get("comp_type") in {"one_way_flow_control", "pressure_comp_flow_control"}
    ]
    if flow_controls and not regulated_paths:
        issues.append(
            _issue(
                "UNJUSTIFIED_FLOW_CONTROL",
                "Flow-control components are selected even though every phase is sizing-only, unrestricted, or regenerative.",
                scope="selection",
                related=[component.get("id") for component in flow_controls],
            )
        )
    elif len(flow_controls) > len(regulated_paths):
        issues.append(
            _issue(
                "EXCESS_FLOW_CONTROL_COMPLEXITY",
                f"{len(flow_controls)} flow-control components serve only {len(regulated_paths)} distinct typed metering paths.",
                severity="warning",
                scope="selection",
                related=[component.get("id") for component in flow_controls],
            )
        )

    global_pressure = ((requirements.get("global_constraints") or {}).get("max_system_pressure") or {}).get("value")
    reduced_functions = []
    if isinstance(global_pressure, (int, float)):
        reduced_functions = [
            str(function.get("id"))
            for function in requirements.get("functions", [])
            if function.get("branch_pressure_limit_required")
            and isinstance((function.get("max_working_pressure") or {}).get("value"), (int, float))
            and float(function["max_working_pressure"]["value"]) < float(global_pressure)
        ]
    if reduced_functions and "pressure_reducing_valve" not in types_present:
        issues.append(
            _issue(
                "MISSING_BRANCH_PRESSURE_REDUCTION",
                "A function has a lower pressure ceiling than the overall system but no pressure-reducing valve is selected.",
                scope="selection",
                related=reduced_functions,
            )
        )

    pump_count = sum(component.get("comp_type") == "pump" for component in components)
    design_text = f"{design.get('design_narrative', '')} {design.get('design_decisions', [])}".casefold()
    hi_lo = pump_count >= 2 and any(word in design_text for word in ("hi-lo", "high-low", "two-pump"))
    if hi_lo and "unloading_valve" not in types_present:
        issues.append(_issue("HI_LO_UNLOADING_VALVE_MISSING", "The selected hi-lo two-pump pattern has no true unloading valve.", scope="selection"))
    if hi_lo and "check_valve" not in types_present:
        issues.append(_issue("HI_LO_CHECK_VALVE_MISSING", "The selected hi-lo two-pump pattern has no plain pump-combining check valve.", scope="selection"))

    implemented_ids = {item.get("function_id") for item in design.get("function_implementations", [])}
    missing_implementations = sorted(_function_ids(requirements) - implemented_ids)
    if missing_implementations:
        issues.append(_issue("MISSING_FUNCTION_TRACEABILITY", f"No function_implementation is provided for: {missing_implementations}.", scope="requirements", related=missing_implementations))
    topology_gaps: list[dict[str, Any]] = []
    for gap in design.get("catalog_gaps", []):
        blocking, explanation = classify_catalog_gap(gap)
        if blocking:
            topology_gaps.append(gap)
        elif gap.get("scope", "topology") == "topology":
            issues.append(
                _issue(
                    "MISCLASSIFIED_CATALOG_GAP",
                    f"{gap.get('capability')}: {explanation} Use EvidenceGap when corroboration is missing.",
                    severity="warning",
                    scope="research",
                    related=[gap.get("capability")],
                )
            )
    if topology_gaps:
        issues.append(_issue("BLOCKING_TOPOLOGY_CATALOG_GAP", "A required generic topology class is absent; non-equivalent workarounds cannot validate.", scope="selection", related=[gap.get("capability") for gap in topology_gaps]))
    for gap in design.get("evidence_gaps", []):
        # v0.4.0: the model no longer decides on its own whether an evidence gap
        # blocks.  See gap_policy.classify_evidence_gap for the reasoning.
        blocking, explanation = classify_evidence_gap(gap)
        blocking = blocking and bool(gap.get("blocking"))
        description = f"Evidence gap for {gap.get('capability')}: {gap.get('reason')}"
        if gap.get("blocking") and not blocking:
            description += f" Downgraded to advisory: {explanation}"
        issues.append(
            _issue(
                "BLOCKING_EVIDENCE_GAP" if blocking else "NONBLOCKING_EVIDENCE_GAP",
                description,
                severity="error" if blocking else "warning",
                scope="research",
                related=gap.get("related_function_ids", []),
            )
        )

    coverage_codes = {"FUNCTION_HAS_NO_ACTUATOR", "MISSING_DRIVER_CAPABILITY", "LOAD_LOCK_COMPONENT_MISSING", "COUNTERBALANCE_COMPONENT_MISSING", "PRESSURE_SEQUENCE_VALVE_MISSING", "POSITION_SEQUENCE_VALVE_MISSING", "UNJUSTIFIED_SEQUENCE_VALVE", "UNJUSTIFIED_POSITION_VALVE", "UNJUSTIFIED_FLOW_CONTROL", "SEQUENCE_STEP_UNTYPED", "HYDRAULIC_SEQUENCE_TRIGGER_UNDEFINED", "SEQUENCE_ACTIVE_FUNCTION_MISMATCH", "SEQUENCE_PREDECESSOR_STATE_MISSING", "SEQUENCE_TRIGGER_STATE_CHANGE_NOT_PROVEN", "HI_LO_UNLOADING_VALVE_MISSING", "HI_LO_CHECK_VALVE_MISSING", "MISSING_BRANCH_PRESSURE_REDUCTION", "MISSING_FUNCTION_TRACEABILITY", "BLOCKING_TOPOLOGY_CATALOG_GAP", "BLOCKING_EVIDENCE_GAP", "UNAUTHORIZED_SERIES_ACTUATORS", "RIGID_PLATEN_REQUIRES_PARALLEL_CYLINDERS", "RIGID_PLATEN_PARALLEL_BRANCHES_NOT_PROVEN", "SYNCHRONIZED_ACTUATOR_COUNT_MISMATCH", "FLOW_DIVIDER_COMBINER_MISSING", "FLOW_DIVIDER_REQUIRES_PARALLEL_ACTUATORS", "SERIES_ACTUATOR_CHAIN_MISSING", "SERIES_CYLINDER_RATIO_NOT_PROVEN"}
    checks.append(ValidationCheck(name="topology_requirement_and_pattern_coverage", passed=not any(item.code in coverage_codes for item in issues), details="Capability classes, typed sequencing, synchronization strategy, hi-lo semantics, traceability, and catalog gaps checked."))

    unique: list[TopologyIssue] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in issues:
        key = (item.code, item.description, tuple(item.related))
        if key not in seen:
            unique.append(item)
            seen.add(key)
    verdict = "invalid" if any(item.severity == "error" for item in unique) else "valid"
    return DeterministicValidation(verdict=verdict, issues=unique, checks=checks)


def repair_scope(issues: list[dict[str, Any]] | list[TopologyIssue]) -> str:
    normalized = [item.model_dump() if hasattr(item, "model_dump") else item for item in issues]
    errors = [item for item in normalized if item.get("severity") == "error"]
    if not errors:
        return "none"
    scopes = {item.get("scope") for item in errors}
    # Upstream contradictions must be repaired before asking a component or
    # netlist agent to work around them.
    if "requirements" in scopes:
        return "requirements"
    if scopes == {"wiring"}:
        return "wiring"
    if scopes.intersection({"selection", "safety"}):
        return "selection"
    if "research" in scopes:
        return "research"
    return "selection"
