"""Prompts for the arms that size without the verification stack.

The catalog is handed over as *text*, which is the point of the A0 condition: the
model has the same series a tool would consult, so anything it gets wrong is not
missing information. It has the numbers and has to do something with them.

A0 and A0.5 receive identical prompts. The only difference between the arms is
whether arithmetic tools are bound to the call, which is what isolates "cannot
calculate" from "does not know".
"""

from __future__ import annotations

import json
from typing import Any

from ..sizing.catalog_sizing import (
    BORE_SERIES,
    DISPLACEMENT_SERIES,
    MOTOR_SERIES,
    RESERVOIR_SERIES,
    ROD_SERIES,
    VALVE_SIZES,
)
from ..sizing.contract import AcceptanceContract


CATALOG_TEXT = f"""\
STANDARD SERIES (use only these values)

Cylinder bores, mm (ISO 3320):
  {', '.join(f'{v:g}' for v in BORE_SERIES)}

Piston rods, mm (ISO 4395):
  {', '.join(f'{v:g}' for v in ROD_SERIES)}

Pump displacement, cm3/rev:
  {', '.join(f'{v:g}' for v in DISPLACEMENT_SERIES)}

Motor ratings, kW (IEC):
  {', '.join(f'{v:g}' for v in MOTOR_SERIES)}

Reservoir sizes, L:
  {', '.join(f'{v:g}' for v in RESERVOIR_SERIES)}

Directional valve nominal sizes and rated flow at 3 bar drop:
  {', '.join(f'{name} = {flow:g} L/min' for name, flow in VALVE_SIZES)}
"""

DESIGN_BASIS = """\
DESIGN BASIS (the same conventions the benchmark is written to)

  mechanical efficiency of a cylinder      eta_m = 0.90
  volumetric efficiency of a pump          eta_v = 0.90
  overall efficiency, pump plus drive      eta_o = 0.85
  prime mover speed                        1500 rpm
  reservoir                                about 3 x the pump delivery per minute
  directional valves rated                 at least 1.25 x the flow through them

  Extending, the cap side is pressurised and the annulus exhausts:
      p_cap * A_cap = F / eta_m + p_rod * A_ann
  Retracting, the two swap. The annulus is A_cap minus the rod area.
  Pump delivery is displacement x speed x eta_v.
  Shaft power is delivery x pressure / eta_o.
"""

INSTRUCTIONS = """\
Size this circuit completely. The topology is fixed and already verified - do not
change it, add components, or remove any. Choose:

  * bore and rod for every cylinder
  * pump displacement
  * the relief valve setting
  * settings for every pressure valve in the circuit
  * the flow each speed control is to pass, in L/min, and the phase it is for
  * motor rating, reservoir size, and directional valve nominal size

Then state, for every motion phase, the velocity and force you believe your
design will actually achieve, and the pressure it will run at. Be exact about
these: they are recorded and checked.

Every value must come from the standard series above. Return the structured
result only.
"""


def render_contract(contract: AcceptanceContract) -> str:
    return json.dumps(
        [
            {
                "id": item.id,
                "kind": item.kind,
                "phase_id": item.phase_id,
                "function_id": item.function_id,
                "required": item.describe_band(),
                "rationale": item.rationale,
            }
            for item in contract.criteria
        ],
        indent=2,
    )


def build_direct_prompt(user_query: str, topology: dict[str, Any],
                        requirements: dict[str, Any],
                        contract: AcceptanceContract) -> str:
    return "\n\n".join([
        "PROBLEM:\n" + user_query,
        "VALIDATED TOPOLOGY:\n" + json.dumps(topology, indent=2),
        "MOTION PHASES AND LOADS:\n" + json.dumps(
            requirements.get("functions", []), indent=2),
        "ACCEPTANCE CRITERIA:\n" + render_contract(contract),
        CATALOG_TEXT,
        DESIGN_BASIS,
        INSTRUCTIONS,
    ])


SYSTEM_A0 = """\
You are a hydraulic design engineer sizing a verified circuit topology.

Work entirely from your own knowledge and the tables you are given. You have no
tools, no calculator and no verifier: no one will check your arithmetic before
the design is recorded, so check it yourself.
"""

SYSTEM_A05 = """\
You are a hydraulic design engineer sizing a verified circuit topology.

You have arithmetic tools available and should use them for every calculation.
There is no verifier and no repair round: whatever you return is the design, so
satisfy yourself that it meets every criterion before returning it.
"""

SYSTEM_A0V = """\
You are a hydraulic design engineer sizing a verified circuit topology.

Work entirely from your own knowledge and the tables you are given. You have no
tools and no calculator: every number you state, you worked out yourself.

You do, however, have a verifier. After you answer, an independent checker will
solve your design and tell you exactly which acceptance criteria it meets, which
it misses, and by how much. You will then get a limited number of chances to
revise. Use them: a design that misses on the first attempt and is corrected is
worth more than one that is defended.
"""

CANDIDATE_INSTRUCTIONS = """\
Return TWO OR THREE genuinely different sizings, not small variations on one.
Different in approach - which phase you sized the actuator against, what
pressure you designed to, how much headroom you carried. A deterministic score
will pick between them; you do not choose the winner.
"""


def build_candidate_prompt(user_query: str, topology: dict[str, Any],
                           requirements: dict[str, Any],
                           contract: AcceptanceContract) -> str:
    """A0-V's opening call: the A0 prompt, asking for a set instead of one design."""
    return build_direct_prompt(user_query, topology, requirements, contract) \
        + "\n\n" + CANDIDATE_INSTRUCTIONS


REPAIR_INSTRUCTIONS = """\
Revise the design so that every criterion is met.

Change only what the report gives you a reason to change. Sizes must still come
from the standard series, and the topology is still fixed. State your predicted
velocity, force and pressure for every phase again - they are recorded each
round, and how they move is itself part of the measurement.

Return one complete sizing, not a diff.
"""


def build_repair_prompt(base_prompt: str, previous: Any, feedback: str,
                        *, attempt: int, attempts_left: int) -> str:
    """Everything the arm needs to revise, restated from scratch.

    Deliberately stateless. Re-sending the whole problem each round costs tokens
    but makes every call reproducible from the record alone, and keeps the
    information the arm was given auditable rather than buried in a conversation
    the transcript does not fully capture.
    """
    return "\n\n".join([
        base_prompt,
        "=" * 68,
        f"YOUR PREVIOUS ATTEMPT (round {attempt}):\n"
        + json.dumps(previous.model_dump(mode="json"), indent=2),
        "=" * 68,
        "WHAT THE VERIFIER FOUND:\n" + feedback,
        "=" * 68,
        f"You have {attempts_left} revision(s) left.\n\n" + REPAIR_INSTRUCTIONS,
    ])
