"""Regenerate every table the paper quotes, from data rather than from memory.

One entry point, one output directory. Nothing here recomputes a design: the
offline evidence is derived from the deterministic arm and the verifier, and the
arms that need a model read their raw records off disk. So a table can always be
traced to the run that produced it, and re-running the analysis never needs the
model again.

Tables produced
---------------
T1  coverage           what the certificates range over, and what they do not
T2  verdicts           per-arm pass rates, with within-problem spread
T3  errors             prediction and requirement error distributions
T4  seeded defects     detection rate by severity, with the false-positive rate
T5  detection curve    detection against defect magnitude
T6  transient          quasi-static error, and where the assumption stops holding
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from ..sizing.contract import compile_contract
from ..sizing.coverage import REASON_TEXT, coverage_summary
from ..sizing.planner import size_problem
from ..sizing.transient import cross_check, summarise_cross_check
from .metrics import catalog_violation_rate, error_table, unreachable_rate, verdict_rates
from .seeded_defects import (
    detection_curve,
    detection_table,
    graded_defects,
    run_seeded_defects,
)


def coverage_report(problems: dict[str, Any]) -> dict[str, Any]:
    rows = []
    totals = {"stated": 0, "answered": 0, "unanswered": 0, "checks": 0}
    reasons: dict[str, int] = {}
    for problem_id in sorted(problems):
        entry = problems[problem_id]
        planned = size_problem(problem_id, entry["topology"], entry["requirements"])
        coverage = planned.certificate.coverage()
        summary = coverage_summary(planned.certificate.uncovered)
        for key in totals:
            totals[key] += coverage[key]
        for reason, count in summary.items():
            reasons[reason] = reasons.get(reason, 0) + count
        methods: dict[str, int] = {}
        for item in planned.certificate.certificates:
            methods[item.method] = methods.get(item.method, 0) + 1
        rows.append({
            "problem_id": problem_id,
            **coverage,
            "verdict": planned.certificate.verdict,
            "counts": planned.certificate.counts(),
            "uncovered_reasons": summary,
            "methods": methods,
        })
    interval_methods = {"monotone-corner", "corner+sampled"}
    all_methods: dict[str, int] = {}
    for row in rows:
        for method, count in row["methods"].items():
            all_methods[method] = all_methods.get(method, 0) + count
    return {
        "per_problem": rows,
        "totals": totals,
        "answered_fraction": totals["answered"] / totals["stated"] if totals["stated"] else 0.0,
        "uncovered_reasons": reasons,
        "reason_glossary": REASON_TEXT,
        "methods": all_methods,
        "interval_fraction": (
            sum(count for method, count in all_methods.items() if method in interval_methods)
            / sum(all_methods.values()) if all_methods else 0.0
        ),
    }


def transient_report(problems: dict[str, Any]) -> dict[str, Any]:
    per_problem = {}
    everything = []
    for problem_id in sorted(problems):
        entry = problems[problem_id]
        planned = size_problem(problem_id, entry["topology"], entry["requirements"])
        results = cross_check(entry["topology"], entry["requirements"],
                              planned.sizing, planned.throttle_k)
        everything.extend(results)
        per_problem[problem_id] = {
            "summary": summarise_cross_check(results),
            "phases": [item.to_dict() for item in results],
        }
    return {"overall": summarise_cross_check(everything), "per_problem": per_problem}


def defect_report(problems: dict[str, Any]) -> dict[str, Any]:
    outcomes = run_seeded_defects(problems)
    graded = run_seeded_defects(problems, defects=graded_defects())
    return {
        "table": detection_table(outcomes),
        "curve": detection_curve(graded),
        "outcomes": [item.to_dict() for item in outcomes],
    }


def arm_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "verdicts": verdict_rates(records),
        "errors": error_table(records),
        "catalog_violation_rate": catalog_violation_rate(records),
        "unreachable": unreachable_rate(records),
    }


def build_evidence(problems: dict[str, Any], out_dir: str | pathlib.Path,
                   records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything that can be produced offline, plus the arm tables if runs exist."""
    directory = pathlib.Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    evidence: dict[str, Any] = {
        "T1_coverage": coverage_report(problems),
        "T4_T5_seeded_defects": defect_report(problems),
        "T6_transient": transient_report(problems),
    }
    if records:
        evidence["T2_T3_arms"] = arm_report(records)
    for name, payload in evidence.items():
        (directory / f"{name}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (directory / "evidence.md").write_text(render_markdown(evidence), encoding="utf-8")
    return evidence


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def render_markdown(evidence: dict[str, Any]) -> str:
    lines: list[str] = ["# Evidence tables", ""]

    coverage = evidence["T1_coverage"]
    lines += [
        "## T1 — What the certificates range over", "",
        "| Problem | Stated | Answered | Checks | Verdict | P/U/R |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in coverage["per_problem"]:
        counts = row["counts"]
        lines.append(
            f"| {row['problem_id']} | {row['stated']} | {row['answered']} | {row['checks']} | "
            f"{row['verdict']} | {counts['PROVED']}/{counts['UNDECIDED']}/{counts['REFUTED']} |")
    totals = coverage["totals"]
    lines += [
        f"| **total** | **{totals['stated']}** | **{totals['answered']}** "
        f"(**{_pct(coverage['answered_fraction'], 0)}**) | **{totals['checks']}** | | |",
        "",
        f"Interval methods account for {_pct(coverage['interval_fraction'], 0)} of checks: "
        + ", ".join(f"{method} {count}" for method, count in sorted(coverage["methods"].items())),
        "",
        "Statements not answered, by reason:", "",
    ]
    for reason, count in sorted(coverage["uncovered_reasons"].items(),
                                key=lambda item: -item[1]):
        lines.append(f"- **{count}** — {reason}: {coverage['reason_glossary'][reason]}")
    lines.append("")

    if "T2_T3_arms" in evidence:
        arms = evidence["T2_T3_arms"]
        lines += [
            "## T2 — Arms", "",
            "| Arm | Runs | Proved | Within-problem SD | Oversizing | Catalog violations |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for arm, row in arms["verdicts"].items():
            lines.append(
                f"| {arm} | {row['runs']} | {_pct(row['proved_rate'], 0)} | "
                f"{row['mean_within_problem_sd']:.3f} | "
                f"{row['median_oversizing_index'] if row['median_oversizing_index'] is not None else 'n/a'} | "
                f"{_pct(arms['catalog_violation_rate'].get(arm), 0)} |")
        lines += ["", "## T3 — Error distributions", ""]
        for name, table in arms["errors"].items():
            lines += [f"### {name.replace('_', ' ')}", "",
                      "| Arm | n | Median | p25 | p75 | Worst | Within 1% | Within 5% |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for arm, stats in table.items():
                if not stats.get("n"):
                    continue
                lines.append(
                    f"| {arm} | {stats['n']} | {_pct(stats['median'])} | {_pct(stats['p25'])} | "
                    f"{_pct(stats['p75'])} | {_pct(stats['worst'])} | "
                    f"{_pct(stats['within_1pct'], 0)} | {_pct(stats['within_5pct'], 0)} |")
            lines.append("")

    defects = evidence["T4_T5_seeded_defects"]["table"]
    lines += ["## T4 — Seeded defects", ""]
    null = defects.get("null", {})
    lines += [
        f"False-positive rate: **{_pct(null.get('false_positive_rate'), 0)}** "
        f"over {null.get('injections', 0)} null injections.", "",
        "| Severity | Injections | Detected | Rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for severity in ("subtle", "gross"):
        row = defects.get(severity)
        if row:
            lines.append(f"| {severity} | {row['injections']} | {row['detected']} | "
                         f"{_pct(row['detection_rate'], 0)} |")
    lines += ["", "## T5 — Detection against defect magnitude", ""]
    for family, rows in evidence["T4_T5_seeded_defects"]["curve"].items():
        lines += [f"**{family}**", "", "| Magnitude | n | Detected |", "| ---: | ---: | ---: |"]
        for row in rows:
            lines.append(f"| {_pct(row['magnitude'])} | {row['n']} | "
                         f"{_pct(row['detection_rate'], 0)} |")
        lines.append("")

    transient = evidence["T6_transient"]["overall"]
    lines += [
        "## T6 — Transient cross-check", "",
        f"- phases integrated: {transient['phases']}, all settled: "
        f"{not transient['unsettled'] and not transient['diverged']}",
        f"- settled velocity against the quasi-static solve: median "
        f"{_pct(transient['median_abs_velocity_error'], 2)}, worst "
        f"{_pct(transient['max_abs_velocity_error'], 2)}",
        f"- acceleration occupies a median {_pct(transient['median_stroke_fraction_settling'], 0)} "
        "of the stroke",
        f"- never reaching steady state: {transient['phases_never_reaching_steady_state'] or 'none'}",
        f"- mostly transient: {transient['phases_mostly_transient'] or 'none'}",
        "",
    ]
    return "\n".join(lines)
