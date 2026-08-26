"""What a sizing certificate does *not* speak to.

A verdict of PROVED is only as strong as the set of statements it ranges over,
and the compiler does not turn every stated acceptance criterion into a checkable
one. Roughly two in five are dropped: some because they were settled at the
topology stage, some because they fix a dimension rather than assert an outcome,
some because they restate a fact another criterion already carries, and some
because quasi-static analysis genuinely cannot see them.

Those are four very different reasons and only the last is a limitation of the
method. Leaving them all unmentioned lets "6 of 7 PROVED" read as "six designs
fully verified", which is not what it means. So every stated criterion is
accounted for here, with the reason recorded per item, and the count is generated
rather than asserted.
"""

from __future__ import annotations

import re
from typing import Any

from .contract import AcceptanceContract


# Reason codes. Only NOT_MODELLED is a gap in the verifier; the rest are honest
# statements about what a sizing certificate is for.
CERTIFIED = "certified"
TOPOLOGY_STAGE = "settled_at_topology_stage"
GEOMETRIC = "geometric_choice_not_an_outcome"
RESTATED = "restated_by_another_criterion"
OUT_OF_SCOPE = "outside_quasi_static_scope"
NOT_MODELLED = "not_modelled"
NON_QUANTITATIVE = "non_quantitative"

REASON_TEXT = {
    CERTIFIED: "checked by a criterion in this certificate",
    TOPOLOGY_STAGE: "a structural claim already proved by the topology validator",
    GEOMETRIC: "fixes a dimension the design chooses; nothing to solve for",
    RESTATED: "the same physical fact another criterion already carries",
    OUT_OF_SCOPE: "requires transient or leakage behaviour a quasi-static model cannot see",
    NOT_MODELLED: "quantitative and in scope, but no check exists for it yet",
    NON_QUANTITATIVE: "states no numeric target to check against",
}

_SPEED = re.compile(r"\bspeed|velocity|feed rate\b", re.I)
_FORCE = re.compile(r"\bforce|load|thrust\b", re.I)
_PRESSURE = re.compile(r"\bpressure\b", re.I)
_DURATION = re.compile(r"\bduration|time\b", re.I)
_DISTANCE = re.compile(r"\bdistance|travel|stroke\b", re.I)
_STRUCTURAL = re.compile(
    r"\bactuator_count|synchroni|drive point|valve|interlock|sequence|position\b", re.I)
_DYNAMIC = re.compile(
    r"\bdrift|stop time|settling|transition stop|variation tolerated|response\b", re.I)


# Words that name the *quantity* rather than the phase. Stripping them from a
# free-text metric leaves the phase it belongs to, so "approach duration" and
# "approach average speed" collapse to one claim about the approach - which is
# what they are - while "advance force" and "return force" stay two claims.
_QUANTITY_WORDS = re.compile(
    r"\b(distance|travel|stroke|speed|velocity|force|load|thrust|pressure|duration"
    r"|time|average|resistance|capability|capacity|required|for|system|total|the)\b",
    re.I)


def _phase_hint(tail: str) -> str:
    return " ".join(_QUANTITY_WORDS.sub(" ", tail).split()).casefold()


def _kinds_for(text: str) -> set[str]:
    """Which certificate kinds could possibly answer this statement."""
    kinds: set[str] = set()
    if _SPEED.search(text):
        kinds |= {"velocity", "velocity_range"}
    if _FORCE.search(text):
        kinds.add("force")
    if _PRESSURE.search(text):
        kinds.add("pressure_ceiling")
    if _DURATION.search(text):
        # A time budget compiles to a speed floor, so either kind answers it.
        kinds |= {"duration", "velocity"}
    return kinds


def _classify(statement: dict[str, Any], contract: AcceptanceContract,
              checked_kinds_by_function: dict[str | None, set[str]],
              seen: set[tuple]) -> tuple[str, str]:
    metric = str(statement.get("metric") or "")
    head = metric.split(".")[0] if "." in metric else ""
    tail = ".".join(metric.split(".")[1:]) or metric
    description = str(statement.get("description") or "")
    text = f"{tail} {description}"
    function_id = statement.get("related_function_id")
    target = statement.get("target") or {}

    if not isinstance(target.get("value"), (int, float)):
        return NON_QUANTITATIVE, tail
    if _STRUCTURAL.search(text):
        return TOPOLOGY_STAGE, tail
    if _DYNAMIC.search(text):
        return OUT_OF_SCOPE, tail

    kinds = _kinds_for(text)
    if not kinds and _DISTANCE.search(text):
        return GEOMETRIC, tail
    if not kinds:
        return NOT_MODELLED, tail

    # Pressure ceilings are global or per-function and are compiled once.
    available = checked_kinds_by_function.get(function_id, set())
    if "pressure_ceiling" in kinds:
        available = available | checked_kinds_by_function.get(None, set())

    matched = kinds & available
    if not matched:
        return NOT_MODELLED, tail
    # Keyed on the phase the metric names, not just the function: "advance force"
    # and "return force" belong to one function but are two different claims, and
    # collapsing them understates coverage badly.
    key = (head or _phase_hint(tail) or function_id, tuple(sorted(matched)))
    if key in seen:
        # Two statements answered by the same check: a duration and the average
        # speed derived from it, for instance. The first is certified; the second
        # is that same fact said again, and counting it twice would inflate both
        # the coverage and the criterion count.
        return RESTATED, tail
    seen.add(key)
    return CERTIFIED, tail


def account_for_criteria(requirements: dict[str, Any],
                         contract: AcceptanceContract) -> list[dict[str, Any]]:
    """One record per *stated* acceptance criterion, with why it is or isn't checked.

    Returns only the ones that produced no check; the certified ones are already
    visible as certificates. The counts still add up because every statement is
    classified exactly once.
    """
    by_function: dict[str | None, set[str]] = {}
    for criterion in contract.criteria:
        by_function.setdefault(criterion.function_id, set()).add(criterion.kind)

    seen: set[tuple] = set()
    uncovered: list[dict[str, Any]] = []
    certified = 0
    for statement in requirements.get("acceptance_criteria", []) or []:
        reason, tail = _classify(statement, contract, by_function, seen)
        if reason == CERTIFIED:
            certified += 1
            continue
        uncovered.append({
            "criterion_id": statement.get("id"),
            "metric": tail,
            "description": str(statement.get("description") or "")[:160],
            "reason": reason,
            "explanation": REASON_TEXT[reason],
        })
    # Recorded on the list itself so the certificate can report a true partition
    # of what was asked, rather than adding two numbers that count different things.
    return _Accounted(uncovered, certified)


class _Accounted(list):
    """The uncovered records, carrying how many statements *were* answered."""

    def __init__(self, items: list[dict[str, Any]], certified: int) -> None:
        super().__init__(items)
        self.certified = certified


def coverage_summary(uncovered: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in uncovered:
        summary[item["reason"]] = summary.get(item["reason"], 0) + 1
    return summary
