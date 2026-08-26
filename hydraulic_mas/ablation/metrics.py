"""How wrong an arm was, rather than merely whether it passed.

"The model without tools proved fewer criteria" is a weak claim: it invites the
reply that the prompt, the schema or the scoring was unfair. The stronger claim
is arithmetic, and it is available for free, because every direct arm is asked
what its own design will do before anything checks it.

So for each phase there are three numbers: what the requirement asked, what the
model predicted its design would achieve, and what the design actually achieves
under the verifier. Two errors follow.

* **Prediction error** - predicted against actual. This is the model's arithmetic
  alone, independent of whether the design was any good. A model that says a
  100 mm bore at 60 bar gives 60 kN is wrong by a fixed amount whatever the
  requirement was.
* **Requirement error** - actual against required. This is design quality: how
  far the delivered design lands from what was asked.

An arm can score badly on the second while being fine on the first, and the two
say different things. Reporting only the pass rate collapses them.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


def _actual_from_certificate(certificate: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Mid-point of each criterion's enclosure, keyed by (phase, kind)."""
    actual: dict[tuple[str, str], float] = {}
    for item in certificate.get("criteria", []) or []:
        phase_id = item.get("phase_id")
        achieved = str(item.get("achieved") or "")
        if not phase_id or "[" not in achieved:
            continue
        try:
            body = achieved[achieved.index("[") + 1:achieved.index("]")]
            low, high = (float(part) for part in body.split(","))
        except (ValueError, IndexError):
            continue
        middle = 0.5 * (low + high)
        # A degenerate enclosure can still reach here from an older record.
        # Averaging a NaN silently poisons every summary it touches.
        if not math.isfinite(middle):
            continue
        actual[(str(phase_id), str(item.get("kind")))] = middle
    return actual


def _required_from_certificate(certificate: dict[str, Any]) -> dict[tuple[str, str], float]:
    required: dict[tuple[str, str], float] = {}
    for item in certificate.get("criteria", []) or []:
        phase_id = item.get("phase_id")
        text = str(item.get("required") or "")
        if not phase_id:
            continue
        numbers = [
            float(token) for token in text.replace("..", " ").split()
            if _is_number(token)
        ]
        if numbers:
            required[(str(phase_id), str(item.get("kind")))] = sum(numbers) / len(numbers)
    return required


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


_PREDICTION_FIELDS = {
    "velocity": "predicted_velocity_m_min",
    "force": "predicted_force_kn",
}


def prediction_errors(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Signed relative error of the arm's own predictions, per phase and quantity.

    Only defined for the direct arms: A1 and A2 never state a prediction, because
    they never do the arithmetic themselves. That asymmetry is not a gap in the
    measurement - it is the thing being measured.
    """
    proposal = result.get("proposal") or {}
    predictions = proposal.get("predictions") or []
    if not predictions:
        return []
    actual = _actual_from_certificate(result.get("certificate") or {})
    rows: list[dict[str, Any]] = []
    for entry in predictions:
        phase_id = str(entry.get("phase_id") or "")
        for kind, field_name in _PREDICTION_FIELDS.items():
            claimed = entry.get(field_name)
            if not isinstance(claimed, (int, float)):
                continue
            truth = actual.get((phase_id, kind))
            if truth is None or abs(truth) < 1e-12:
                continue
            rows.append({
                "arm": result.get("arm"),
                "problem_id": result.get("problem_id"),
                "seed": result.get("seed"),
                "phase_id": phase_id,
                "quantity": kind,
                "predicted": float(claimed),
                "actual": truth,
                "relative_error": (float(claimed) - truth) / abs(truth),
            })
    return rows


def requirement_errors(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Signed relative shortfall of the delivered design against what was asked.

    Negative means the design falls short. Defined for every arm, so this is the
    column the four conditions are actually compared in.
    """
    certificate = result.get("certificate") or {}
    actual = _actual_from_certificate(certificate)
    required = _required_from_certificate(certificate)
    rows: list[dict[str, Any]] = []
    for key, target in required.items():
        truth = actual.get(key)
        if truth is None or abs(target) < 1e-12:
            continue
        rows.append({
            "arm": result.get("arm"),
            "problem_id": result.get("problem_id"),
            "seed": result.get("seed"),
            "phase_id": key[0],
            "quantity": key[1],
            "required": target,
            "actual": truth,
            "relative_error": (truth - target) / abs(target),
        })
    return rows


def summarise(values: list[float]) -> dict[str, float | int]:
    """Median, quartiles and tail. Median rather than mean on purpose.

    A single wildly wrong answer - a factor of ten from a unit slip - would
    dominate a mean and make the summary a statement about one outlier.
    """
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "p25": ordered[max(0, int(0.25 * (len(ordered) - 1)))],
        "p75": ordered[min(len(ordered) - 1, int(0.75 * (len(ordered) - 1)))],
        "worst": max(ordered, key=abs),
        "mean_abs": statistics.fmean(abs(value) for value in ordered),
        "within_5pct": sum(1 for value in ordered if abs(value) <= 0.05) / len(ordered),
        "within_1pct": sum(1 for value in ordered if abs(value) <= 0.01) / len(ordered),
    }


def verdict_rates(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass rates per arm, with the repeatability spread across seeds."""
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_arm.setdefault(str(result.get("arm")), []).append(result)

    table: dict[str, Any] = {}
    for arm, rows in sorted(by_arm.items()):
        counts = {"PROVED": 0, "UNDECIDED": 0, "REFUTED": 0, "ERROR": 0}
        for row in rows:
            counts[str(row.get("verdict", "ERROR"))] = counts.get(
                str(row.get("verdict", "ERROR")), 0) + 1
        by_problem: dict[str, list[int]] = {}
        for row in rows:
            by_problem.setdefault(str(row.get("problem_id")), []).append(
                1 if row.get("verdict") == "PROVED" else 0)
        # Variation *within* a problem across seeds is the reliability question;
        # variation between problems is just problem difficulty and would inflate
        # any spread computed over the pooled runs.
        spreads = [
            statistics.pstdev(flags) for flags in by_problem.values() if len(flags) > 1
        ]
        indices = [row["oversizing_index"] for row in rows
                   if isinstance(row.get("oversizing_index"), (int, float))]
        table[arm] = {
            "runs": len(rows),
            "counts": counts,
            "proved_rate": counts["PROVED"] / len(rows) if rows else 0.0,
            "per_problem_proved": {
                problem: sum(flags) / len(flags) for problem, flags in sorted(by_problem.items())
            },
            "mean_within_problem_sd": statistics.fmean(spreads) if spreads else 0.0,
            "median_oversizing_index": statistics.median(indices) if indices else None,
        }
    return table


def error_table(results: list[dict[str, Any]]) -> dict[str, Any]:
    prediction: dict[str, list[float]] = {}
    requirement: dict[str, list[float]] = {}
    for result in results:
        arm = str(result.get("arm"))
        for row in prediction_errors(result):
            prediction.setdefault(arm, []).append(row["relative_error"])
        for row in requirement_errors(result):
            requirement.setdefault(arm, []).append(row["relative_error"])
    return {
        "prediction_error": {arm: summarise(values) for arm, values in sorted(prediction.items())},
        "requirement_error": {arm: summarise(values) for arm, values in sorted(requirement.items())},
    }


def unreachable_rate(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """How often an arm produced a design with no solvable operating point.

    This must be reported alongside the error distributions, because those
    distributions are survivorship-biased without it. A design so far out that no
    phase moves contributes *no* rows to the error table - it has no achieved
    value to compare - so an arm that fails catastrophically can look, in the
    error columns alone, better than one that fails by ten percent. The count of
    what never reached the table is the correction.
    """
    by_arm: dict[str, list[float]] = {}
    for result in results:
        criteria = (result.get("certificate") or {}).get("criteria") or []
        if not criteria:
            by_arm.setdefault(str(result.get("arm")), []).append(1.0)
            continue
        unreachable = sum(1 for item in criteria if "[" not in str(item.get("achieved") or ""))
        by_arm.setdefault(str(result.get("arm")), []).append(unreachable / len(criteria))
    return {
        arm: {
            "mean_fraction_unreachable": statistics.fmean(values),
            "runs_entirely_unreachable": sum(1 for value in values if value >= 1.0 - 1e-9),
            "runs": len(values),
        }
        for arm, values in sorted(by_arm.items())
    }


def catalog_violation_rate(results: list[dict[str, Any]]) -> dict[str, float]:
    """How often an arm names a size that is not on the standard series.

    A separate failure mode from getting the arithmetic wrong, and one only the
    direct arms can exhibit: A1 and A2 select from the series by construction, so
    they cannot leave it. Worth reporting precisely because it is a difference in
    kind rather than degree.
    """
    by_arm: dict[str, list[int]] = {}
    for result in results:
        by_arm.setdefault(str(result.get("arm")), []).append(
            1 if result.get("catalog_violations") else 0)
    return {arm: sum(flags) / len(flags) for arm, flags in sorted(by_arm.items()) if flags}
