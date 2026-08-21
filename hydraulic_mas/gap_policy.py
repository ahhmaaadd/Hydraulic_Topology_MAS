"""Deterministic distinction between catalog absence and evidence uncertainty."""

from __future__ import annotations

import re
from typing import Any

from .catalog import CATALOG


_EVIDENCE_WORDS = {
    "citation",
    "cited",
    "evidence",
    "research",
    "source",
    "manual",
    "schematic",
    "reference",
    "documentation",
}


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _catalog_class_aliases() -> list[set[str]]:
    aliases: list[set[str]] = []
    for key, entry in CATALOG.items():
        aliases.append(_tokens(key))
        aliases.append(_tokens(entry.get("type")))
        aliases.append(_tokens(entry.get("name")))
        for capability in entry.get("capabilities", []):
            aliases.append(_tokens(capability))
    return [value for value in aliases if value]


_CLASS_ALIASES = _catalog_class_aliases()


def classify_catalog_gap(gap: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(is_blocking_catalog_absence, explanation)``.

    A model cannot turn missing documentation or an already available generic
    class into a blocking catalog gap.  A genuinely absent topology class still
    blocks regardless of the model's ``blocking`` flag.
    """
    if gap.get("scope", "topology") != "topology":
        return False, "Sizing gaps are outside topology validation."

    reason_tokens = _tokens(gap.get("reason"))
    class_tokens = _tokens(gap.get("needed_component_class"))
    if reason_tokens.intersection(_EVIDENCE_WORDS):
        return False, "This is an evidence/research gap, not a missing catalog class."
    if class_tokens and any(class_tokens <= alias or alias <= class_tokens for alias in _CLASS_ALIASES):
        return False, "The requested generic class/capability already exists in the runtime catalog."
    return True, "The required topology-changing class is not represented by the runtime catalog."


def blocking_catalog_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [gap for gap in gaps if classify_catalog_gap(gap)[0]]


def classify_evidence_gap(gap: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(is_blocking_evidence_gap, explanation)``.

    An ``EvidenceGap`` records missing corroboration for a pattern the runtime
    catalog can already express.  Before v0.4.0 the model's own ``blocking``
    flag was trusted verbatim, so a designer that had been told "the class
    exists, this is an evidence gap rather than a catalog gap" could re-emit the
    same complaint as a *blocking* evidence gap.  That produced an error with
    ``scope="research"``, one targeted search that could not possibly find a
    schematic for the exact machine, and then a stalled repair loop.

    Blocking is therefore now a deterministic decision:

    * a gap whose reason is about missing documentation, citations, schematics
      or sources is never blocking - supported sub-patterns may be composed;
    * a gap whose capability maps onto an available generic class is never
      blocking, because the topology can be built and proven by the directed
      phase graphs regardless of corroboration; and
    * only a capability that is both uncorroborated *and* unrepresented in the
      catalog can block, and that case is already reported as a catalog gap.
    """
    reason_tokens = _tokens(gap.get("reason"))
    capability_tokens = _tokens(gap.get("capability"))
    if reason_tokens.intersection(_EVIDENCE_WORDS):
        return (
            False,
            "Missing corroboration is not blocking; compose the supported sub-patterns "
            "and let the directed phase graphs prove the behavior.",
        )
    if capability_tokens and any(
        capability_tokens & alias for alias in _CLASS_ALIASES if len(alias & capability_tokens) >= 2
    ):
        return (
            False,
            "The capability maps onto an available generic class, so the pattern can be built "
            "and validated without further search.",
        )
    return True, "The capability is neither corroborated nor expressible with an available class."



def interfunction_sequence_steps(requirements: dict[str, Any]) -> list[dict[str, Any]]:
    """Sequence steps that genuinely interlock *two different* functions.

    A pressure-triggered step is not automatically a sequence-valve requirement.
    Two very different things arrive wearing the same clothes:

    * **Between functions.** "Only after the clamp has developed force may the
      drill begin." One actuator's pressure must gate another actuator's supply.
      That needs a sequence valve.
    * **Within one function.** "The speed change must result from the change in
      load." One actuator changes speed because the load pressure rose. Nothing
      gates anything: a plain non-compensated throttle passes less flow as its
      pressure drop collapses, and that *is* the mechanism.

    Before v0.4.4 both produced ``pressure_sequence_required``, which made the
    P7-04 slide unsatisfiable. The validator demanded a sequence valve because a
    pressure-triggered step existed; the minimality rules and the design reviewer
    then rejected that same valve as unauthorized complexity. The repair loop
    spent all four rounds moving a component it was simultaneously required to
    have and forbidden to have.

    A step is inter-function only when the functions it activates differ from the
    functions its predecessor phase activates. Everything else is a within-actuator
    transition and must not imply a valve.
    """
    sequence = (requirements.get("operational_logic") or {}).get("sequence") or []
    function_by_phase: dict[str, set[str]] = {}
    for function in requirements.get("functions", []):
        for phase in function.get("motion_phases") or []:
            if phase.get("id"):
                function_by_phase.setdefault(str(phase["id"]), set()).add(str(function.get("id")))

    steps_by_phase = {str(step.get("phase_id")): step for step in sequence if step.get("phase_id")}
    interfunction: list[dict[str, Any]] = []
    for step in sequence:
        own = set(str(value) for value in step.get("function_ids", []) if value)
        own |= function_by_phase.get(str(step.get("phase_id")), set())
        predecessor_id = step.get("predecessor_phase_id")
        if not predecessor_id:
            # No predecessor to compare against; treat a declared multi-function
            # step as inter-function and a single-function step as internal.
            if len(own) > 1:
                interfunction.append(step)
            continue
        predecessor = steps_by_phase.get(str(predecessor_id)) or {}
        previous = set(str(value) for value in predecessor.get("function_ids", []) if value)
        previous |= function_by_phase.get(str(predecessor_id), set())
        if own and previous and own != previous:
            interfunction.append(step)
        elif len(own) > 1:
            interfunction.append(step)
    return interfunction


def requires_pressure_sequence_valve(requirements: dict[str, Any]) -> bool:
    """True only when a pressure trigger crosses a function boundary."""
    return any(
        step.get("trigger") == "pressure" for step in interfunction_sequence_steps(requirements)
    )
