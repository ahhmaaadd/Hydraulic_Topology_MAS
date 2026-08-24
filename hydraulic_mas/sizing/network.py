"""Build one algebraic phase model from a certified topology and a sizing.

The model is deliberately a *path* model rather than a general network solve.
That is not a simplification for convenience: the topology stage has already
proved, for this phase's valve states, that oil flows from the pump to one
chamber and from the other chamber to tank along specific directed paths. Those
paths are the model. Parallel actuators joined by a rigid structure are folded
into one equivalent actuator, which is exact because the structure forces equal
velocity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..validation import phase_flow_paths
from .laws import Element, check_element, valve_element
from .quantities import Q


# How each generic class behaves once oil is flowing through it in a given
# direction. The kind is chosen by the *edge kind* the validator proved, not by
# the component type alone: a one-way flow control is a throttle in its metered
# direction and a plain check in its free direction.
_METERED_KINDS = {"metered", "metered_compensated"}
_FREE_KINDS = {"free_reverse", "free_check", "pilot_released", "bypass"}


@dataclass
class PhaseModel:
    """Everything needed to solve one motion phase."""

    phase_id: str
    motion: str
    function_id: str
    supply_area: Q
    exhaust_area: Q
    load: Q
    supply_elements: list[Element] = field(default_factory=list)
    exhaust_elements: list[Element] = field(default_factory=list)
    pump_flow: Q = field(default_factory=lambda: Q.of(0.0, "l/min"))
    relief_pressure: Q = field(default_factory=lambda: Q.of(0.0, "bar"))
    mechanical_efficiency: float = 0.90
    cylinder_ids: tuple[str, ...] = ()

    @property
    def area_ratio(self) -> float:
        return self.supply_area.value / self.exhaust_area.value

    def metered_element(self) -> Element | None:
        for element in self.exhaust_elements + self.supply_elements:
            if element.throttle_k is not None or element.kind == "flow_setting":
                return element
        return None


def _element_for(component_id: str, comp_type: str, edge_kind: str,
                 sizing: dict, throttle_k: dict[str, float]) -> Element | None:
    """Turn one proved path edge into a constitutive element."""
    record = sizing.get(component_id, {})
    rated = float(record.get("rated_flow_lpm", 60.0))

    if comp_type in {"one_way_flow_control", "pressure_comp_flow_control"}:
        if edge_kind in _METERED_KINDS:
            if comp_type == "pressure_comp_flow_control":
                # A compensated control sets the flow and absorbs the rest as drop.
                return Element(component_id, comp_type, "flow_setting",
                               set_flow_m3s=float(record.get("set_flow_m3s", 0.0)))
            return Element(component_id, comp_type, "resistive",
                           throttle_k=throttle_k.get(component_id))
        return check_element(component_id, comp_type, rated_flow_lpm=rated)

    if comp_type == "pressure_reducing_valve":
        if edge_kind in _FREE_KINDS:
            return check_element(component_id, comp_type, rated_flow_lpm=rated)
        return Element(component_id, comp_type, "pressure_setting",
                       set_pressure_pa=float(record.get("setting_pa", 0.0)))

    if comp_type in {"check_valve", "pilot_check_valve", "single_pilot_check_valve"}:
        return check_element(component_id, comp_type, rated_flow_lpm=rated)

    if comp_type in {"dcv", "sequence_valve", "unloading_valve", "counterbalance_valve",
                     "position_valve", "flow_divider_combiner"}:
        return valve_element(component_id, comp_type, rated,
                             reference_drop_bar=float(record.get("reference_drop_bar", 3.0)))

    # Tanks, pumps and relief valves are boundary conditions, not path resistances.
    return None


def build_phase_model(
    topology: dict,
    requirements: dict,
    phase_id: str,
    sizing: dict,
    *,
    throttle_k: dict[str, float] | None = None,
    mechanical_efficiency: float = 0.90,
) -> PhaseModel | None:
    """Assemble the phase model, or ``None`` for a non-motion phase."""
    paths = phase_flow_paths(topology, requirements, phase_id)
    if paths is None:
        return None

    throttle_k = throttle_k or {}

    # Load and areas, summed over every cylinder the function drives.
    supply_area = 0.0
    exhaust_area = 0.0
    for cylinder_id in paths["cylinders"]:
        record = sizing.get(cylinder_id, {})
        cap = float(record.get("cap_area_m2", 0.0))
        annulus = float(record.get("annulus_area_m2", 0.0))
        if paths["motion"] == "extend":
            supply_area += cap
            exhaust_area += annulus
        else:
            supply_area += annulus
            exhaust_area += cap

    phase = _find_phase(requirements, phase_id)
    load_value = 0.0
    if phase and isinstance((phase.get("force") or {}).get("value"), (int, float)):
        load_value = Q.of(float(phase["force"]["value"]), str(phase["force"].get("unit") or "N")).value

    # Elements are taken from the first cylinder's proved paths; parallel branches
    # of a rigid platen traverse the same components.
    first = next(iter(paths["cylinders"].values()))
    supply_elements = [
        element
        for component_id, comp_type, edge_kind in first["supply_elements"]
        if (element := _element_for(component_id, comp_type, edge_kind, sizing, throttle_k))
    ]
    exhaust_elements = [
        element
        for component_id, comp_type, edge_kind in first["exhaust_elements"]
        if (element := _element_for(component_id, comp_type, edge_kind, sizing, throttle_k))
    ]

    supply = sizing.get("__supply__", {})
    return PhaseModel(
        phase_id=phase_id,
        motion=paths["motion"],
        function_id=paths["function_id"],
        supply_area=Q(supply_area, (2, 0, 0)),
        exhaust_area=Q(exhaust_area, (2, 0, 0)),
        load=Q(load_value, (1, 1, -2)),
        supply_elements=supply_elements,
        exhaust_elements=exhaust_elements,
        pump_flow=Q(float(supply.get("flow_m3s", 0.0)), (3, 0, -1)),
        relief_pressure=Q(float(supply.get("relief_pa", 0.0)), (-1, 1, -2)),
        mechanical_efficiency=mechanical_efficiency,
        cylinder_ids=tuple(paths["cylinders"]),
    )


def _find_phase(requirements: dict, phase_id: str) -> dict | None:
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) == str(phase_id):
                return phase
    return None
