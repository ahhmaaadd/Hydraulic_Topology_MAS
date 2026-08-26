"""E2: inject known defects into certified designs and see what the verifier says.

E1 - rediscovering the defects a human audit found in the published reference
solutions - is the stronger evidence of the two, because those defects were
identified before this verifier existed and so cannot have been chosen to suit
it. Its weakness is that there are only seven of them and they are all of the
kinds a person notices.

E2 is the complement. Defects are injected mechanically, in known quantities, at
known magnitudes, into designs that certify cleanly. That gives a detection rate
as a function of severity rather than a single count, and - because the same
sweep includes *null* injections that change nothing - a false-positive rate to
put beside it. A detector with no false-positive rate quoted is not characterised.

The defect classes are the ones this stage can actually be wrong about, taken
from the audit rather than invented: an undersized actuator, a relief below the
working pressure, a prime mover sized on hydraulic instead of shaft power, a
speed control set to the wrong flow, a reduced branch set above its ceiling.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from ..sizing.catalog_sizing import BORE_SERIES, MOTOR_SERIES
from ..sizing.certify import certify
from ..sizing.contract import compile_contract
from ..sizing.intervals import standard_envelope
from ..sizing.planner import _load_tolerance, size_problem


# A defect is a function from (sizing, throttles) to (sizing, throttles), plus
# what it is called and how hard it was pushed.
Mutation = Callable[[dict, dict], tuple[dict, dict] | None]


@dataclass
class Defect:
    name: str
    severity: str            # "null" | "subtle" | "gross"
    apply: Mutation
    expected_detectable: bool = True


@dataclass
class DefectOutcome:
    problem_id: str
    defect: str
    severity: str
    expected_detectable: bool
    baseline_verdict: str
    verdict: str
    detected: bool
    codes: list[str] = field(default_factory=list)
    refuted_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id, "defect": self.defect, "severity": self.severity,
            "expected_detectable": self.expected_detectable,
            "baseline_verdict": self.baseline_verdict, "verdict": self.verdict,
            "detected": self.detected, "codes": self.codes,
            "refuted_criteria": self.refuted_criteria,
        }


def _step_bores(steps: int) -> Mutation:
    def mutate(sizing: dict, throttles: dict):
        changed = copy.deepcopy(sizing)
        moved = False
        for record in changed.values():
            if not isinstance(record, dict) or "bore_mm" not in record:
                continue
            try:
                index = BORE_SERIES.index(record["bore_mm"])
            except ValueError:
                continue
            new_index = index + steps
            if not 0 <= new_index < len(BORE_SERIES):
                continue
            from ..sizing.quantities import annulus_area, area_of_bore

            bore = BORE_SERIES[new_index]
            if record["rod_mm"] >= bore:
                continue
            record["bore_mm"] = bore
            record["cap_area_m2"] = area_of_bore(bore).value
            record["annulus_area_m2"] = annulus_area(bore, record["rod_mm"]).value
            moved = True
        return (changed, dict(throttles)) if moved else None
    return mutate


def _scale_relief(factor: float) -> Mutation:
    def mutate(sizing: dict, throttles: dict):
        changed = copy.deepcopy(sizing)
        supply = changed.get("__supply__")
        if not supply or not supply.get("relief_pa"):
            return None
        supply["relief_pa"] = supply["relief_pa"] * factor
        return changed, dict(throttles)
    return mutate


def _drop_motor(steps: int) -> Mutation:
    def mutate(sizing: dict, throttles: dict):
        changed = copy.deepcopy(sizing)
        supply = changed.get("__supply__")
        if not supply or not supply.get("motor_kw"):
            return None
        try:
            index = MOTOR_SERIES.index(supply["motor_kw"])
        except ValueError:
            return None
        if index - steps < 0:
            return None
        supply["motor_kw"] = MOTOR_SERIES[index - steps]
        return changed, dict(throttles)
    return mutate


def _scale_throttles(factor: float) -> Mutation:
    def mutate(sizing: dict, throttles: dict):
        if not throttles:
            return None
        changed = copy.deepcopy(sizing)
        for record in changed.values():
            if isinstance(record, dict) and record.get("set_flow_m3s"):
                record["set_flow_m3s"] *= factor
        return changed, {key: value * factor for key, value in throttles.items()}
    return mutate


def _raise_branch_settings(factor: float) -> Mutation:
    def mutate(sizing: dict, throttles: dict):
        changed = copy.deepcopy(sizing)
        touched = False
        for record in changed.values():
            if isinstance(record, dict) and record.get("setting_pa"):
                record["setting_pa"] *= factor
                touched = True
        return (changed, dict(throttles)) if touched else None
    return mutate


def _identity(sizing: dict, throttles: dict):
    return copy.deepcopy(sizing), dict(throttles)


def _relabel(sizing: dict, throttles: dict):
    """Change nothing physical. A verifier that fires here is fabricating."""
    changed = copy.deepcopy(sizing)
    for record in changed.values():
        if isinstance(record, dict):
            record.setdefault("note", "cosmetic")
    return changed, dict(throttles)


DEFECTS: tuple[Defect, ...] = (
    Defect("none", "null", _identity, expected_detectable=False),
    Defect("cosmetic_annotation", "null", _relabel, expected_detectable=False),
    Defect("actuator_one_size_down", "subtle", _step_bores(-1)),
    Defect("actuator_two_sizes_down", "gross", _step_bores(-2)),
    Defect("relief_5pct_low", "subtle", _scale_relief(0.95)),
    Defect("relief_25pct_low", "gross", _scale_relief(0.75)),
    Defect("prime_mover_one_frame_down", "subtle", _drop_motor(1)),
    Defect("prime_mover_three_frames_down", "gross", _drop_motor(3)),
    Defect("speed_control_10pct_shut", "subtle", _scale_throttles(0.90)),
    Defect("speed_control_half_shut", "gross", _scale_throttles(0.50)),
    Defect("branch_settings_30pct_high", "gross", _raise_branch_settings(1.30)),
)


def run_seeded_defects(problems: dict[str, dict[str, Any]],
                       problem_ids: list[str] | None = None,
                       defects: tuple[Defect, ...] = DEFECTS) -> list[DefectOutcome]:
    outcomes: list[DefectOutcome] = []
    for problem_id in (problem_ids or sorted(problems)):
        entry = problems[problem_id]
        topology, requirements = entry["topology"], entry["requirements"]
        contract = compile_contract(requirements, problem_id)
        tolerance = _load_tolerance(requirements)
        envelope = standard_envelope(tolerance)
        planned = size_problem(problem_id, topology, requirements)
        baseline = planned.certificate.verdict
        # Only a design that certifies cleanly is a fair thing to damage: if the
        # baseline is already UNDECIDED there is nothing for a detection to be
        # distinguished from.
        if baseline != "PROVED":
            continue
        for defect in defects:
            mutated = defect.apply(planned.sizing, planned.throttle_k)
            if mutated is None:
                continue
            sizing, throttles = mutated
            certificate = certify(problem_id, topology, requirements, contract,
                                  sizing, throttles, envelope)
            refuted = [item.criterion_id for item in certificate.certificates
                       if item.verdict == "REFUTED"]
            outcomes.append(DefectOutcome(
                problem_id=problem_id,
                defect=defect.name,
                severity=defect.severity,
                expected_detectable=defect.expected_detectable,
                baseline_verdict=baseline,
                verdict=certificate.verdict,
                detected=certificate.verdict != "PROVED",
                codes=sorted({item.code for item in certificate.findings}),
                refuted_criteria=refuted,
            ))
    return outcomes


def graded_defects(fractions: tuple[float, ...] = (0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20)
                   ) -> tuple[Defect, ...]:
    """The same defect at rising magnitude, to find where detection actually turns on.

    A benchmark whose every injection is caught tells you the injections were
    coarse, not that the detector is perfect - and 100 percent across the board
    is the first thing a referee will disbelieve. What characterises a detector is
    the *curve*: the magnitude at which it starts to fire, and how that compares
    with the width of the operating envelope it judges over. A defect smaller
    than the envelope spread should not be detectable, and it would be a bad sign
    if it were, because that would mean the verdict was responding to something
    other than physics.
    """
    graded: list[Defect] = []
    for fraction in fractions:
        graded.append(Defect(
            f"relief_low_{fraction:.1%}", f"graded_{fraction:.4f}",
            _scale_relief(1.0 - fraction)))
        graded.append(Defect(
            f"speed_control_shut_{fraction:.1%}", f"graded_{fraction:.4f}",
            _scale_throttles(1.0 - fraction)))
    return tuple(graded)


def detection_curve(outcomes: list[DefectOutcome]) -> dict[str, list[dict[str, Any]]]:
    """Detection rate against defect magnitude, per defect family."""
    families: dict[str, dict[float, list[bool]]] = {}
    for outcome in outcomes:
        if not outcome.severity.startswith("graded_"):
            continue
        magnitude = float(outcome.severity.split("_", 1)[1])
        family = outcome.defect.rsplit("_", 1)[0]
        families.setdefault(family, {}).setdefault(magnitude, []).append(outcome.detected)
    return {
        family: [
            {
                "magnitude": magnitude,
                "n": len(flags),
                "detection_rate": sum(flags) / len(flags),
            }
            for magnitude, flags in sorted(points.items())
        ]
        for family, points in sorted(families.items())
    }


def detection_table(outcomes: list[DefectOutcome]) -> dict[str, Any]:
    """Detection rate by severity, and the false-positive rate beside it."""
    by_severity: dict[str, list[DefectOutcome]] = {}
    for outcome in outcomes:
        by_severity.setdefault(outcome.severity, []).append(outcome)

    table: dict[str, Any] = {}
    for severity, rows in sorted(by_severity.items()):
        detected = sum(1 for row in rows if row.detected)
        if severity == "null":
            table[severity] = {
                "injections": len(rows),
                "false_positives": detected,
                "false_positive_rate": detected / len(rows) if rows else 0.0,
            }
        else:
            table[severity] = {
                "injections": len(rows),
                "detected": detected,
                "detection_rate": detected / len(rows) if rows else 0.0,
                "missed": sorted({row.defect for row in rows if not row.detected}),
            }
    by_defect = {}
    for outcome in outcomes:
        record = by_defect.setdefault(outcome.defect, {"n": 0, "detected": 0})
        record["n"] += 1
        record["detected"] += 1 if outcome.detected else 0
    table["by_defect"] = {
        name: {**record, "rate": record["detected"] / record["n"]}
        for name, record in sorted(by_defect.items())
    }
    return table
