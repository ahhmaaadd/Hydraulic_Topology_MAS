"""Generic component-class catalog for the seven topology benchmarks.

This is intentionally not a procurement or sizing catalog. Every entry is a
hydraulic function that may change a circuit topology. Manufacturer names,
model numbers, dimensions, pressure/flow ratings, prime movers, line hardware,
filters, coolers, gauges, manifolds and reservoir accessories belong to later
engineering phases and are deliberately absent here.

Branches are represented by multiple connections at a shared component port;
there is no tee or manifold component.
"""

CATALOG_TITLE = "Problems_7 generic topology component catalog"

# Retained for API compatibility with older callers. Topology classes are not
# tied to vendor documents, so there are no procurement sources in this phase.
SOURCES: dict[str, dict[str, str]] = {}


def _entry(
    name: str,
    comp_type: str,
    ports: list[str],
    port_types: dict[str, str],
    summary: str,
    *,
    capabilities: list[str],
    states: list[str] | None = None,
    actuation: str | None = None,
    fixed_paths: list[tuple[str, str, str]] | None = None,
    state_paths: dict[str, list[tuple[str, str, str]]] | None = None,
    default_state: str | None = None,
    requires_phase_state: bool = False,
    conditional_paths: list[dict] | None = None,
) -> dict:
    """Build one deliberately sizing-free component-class record."""
    value = {
        "name": name,
        "type": comp_type,
        "ports": ports,
        "port_types": port_types,
        "summary": summary,
        "capabilities": capabilities,
        "states": states or [],
        "fixed_paths": [
            {"from": source, "to": target, "kind": kind}
            for source, target, kind in (fixed_paths or [])
        ],
        "state_paths": {
            state: [
                {"from": source, "to": target, "kind": kind}
                for source, target, kind in paths
            ]
            for state, paths in (state_paths or {}).items()
        },
        "default_state": default_state,
        "requires_phase_state": requires_phase_state,
        "conditional_paths": conditional_paths or [],
        "draw_key": name,
    }
    if actuation:
        value["actuation"] = actuation
    return value


CATALOG = {
    "GENERIC_TANK": _entry(
        "Tank",
        "tank",
        ["S", "R"],
        {"S": "suction_out", "R": "return_in"},
        "Common open-circuit reservoir boundary for pump suction and hydraulic returns.",
        capabilities=["suction source", "common return sink"],
    ),
    "GENERIC_FIXED_DISPLACEMENT_PUMP": _entry(
        "Pump",
        "pump",
        ["S", "P"],
        {"S": "suction_in", "P": "pressure_out"},
        "Generic fixed-displacement hydraulic pump. Drive hardware and displacement are deferred to sizing.",
        capabilities=["positive-displacement supply"],
        fixed_paths=[("S", "P", "pump_source")],
    ),
    "GENERIC_PRESSURE_COMPENSATED_VARIABLE_PUMP": _entry(
        "Pressure-Compensated Variable Pump",
        "pump",
        ["S", "P"],
        {"S": "suction_in", "P": "pressure_out"},
        "Generic variable-delivery pump with pressure compensation for topologies that require standby flow reduction.",
        capabilities=["variable delivery", "pressure compensation", "standby unloading"],
        fixed_paths=[("S", "P", "pump_source")],
    ),
    "GENERIC_RELIEF_VALVE": _entry(
        "Relief Valve",
        "relief_valve",
        ["P", "T"],
        {"P": "pressure_in", "T": "tank_out"},
        "Normally closed pressure limiter that opens from P to T at its setting.",
        capabilities=["overpressure protection", "pressure limiting"],
        states=["normal: P blocked from T", "overpressure: P connected to T"],
        state_paths={
            "closed": [],
            "relieving": [("P", "T", "pressure_relief")],
        },
        default_state="closed",
    ),
    "GENERIC_4_3_SOLENOID_TANDEM_DCV": _entry(
        "4/3 Solenoid DCV, Tandem Center",
        "dcv",
        ["P", "T", "A", "B"],
        {"P": "pressure_in", "T": "tank_out", "A": "work", "B": "work"},
        "Four-way, three-position directional valve with pump-to-tank neutral and blocked work ports.",
        capabilities=["bidirectional actuator control", "neutral pump unloading", "neutral work-port blocking"],
        states=["extend: P-A and B-T", "neutral: P-T with A and B blocked", "retract: P-B and A-T"],
        actuation="solenoid operated, spring centered; electrical command is outside hydraulic topology",
        state_paths={
            "extend": [("P", "A", "directed"), ("B", "T", "directed")],
            "neutral": [("P", "T", "neutral_unloading")],
            "retract": [("P", "B", "directed"), ("A", "T", "directed")],
        },
        default_state="neutral",
        requires_phase_state=True,
    ),
    "GENERIC_4_3_SOLENOID_CLOSED_CENTER_DCV": _entry(
        "4/3 Solenoid DCV, Closed Center",
        "dcv",
        ["P", "T", "A", "B"],
        {"P": "pressure_in", "T": "tank_out", "A": "work", "B": "work"},
        "Four-way, three-position directional valve with all hydraulic ports blocked in neutral.",
        capabilities=["bidirectional actuator control", "closed-center neutral"],
        states=["extend: P-A and B-T", "neutral: P, T, A and B blocked", "retract: P-B and A-T"],
        actuation="solenoid operated, spring centered; electrical command is outside hydraulic topology",
        state_paths={
            "extend": [("P", "A", "directed"), ("B", "T", "directed")],
            "neutral": [],
            "retract": [("P", "B", "directed"), ("A", "T", "directed")],
        },
        default_state="neutral",
        requires_phase_state=True,
    ),
    "GENERIC_4_2_SOLENOID_DCV": _entry(
        "4/2 Solenoid DCV",
        "dcv",
        ["P", "T", "A", "B"],
        {"P": "pressure_in", "T": "tank_out", "A": "work", "B": "work"},
        "Four-way, two-position directional valve for extend/retract service without a neutral state.",
        capabilities=["bidirectional actuator control"],
        states=["extend: P-A and B-T", "retract: P-B and A-T"],
        actuation="solenoid operated; electrical command is outside hydraulic topology",
        state_paths={
            "extend": [("P", "A", "directed"), ("B", "T", "directed")],
            "retract": [("P", "B", "directed"), ("A", "T", "directed")],
        },
        default_state="extend",
        requires_phase_state=True,
    ),
    "GENERIC_DOUBLE_ACTING_CYLINDER": _entry(
        "Double-Acting Cylinder",
        "cylinder",
        ["Cap", "Rod"],
        {"Cap": "cap_end", "Rod": "rod_end"},
        "Generic double-acting linear actuator; bore, rod and stroke are deferred to sizing.",
        capabilities=["powered extension", "powered retraction"],
    ),
    "GENERIC_PRESSURE_COMPENSATED_ONE_WAY_FLOW_CONTROL": _entry(
        "Pressure-Compensated One-Way Speed Control",
        "pressure_comp_flow_control",
        ["A", "B"],
        {"A": "controlled_direction_in", "B": "controlled_direction_out"},
        "Adjustable pressure-compensated metering path from A to B with free reverse flow from B to A.",
        capabilities=["load-independent speed control", "adjustable metering", "free reverse flow"],
        states=["A-B: pressure-compensated metering", "B-A: free reverse check flow"],
        fixed_paths=[
            ("A", "B", "metered_compensated"),
            ("B", "A", "free_reverse"),
        ],
    ),
    "GENERIC_ONE_WAY_FLOW_CONTROL": _entry(
        "One-Way Speed Control",
        "one_way_flow_control",
        ["A", "B"],
        {"A": "controlled_direction_in", "B": "controlled_direction_out"},
        "Adjustable non-compensated throttle from A to B with free reverse flow from B to A.",
        capabilities=["load-dependent speed control", "adjustable metering", "free reverse flow"],
        states=["A-B: throttled flow", "B-A: free reverse check flow"],
        fixed_paths=[
            ("A", "B", "metered"),
            ("B", "A", "free_reverse"),
        ],
    ),
    "GENERIC_POSITION_OPERATED_BYPASS": _entry(
        "Position-Operated Bypass/Changeover",
        "position_valve",
        ["P", "A"],
        {"P": "bypass_in", "A": "bypass_out"},
        "Mechanically position-operated two-port bypass that changes motion phase without electrical sensing.",
        capabilities=["position-triggered phase change", "parallel rapid bypass"],
        states=["before trip: P connected to A", "after trip: P blocked from A"],
        actuation="mechanical position operator; the cam/roller is a property, not a separate component",
        state_paths={
            "open": [("P", "A", "bypass"), ("A", "P", "bypass")],
            "closed": [],
        },
        default_state="closed",
        requires_phase_state=True,
    ),
    "GENERIC_DUAL_PILOT_OPERATED_CHECK": _entry(
        "Dual Pilot-Operated Check Valve",
        "pilot_check_valve",
        ["A1", "A2", "B1", "B2"],
        {"A1": "valve_side", "A2": "actuator_side", "B1": "valve_side", "B2": "actuator_side"},
        "Cross-piloted dual load lock installed between a DCV and both actuator chambers.",
        capabilities=["bidirectional load holding", "low-leakage neutral hold", "commanded pilot release"],
        states=["supply direction free to actuator", "reverse flow blocked until opposite work line is pressurized"],
        fixed_paths=[
            ("A1", "A2", "free_check"),
            ("B1", "B2", "free_check"),
        ],
        conditional_paths=[
            {"from": "A2", "to": "A1", "kind": "pilot_released", "requires_pressure_at": "B1"},
            {"from": "B2", "to": "B1", "kind": "pilot_released", "requires_pressure_at": "A1"},
        ],
    ),
    "GENERIC_SINGLE_PILOT_OPERATED_CHECK": _entry(
        "Pilot-Operated Check Valve",
        "single_pilot_check_valve",
        ["V", "C", "X"],
        {"V": "valve_side", "C": "actuator_side", "X": "pilot_in"},
        "Single-line load lock with a separate hydraulic pilot release.",
        capabilities=["single-port load holding", "commanded pilot release"],
        states=["V-C: free flow", "C-V: blocked until X is pressurized"],
        fixed_paths=[("V", "C", "free_check")],
        conditional_paths=[
            {"from": "C", "to": "V", "kind": "pilot_released", "requires_pressure_at": "X"},
        ],
    ),
    "GENERIC_SEQUENCE_VALVE_WITH_REVERSE_CHECK": _entry(
        "Hydraulic Sequence Valve with Reverse Check",
        "sequence_valve",
        ["P", "A", "T"],
        {"P": "primary_in", "A": "sequenced_out", "T": "drain"},
        "Pressure-operated sequence valve that opens P to A after a hydraulic threshold and permits free reverse return.",
        capabilities=["pressure-triggered sequencing", "hydraulic interlock", "free reverse flow"],
        states=["below threshold: P blocked", "above threshold: P connected to A", "reverse: A connected to P through check"],
        state_paths={
            "closed": [("A", "P", "free_reverse")],
            "open": [("P", "A", "pressure_sequence"), ("A", "P", "free_reverse")],
        },
        default_state="closed",
        requires_phase_state=True,
    ),
    "GENERIC_PRESSURE_REDUCING_VALVE": _entry(
        "Pressure-Reducing Valve",
        "pressure_reducing_valve",
        ["P", "A", "T"],
        {"P": "pressure_in", "A": "reduced_pressure_out", "T": "drain"},
        "Three-way pressure-reducing valve for a lower-pressure actuator branch.",
        capabilities=["branch pressure reduction", "downstream pressure limiting"],
        states=["regulating: P connected to A as needed", "relieving: A connected to T as needed"],
        fixed_paths=[
            ("P", "A", "pressure_reduced"),
            ("A", "T", "downstream_relief"),
        ],
    ),
    "GENERIC_CHECK_VALVE": _entry(
        "Check Valve",
        "check_valve",
        ["P", "A"],
        {"P": "free_flow_in", "A": "free_flow_out"},
        "Plain one-way valve: free flow from P to A and blocked reverse flow from A to P.",
        capabilities=["one-way isolation", "reverse-flow prevention", "pump combining"],
        states=["P-A: free flow", "A-P: blocked"],
        fixed_paths=[("P", "A", "free_check")],
    ),
    "GENERIC_EXTERNALLY_PILOTED_UNLOADING_VALVE": _entry(
        "Externally Piloted Unloading Valve",
        "unloading_valve",
        ["P", "T", "X"],
        {"P": "pump_in", "T": "tank_out", "X": "external_pilot_in"},
        "Normally closed valve that unloads a pump from P to T when external pilot X reaches its setting.",
        capabilities=["true hi-lo pump unloading", "externally piloted unloading", "low-pressure recirculation"],
        states=["loaded: P blocked from T", "unloaded: P connected to T by pilot X"],
        state_paths={
            "loaded": [],
            "unloaded": [("P", "T", "unloading")],
        },
        default_state="loaded",
        requires_phase_state=True,
    ),
    "GENERIC_COUNTERBALANCE_VALVE": _entry(
        "Counterbalance/Overcenter Valve",
        "counterbalance_valve",
        ["V", "C", "X"],
        {"V": "valve_side", "C": "actuator_side", "X": "pilot_in"},
        "Load-control valve with free flow V to C and controlled, pilot-assisted reverse flow C to V.",
        capabilities=[
            "dynamic overrunning-load control",
            "static load holding",
            "hose-failure load retention",
            "pilot-assisted controlled lowering",
        ],
        states=["holding: C-V blocked", "controlled_release: C-V metered while pilot/load opens valve"],
        fixed_paths=[("V", "C", "free_check")],
        state_paths={
            "holding": [],
            "controlled_release": [("C", "V", "counterbalance_controlled")],
        },
        default_state="holding",
        requires_phase_state=True,
    ),
    "GENERIC_FLOW_DIVIDER_COMBINER": _entry(
        "Flow Divider/Combiner",
        "flow_divider_combiner",
        ["P", "A", "B"],
        {"P": "common", "A": "branch_1", "B": "branch_2"},
        "Bidirectional divider/combiner for hydraulically synchronized parallel actuator branches.",
        capabilities=["parallel flow division", "reverse flow combining", "hydraulic synchronization"],
        states=["divide: P supplies A and B", "combine: A and B return through P"],
        fixed_paths=[
            ("P", "A", "divided_flow"),
            ("P", "B", "divided_flow"),
            ("A", "P", "combined_flow"),
            ("B", "P", "combined_flow"),
        ],
    ),
    "GENERIC_PRESSURE_REDUCING_VALVE_WITH_REVERSE_CHECK": _entry(
        "Pressure-Reducing Valve with Reverse Check",
        "pressure_reducing_valve",
        ["P", "A", "T"],
        {"P": "pressure_in", "A": "reduced_pressure_out", "T": "drain"},
        "Three-way reducing valve with an integral check for unrestricted reverse return from A to P.",
        capabilities=["branch pressure reduction", "downstream pressure limiting", "free reverse flow"],
        states=["regulating: P-A", "relieving: A-T", "reverse: A-P through check"],
        fixed_paths=[
            ("P", "A", "pressure_reduced"),
            ("A", "T", "downstream_relief"),
            ("A", "P", "free_reverse"),
        ],
    ),
    "GENERIC_EXTERNALLY_PILOTED_SEQUENCE_VALVE": _entry(
        "Externally Piloted Sequence Valve with Reverse Check",
        "sequence_valve",
        ["P", "A", "X", "T"],
        {"P": "primary_in", "A": "sequenced_out", "X": "external_pilot_in", "T": "drain"},
        "Externally piloted sequence valve for a pressure trigger sensed separately from the sequenced branch.",
        capabilities=["external-pressure sequencing", "hydraulic interlock", "free reverse flow"],
        states=["closed: P-A blocked", "open: P-A enabled by X", "reverse: A-P through check"],
        state_paths={
            "closed": [("A", "P", "free_reverse")],
            "open": [("P", "A", "pressure_sequence"), ("A", "P", "free_reverse")],
        },
        default_state="closed",
        requires_phase_state=True,
    ),
}


FORBIDDEN_TOPOLOGY_TYPES = {
    "manifold",
    "tee",
    "pipe",
    "hose",
    "filter",
    "suction_strainer",
    "cooler",
    "pressure_gauge",
    "level_temperature_gauge",
    "temperature_sensor",
    "breather",
    "electric_motor",
    "coupling",
    "shaft",
    "pressure_switch",
}
