"""A one-off transient integration, to bound what the quasi-static model omits.

Every certificate in this system rests on an assumption that is never argued for
inside the certificate itself: that each phase can be judged at a steady
operating point. That is a modelling choice, and a paper claiming verification
has to say what it costs.

So this module integrates the same circuit properly - piston mass accelerating
under the same force balance, fluid compliance charging the trapped volumes, the
relief opening and reseating on its own - and compares the settled result and the
transit time against what the quasi-static solve predicted. It is not part of the
verification loop and is not run per design. It exists to be run once per problem
and quoted.

What it can and cannot say
--------------------------
It bounds *this* omission: the time spent away from the steady point, and the
overshoot on the way. It does not model cavitation, temperature, stick-slip, hose
expansion or valve spool dynamics, and it should not be described as though it
did. The honest claim is narrow: for the phases in this benchmark, the settled
velocity agrees with the quasi-static solve to within a stated tolerance, and the
approach to it occupies a stated fraction of the stroke.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .laws import BULK_MODULUS, RHO
from .network import PhaseModel, build_phase_model
from .quantities import Q
from .solve import solve_phase


# A steel-and-hose line has more compliance than the oil alone. Softening the
# effective bulk modulus is the usual allowance and makes the transient slower,
# which is the conservative direction for a claim about settling time.
EFFECTIVE_BULK = 0.6 * BULK_MODULUS

# Trapped volume per side, as a length of line plus the cylinder chamber. Stated
# rather than derived because the topology carries no lengths; 2 m is a normal
# machine-frame run and the sensitivity to it is reported by the sweep below.
LINE_LENGTH_M = 2.0
LINE_BORE_M = 0.016


@dataclass
class TransientResult:
    phase_id: str
    quasi_static_velocity_m_s: float
    settled_velocity_m_s: float
    peak_velocity_m_s: float
    settling_time_s: float
    settling_distance_m: float
    stroke_m: float | None
    relief_opened: bool
    steps: int
    diverged: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def velocity_error(self) -> float:
        if not self.quasi_static_velocity_m_s:
            return 0.0
        return ((self.settled_velocity_m_s - self.quasi_static_velocity_m_s)
                / self.quasi_static_velocity_m_s)

    @property
    def overshoot(self) -> float:
        if not self.settled_velocity_m_s:
            return 0.0
        return (self.peak_velocity_m_s - self.settled_velocity_m_s) / self.settled_velocity_m_s

    @property
    def stroke_fraction_settling(self) -> float | None:
        if not self.stroke_m:
            return None
        return self.settling_distance_m / self.stroke_m

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "quasi_static_velocity_m_s": self.quasi_static_velocity_m_s,
            "settled_velocity_m_s": self.settled_velocity_m_s,
            "peak_velocity_m_s": self.peak_velocity_m_s,
            "velocity_error": self.velocity_error,
            "overshoot": self.overshoot,
            "settling_time_s": self.settling_time_s,
            "settling_distance_m": self.settling_distance_m,
            "stroke_m": self.stroke_m,
            "stroke_fraction_settling": self.stroke_fraction_settling,
            "relief_opened": self.relief_opened,
            "steps": self.steps,
            "diverged": self.diverged,
            "notes": list(self.notes),
        }


def _moving_mass(model: PhaseModel, load_n: float) -> float:
    """Mass accelerated by the phase.

    The briefs give loads, not masses. Where the load is a cutting or clamping
    resistance the moving mass is the slide itself, and taking it as the load
    divided by g is the standard stand-in: it makes the acceleration phase as
    long as the stated forces can justify, which is the conservative direction
    for a claim that the transient is short.
    """
    equivalent = abs(load_n) / 9.81
    # A floor so a light phase does not integrate as an impulse.
    area_based = 1500.0 * model.supply_area.value * 10.0
    return max(equivalent, area_based, 20.0)


def _chamber_volume(area_m2: float, stroke_m: float | None) -> float:
    """Trapped volume on one side: the line run plus half the swept chamber.

    Half, because mid-stroke is the representative position and the compliance
    of a chamber is proportional to how much oil is in it. A fixed chamber
    length was used here at first and it made short-stroke cylinders look five
    times more compliant than they are, which pushed their settling times out by
    an order of magnitude.
    """
    line = math.pi * LINE_BORE_M**2 / 4.0 * LINE_LENGTH_M
    swept = area_m2 * (stroke_m if stroke_m else 0.25) * 0.5
    return line + swept


def _path_drop(elements, flow: float) -> float:
    return sum(element.pressure_drop(flow) for element in elements)


def _path_ceiling(elements, default: float) -> float:
    ceiling = default
    for element in elements:
        if element.kind == "pressure_setting" and element.set_pressure_pa > 0:
            ceiling = min(ceiling, element.set_pressure_pa)
    return ceiling


def _set_flow(elements) -> float | None:
    for element in elements:
        if element.kind == "flow_setting" and element.set_flow_m3s > 0:
            return element.set_flow_m3s
    return None


def integrate_phase(topology: dict, requirements: dict, phase_id: str, sizing: dict,
                    throttle_k: dict[str, float], *, max_time_s: float = 8.0,
                    line_length_m: float = LINE_LENGTH_M,
                    moving_mass_kg: float | None = None) -> TransientResult | None:
    """Integrate one phase from rest until the velocity settles.

    Three states: piston velocity, and the pressure in each chamber. Flow into
    the supply chamber is what the pump delivers minus what the relief passes
    minus what the piston swallows; the chamber pressure follows from the net
    flow against the fluid's compliance. The exhaust chamber is the mirror of it.
    Explicit integration with a step small enough for the stiffest of the three.
    """
    model = build_phase_model(topology, requirements, phase_id, sizing, throttle_k=throttle_k)
    if model is None:
        return None
    steady = solve_phase(model)
    if not steady.feasible:
        return None

    global LINE_LENGTH_M
    saved_length, LINE_LENGTH_M = LINE_LENGTH_M, line_length_m
    try:
        return _integrate(model, steady, requirements, phase_id, max_time_s, moving_mass_kg)
    finally:
        LINE_LENGTH_M = saved_length


def _integrate(model: PhaseModel, steady, requirements: dict, phase_id: str,
               max_time_s: float, moving_mass_kg: float | None = None) -> TransientResult:
    """Two states: piston velocity and supply chamber pressure.

    The exhaust side is treated as quasi-static - back pressure is whatever the
    return path drops at the instantaneous flow - because return volumes
    discharge to tank through passages an order of magnitude less restrictive
    than the metering, so their charging time is short against the supply
    chamber's. That is a stated simplification, not an oversight, and it is the
    reason this is a *cross-check* rather than a second verifier.

    Settling is detected from the acceleration alone, never from proximity to
    the quasi-static answer. Stopping the integration when it got near the number
    it is meant to be testing would guarantee agreement and prove nothing - which
    is exactly what the first version of this function did.
    """
    supply_area = model.supply_area.value
    exhaust_area = model.exhaust_area.value
    load = model.load.value
    mass = moving_mass_kg if moving_mass_kg is not None else _moving_mass(model, load)
    relief = model.relief_pressure.value
    pump_flow = model.pump_flow.value
    supply_ceiling = _path_ceiling(model.supply_elements, relief)
    metered_flow = _set_flow(model.exhaust_elements) or _set_flow(model.supply_elements)
    # A compensated control passes its set flow whatever the drop across it, so
    # it caps the piston velocity outright; the compensator absorbs the surplus
    # pressure. Modelling its internal spool dynamics would need a gain nothing
    # in the topology supplies, and the charging transient dominates anyway.
    velocity_cap = None
    if metered_flow is not None:
        metered_on_exhaust = _set_flow(model.exhaust_elements) is not None
        area = exhaust_area if metered_on_exhaust else supply_area
        velocity_cap = metered_flow / max(area, 1e-12)

    volume_supply = _chamber_volume(supply_area, _stroke_of(requirements, phase_id))

    stiffness = EFFECTIVE_BULK * supply_area**2 / max(volume_supply, 1e-9)
    natural = math.sqrt(stiffness / mass)
    step = min(1.0 / (40.0 * natural), 1e-4)

    velocity = 0.0
    pressure_supply = 0.0
    time = 0.0
    distance = 0.0
    peak = 0.0
    relief_opened = False
    settled_at: float | None = None
    settled_distance = 0.0
    settled_velocity: float | None = None
    quiet_since: float | None = None
    steps = 0
    diverged = False

    # "Settled" means the velocity is changing by less than a tenth of a percent
    # of its own value per millisecond, and has been for 20 ms.
    quiet_slope = 0.001 / 1e-3
    quiet_hold = 0.02

    while time < max_time_s:
        steps += 1
        # The relief passes whatever it must to hold the supply pressure at the
        # setting, and nothing below it. A steep finite slope rather than an
        # ideal clamp, so the state stays differentiable.
        over = pressure_supply - relief
        relief_flow = min(max(0.0, over) * pump_flow / max(0.02 * relief, 1.0),
                          pump_flow * 4.0)
        if relief_flow > 1e-12:
            relief_opened = True

        swallowed = velocity * supply_area
        net_in = pump_flow - relief_flow - swallowed
        pressure_supply += step * EFFECTIVE_BULK / volume_supply * net_in
        pressure_supply = max(0.0, min(pressure_supply, supply_ceiling))

        expelled = max(velocity * exhaust_area, 0.0)
        pressure_exhaust = min(_path_drop(model.exhaust_elements, expelled), supply_ceiling)
        supply_loss = _path_drop(model.supply_elements, max(swallowed, 0.0))
        net_force = ((pressure_supply - supply_loss) * supply_area
                     - pressure_exhaust * exhaust_area) * model.mechanical_efficiency - load
        acceleration = net_force / mass
        previous = velocity
        velocity = max(0.0, velocity + step * acceleration)
        if velocity_cap is not None:
            velocity = min(velocity, velocity_cap)
        distance += step * velocity
        peak = max(peak, velocity)
        time += step

        if not math.isfinite(velocity) or not math.isfinite(pressure_supply):
            diverged = True
            break

        slope = abs(velocity - previous) / step
        if velocity > 1e-9 and slope < quiet_slope * velocity:
            if quiet_since is None:
                quiet_since = time
            elif time - quiet_since >= quiet_hold and settled_at is None:
                settled_at = quiet_since
                settled_distance = distance
                settled_velocity = velocity
                break
        else:
            quiet_since = None

    stroke = _stroke_of(requirements, phase_id)
    result = TransientResult(
        phase_id=phase_id,
        quasi_static_velocity_m_s=steady.velocity_m_s,
        settled_velocity_m_s=settled_velocity if settled_velocity is not None else velocity,
        peak_velocity_m_s=peak,
        settling_time_s=settled_at if settled_at is not None else time,
        settling_distance_m=settled_distance if settled_at is not None else distance,
        stroke_m=stroke,
        relief_opened=relief_opened,
        steps=steps,
        diverged=diverged,
    )
    if settled_at is None:
        result.notes.append(
            "the velocity had not stopped changing when the integration window ran out; "
            "the figure reported is where it had reached, not a settled value")
    return result


def _stroke_of(requirements: dict, phase_id: str) -> float | None:
    for function in requirements.get("functions", []) or []:
        for phase in function.get("motion_phases") or []:
            if str(phase.get("id")) != str(phase_id):
                continue
            distance = phase.get("distance") or {}
            if isinstance(distance.get("value"), (int, float)):
                return Q.of(float(distance["value"]), str(distance.get("unit") or "mm")).value
    return None


def cross_check(topology: dict, requirements: dict, sizing: dict,
                throttle_k: dict[str, float],
                phase_ids: list[str] | None = None) -> list[TransientResult]:
    """Integrate every motion phase of one design."""
    if phase_ids is None:
        phase_ids = [
            str(phase.get("id"))
            for function in requirements.get("functions", []) or []
            for phase in function.get("motion_phases") or []
            if str(phase.get("motion")) in {"extend", "retract"}
        ]
    results = []
    for phase_id in phase_ids:
        outcome = integrate_phase(topology, requirements, phase_id, sizing, throttle_k)
        if outcome is not None:
            results.append(outcome)
    return results


def summarise_cross_check(results: list[TransientResult]) -> dict[str, Any]:
    """The two numbers the paper needs, and the caveat that goes with them.

    The first is how far the settled velocity departs from the quasi-static one:
    that is the error the modelling assumption introduces where the assumption
    holds. The second is where it does not hold - phases whose acceleration
    occupies a large part of the stroke never reach a steady point at all, so a
    steady-state velocity criterion on them is answering a question the motion
    never asks.
    """
    usable = [item for item in results if not item.diverged and not item.notes]
    errors = [abs(item.velocity_error) for item in usable]
    fractions = [
        item.stroke_fraction_settling for item in usable
        if item.stroke_fraction_settling is not None
    ]
    never_settle = [
        item.phase_id for item in usable
        if item.stroke_fraction_settling is not None and item.stroke_fraction_settling >= 1.0
    ]
    mostly_transient = [
        item.phase_id for item in usable
        if item.stroke_fraction_settling is not None and 0.2 <= item.stroke_fraction_settling < 1.0
    ]
    return {
        "phases": len(results),
        "usable": len(usable),
        "unsettled": [item.phase_id for item in results if item.notes],
        "diverged": [item.phase_id for item in results if item.diverged],
        "max_abs_velocity_error": max(errors) if errors else None,
        "median_abs_velocity_error": sorted(errors)[len(errors) // 2] if errors else None,
        "max_overshoot": max((item.overshoot for item in usable), default=None),
        "median_stroke_fraction_settling": (
            sorted(fractions)[len(fractions) // 2] if fractions else None),
        "phases_never_reaching_steady_state": never_settle,
        "phases_mostly_transient": mostly_transient,
    }


def sensitivity_to_line_length(topology: dict, requirements: dict, phase_id: str,
                               sizing: dict, throttle_k: dict[str, float],
                               lengths_m: tuple[float, ...] = (1.0, 2.0, 4.0)
                               ) -> list[dict[str, Any]]:
    """How much the answer depends on the one number this model had to invent.

    Line length is not in the topology and had to be assumed. Quoting a settling
    time without showing its sensitivity to that assumption would be presenting a
    guess as a measurement.
    """
    rows = []
    for length in lengths_m:
        outcome = integrate_phase(topology, requirements, phase_id, sizing, throttle_k,
                                  line_length_m=length)
        if outcome is None:
            continue
        rows.append({
            "line_length_m": length,
            "settling_time_s": outcome.settling_time_s,
            "velocity_error": outcome.velocity_error,
            "overshoot": outcome.overshoot,
        })
    return rows


def sensitivity_to_moving_mass(topology: dict, requirements: dict, phase_id: str,
                               sizing: dict, throttle_k: dict[str, float],
                               factors: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.0)
                               ) -> list[dict[str, Any]]:
    """How much the answer depends on the *other* number that had to be invented.

    This one matters more than the line length and needs saying plainly. The
    briefs state loads, not masses, and the stand-in used here treats a stated
    load as though it were an accelerated weight. For a slide being pushed along
    that is roughly right. For a clamp pressing on a workpiece it is wrong: the
    12 kN is a reaction, not an inertia, and nothing like that mass is actually
    being accelerated.

    So any phase this study reports as "mostly transient" has to be read together
    with this sweep. Where the settling time collapses as the mass falls, the
    finding is an artefact of the stand-in rather than a property of the design.
    """
    rows = []
    load = 0.0
    model = build_phase_model(topology, requirements, phase_id, sizing, throttle_k=throttle_k)
    if model is not None:
        load = abs(model.load.value)
    nominal = _moving_mass(model, load) if model is not None else 0.0
    for factor in factors:
        outcome = integrate_phase(topology, requirements, phase_id, sizing, throttle_k,
                                  moving_mass_kg=max(nominal * factor, 1.0))
        if outcome is None:
            continue
        rows.append({
            "mass_factor": factor,
            "moving_mass_kg": max(nominal * factor, 1.0),
            "settling_time_s": outcome.settling_time_s,
            "stroke_fraction_settling": outcome.stroke_fraction_settling,
            "velocity_error": outcome.velocity_error,
        })
    return rows
