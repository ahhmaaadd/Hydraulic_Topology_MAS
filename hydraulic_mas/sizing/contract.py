"""Compile the typed requirements into machine-checkable acceptance criteria.

Interval certification is only meaningful against a declared band, and the briefs
are full of "approximately 1.5 m/min" and "in no more than 5 seconds". Those two
phrases demand completely different checks - one is a two-sided band, the other a
one-sided bound - and guessing wrong makes every downstream certificate either
unfalsifiable or unfairly strict.

So tolerance resolution happens here, once, deterministically, from the qualifier
and raw text the extractor already preserved. Every resolution is recorded on the
criterion, and every certificate prints the band it was judged against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .quantities import Q, from_requirement


Relation = Literal["<=", ">=", "==", "within"]

# "approximately 4 s" is a target with slack; how much slack is a judgement the
# brief does not make, so it is stated here once and recorded as an assumption.
APPROXIMATE_TOLERANCE = 0.10

_APPROXIMATE = re.compile(r"\b(approx|approximately|about|around|circa|nominal|roughly|~)\b", re.I)
_UPPER_BOUND = re.compile(r"\b(no more than|not more than|at most|maximum|max|within|less than|under|not exceed)\b", re.I)
# "travel 250 mm in 5 seconds" budgets the time. Read as a *speed* that inverts
# to a floor: finishing early is not a failure. Applied only where a speed is
# being interpreted, never to the duration itself, or the bound flips twice.
_TIME_BUDGET = re.compile(r"\bin\s+\d+(?:\.\d+)?\s*(?:s|sec|secs|second|seconds)\b", re.I)
_LOWER_BOUND = re.compile(r"\b(at least|no less than|minimum|min|more than|greater than)\b", re.I)
_RANGE = re.compile(r"\bbetween\b|\bto\b.*\band\b|\brange\b", re.I)


_RANGE_NUMBERS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:to|and|-|\u2013)\s*(\d+(?:\.\d+)?)\s*([A-Za-z/]+)"
)


def _text_of(quantity: dict[str, Any] | None) -> str:
    if not isinstance(quantity, dict):
        return ""
    return " ".join(
        str(quantity.get(key) or "") for key in ("raw_text", "qualifier", "notes")
    )


def parse_range(quantity: dict[str, Any] | None) -> tuple[Q, Q] | None:
    """Read an adjustable band such as "between 0.5 and 1.0 m/min".

    The extractor leaves ``value`` null for these and keeps the span in the raw
    text, because a range is not a single number. It is still a checkable
    requirement: the speed control must be *adjustable across* the whole span.
    """
    if not isinstance(quantity, dict) or quantity.get("value") is not None:
        return None
    match = _RANGE_NUMBERS.search(str(quantity.get("raw_text") or ""))
    if not match:
        return None
    low, high, unit = match.group(1), match.group(2), match.group(3)
    try:
        first, second = Q.of(float(low), unit), Q.of(float(high), unit)
    except Exception:
        return None
    return (first, second) if first.value <= second.value else (second, first)


def resolve_tolerance(quantity: dict[str, Any] | None) -> tuple[Relation, float, str]:
    """Return ``(relation, relative_tolerance, rationale)`` for one stated value.

    The qualifier is consulted before the raw text. A retract stated as "350 mm in
    no more than 5 seconds" carries an upper bound *on the time*, but the derived
    speed it implies is a *lower* bound, and the extractor records exactly that in
    the qualifier ("minimum average speed to meet maximum time"). Reading the raw
    text first inverts the criterion and turns a floor into a ceiling.
    """
    qualifier = str((quantity or {}).get("qualifier") or "")
    if _LOWER_BOUND.search(qualifier):
        return ">=", 0.0, "qualified as a lower bound"
    if _UPPER_BOUND.search(qualifier):
        return "<=", 0.0, "qualified as an upper bound"
    text = _text_of(quantity)
    if _UPPER_BOUND.search(text):
        return "<=", 0.0, "stated as an upper bound"
    if _LOWER_BOUND.search(text):
        return ">=", 0.0, "stated as a lower bound"
    if _APPROXIMATE.search(text):
        return (
            "within",
            APPROXIMATE_TOLERANCE,
            f"stated as approximate; judged within +/-{APPROXIMATE_TOLERANCE:.0%}",
        )
    return "within", APPROXIMATE_TOLERANCE, (
        "stated as a target without an explicit tolerance; judged within "
        f"+/-{APPROXIMATE_TOLERANCE:.0%}"
    )


def speed_relation(phase: dict[str, Any]) -> Relation:
    """How a phase's speed should be read, including the time-budget rule.

    Shared so the planner and the contract cannot disagree. They did: the planner
    aimed a design off a bound the contract had already decided was a different
    kind of bound, and the resulting design sat one percent from a floor it was
    being judged against with a two percent spread.
    """
    speed = phase.get("speed")
    if isinstance((speed or {}).get("value"), (int, float)):
        relation, _, _ = resolve_tolerance(speed)
        if relation == "within" and _TIME_BUDGET.search(_text_of(speed)):
            return ">="
        return relation
    duration = phase.get("duration")
    if isinstance((duration or {}).get("value"), (int, float)):
        relation, _, _ = resolve_tolerance(duration)
        return {"<=": ">=", ">=": "<="}.get(relation, relation)
    return "within"


@dataclass
class Criterion:
    """One checkable acceptance statement."""

    id: str
    kind: str                 # velocity | force | duration | pressure_ceiling
    phase_id: str | None
    function_id: str | None
    relation: Relation
    target: Q
    unit: str                 # preferred unit for reporting
    rationale: str
    source: str = "explicit"
    target_hi: Q | None = None   # set only for adjustable-range criteria

    def bounds(self) -> tuple[float, float]:
        """Acceptable SI range implied by relation and tolerance."""
        if self.target_hi is not None:
            return self.target.value, self.target_hi.value
        value = self.target.value
        if self.relation == "<=":
            return float("-inf"), value
        if self.relation == ">=":
            return value, float("inf")
        if self.relation == "==":
            return value, value
        lo, hi = self.target.band()
        return lo, hi

    def describe_band(self) -> str:
        lo, hi = self.bounds()
        if self.target_hi is not None:
            factor = self.target.value / self.target.to(self.unit) if self.target.value else 1.0
            return f"adjustable {lo / factor:.4g} .. {hi / factor:.4g} {self.unit}"
        factor = self.target.value / self.target.to(self.unit) if self.target.value else 1.0
        if self.relation == "<=":
            return f"<= {hi / factor:.4g} {self.unit}"
        if self.relation == ">=":
            return f">= {lo / factor:.4g} {self.unit}"
        return f"{lo / factor:.4g} .. {hi / factor:.4g} {self.unit}"


@dataclass
class AcceptanceContract:
    problem_id: str
    criteria: list[Criterion] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def for_phase(self, phase_id: str) -> list[Criterion]:
        return [item for item in self.criteria if item.phase_id == phase_id]

    def of_kind(self, kind: str) -> list[Criterion]:
        return [item for item in self.criteria if item.kind == kind]


def load_tolerance(requirements: dict[str, Any]) -> float:
    """Fractional load variation the brief declares, if any.

    Canonical here rather than in the planner. It is consumed in two places -
    the envelope the design is judged over, and the force target a phase must
    develop - and when those two disagreed the force check silently ignored the
    variation entirely.
    """
    text = " ".join(
        str(criterion.get("description") or "")
        for criterion in requirements.get("acceptance_criteria", []) or []
    ).casefold()
    if "15 percent" in text or "15%" in text or "plus or minus 15" in text:
        return 0.15
    return 0.0


def compile_contract(requirements: dict[str, Any], problem_id: str = "") -> AcceptanceContract:
    contract = AcceptanceContract(problem_id=problem_id)
    load_variation = load_tolerance(requirements)
    seen_assumptions: set[str] = set()

    def note(text: str) -> None:
        if text not in seen_assumptions:
            seen_assumptions.add(text)
            contract.assumptions.append(text)

    for function in requirements.get("functions", []):
        function_id = str(function.get("id"))
        for phase in function.get("motion_phases") or []:
            phase_id = str(phase.get("id"))

            speed = from_requirement(phase.get("speed"))
            distance = from_requirement(phase.get("distance"))
            duration = from_requirement(phase.get("duration"))

            span = parse_range(phase.get("speed"))
            if span is not None:
                contract.criteria.append(
                    Criterion(
                        id=f"{phase_id}__velocity_range",
                        kind="velocity_range",
                        phase_id=phase_id,
                        function_id=function_id,
                        relation="within",
                        target=span[0],
                        target_hi=span[1],
                        unit="m/min",
                        rationale="the speed control must be adjustable across the whole stated span",
                    )
                )
                note(f"{phase_id}: speed stated as an adjustable span; both endpoints must be reachable.")

            # Velocity: prefer the stated speed; otherwise derive from distance/time.
            derived = False
            if speed is None and distance is not None and duration is not None and duration.value > 0:
                speed = distance / duration
                derived = True
            if speed is not None and speed.value > 0:
                if derived:
                    relation, tolerance, rationale = resolve_tolerance(phase.get("duration"))
                    # A bounded duration inverts when read as a speed: finishing
                    # in "no more than 5 s" is a speed *floor*, not a ceiling.
                    relation = {"<=": ">=", ">=": "<="}.get(relation, relation)
                    rationale = f"derived from distance/duration (bound inverted); {rationale}"
                else:
                    relation, tolerance, rationale = resolve_tolerance(phase.get("speed"))
                    if speed_relation(phase) == ">=" and relation == "within":
                        relation, tolerance = ">=", 0.0
                        rationale = (
                            "the distance is budgeted a time, so the speed is a floor; "
                            "completing early satisfies the requirement"
                        )
                contract.criteria.append(
                    Criterion(
                        id=f"{phase_id}__velocity",
                        kind="velocity",
                        phase_id=phase_id,
                        function_id=function_id,
                        relation=relation,
                        target=speed.with_tolerance(tolerance),
                        unit="m/min",
                        rationale=rationale,
                    )
                )
                if tolerance:
                    note(f"{phase_id}: velocity {rationale}.")

            # Force: the phase must be able to develop the stated resistance -
            # and where the brief says that resistance varies, the *top of the
            # stated band* is the load the actuator has to cover. Judging
            # against the nominal instead was worth a fourfold overstatement of
            # margin on P7-01: 1.20x reported where the honest figure is 1.04x.
            force = from_requirement(phase.get("force"))
            if force is not None and force.value > 0:
                worst = Q(force.value * (1.0 + load_variation), force.dimension)
                rationale = "the phase must develop at least the stated load"
                if load_variation > 0:
                    rationale = (
                        f"the phase must develop the stated load at the top of the "
                        f"+/-{load_variation:.0%} variation the brief declares"
                    )
                    note(
                        f"{phase_id}: load stated as varying +/-{load_variation:.0%}; the force "
                        f"criterion is judged against {worst.to('kn'):.4g} kn, not the nominal "
                        f"{force.to('kn'):.4g} kn."
                    )
                contract.criteria.append(
                    Criterion(
                        id=f"{phase_id}__force",
                        kind="force",
                        phase_id=phase_id,
                        function_id=function_id,
                        relation=">=",
                        target=worst,
                        unit="kn",
                        rationale=rationale,
                    )
                )

            # Duration is only a separate requirement when the brief *bounds the
            # time*. Where a speed is stated independently, the duration merely
            # restates it, and emitting both turns one requirement into two that
            # can contradict each other.
            duration_relation, _, _ = resolve_tolerance(phase.get("duration"))
            speed_stated_independently = (
                isinstance((phase.get("speed") or {}).get("value"), (int, float)) and not derived
            )
            duration_is_bound = (
                not speed_stated_independently
                and (duration_relation in {"<=", ">="} or derived)
            )
            if (
                duration is not None and duration.value > 0
                and distance is not None and duration_is_bound
            ):
                relation, tolerance, rationale = resolve_tolerance(phase.get("duration"))
                contract.criteria.append(
                    Criterion(
                        id=f"{phase_id}__duration",
                        kind="duration",
                        phase_id=phase_id,
                        function_id=function_id,
                        relation=relation,
                        target=duration.with_tolerance(tolerance),
                        unit="s",
                        rationale=rationale,
                    )
                )

        # Per-function branch pressure ceiling.
        ceiling = from_requirement(function.get("max_working_pressure"))
        if ceiling is not None and ceiling.value > 0:
            contract.criteria.append(
                Criterion(
                    id=f"{function_id}__pressure_ceiling",
                    kind="pressure_ceiling",
                    phase_id=None,
                    function_id=function_id,
                    relation="<=",
                    target=ceiling,
                    unit="bar",
                    rationale="branch working-pressure ceiling stated for this function",
                )
            )

    system_ceiling = from_requirement(
        (requirements.get("global_constraints") or {}).get("max_system_pressure")
    )
    if system_ceiling is not None and system_ceiling.value > 0:
        contract.criteria.append(
            Criterion(
                id="system__pressure_ceiling",
                kind="pressure_ceiling",
                phase_id=None,
                function_id=None,
                relation="<=",
                target=system_ceiling,
                unit="bar",
                rationale="global maximum system pressure",
            )
        )
    return contract
