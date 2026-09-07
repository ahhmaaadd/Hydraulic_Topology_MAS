"""Rendering a certificate back to the model that produced the design.

Only the A0-V arm uses this. The decision it embodies is registered in
``docs/PREREGISTRATION_E1.md`` §4.3 and is worth restating here, because it looks
like a mistake if you meet it cold:

**A0-V is given the full certificate, including the computed enclosures.** Those
numbers came out of the tools A0-V is defined as not having. There is no way
round it - the feedback *is* tool output, so any feedback at all leaks
computation, and a partial disclosure would only be an arbitrary line drawn
somewhere less defensible.

So the leak is made total and deliberate, which turns A0-V into an *upper bound*
on what a toolless proposer can do. That makes the experiment readable in both
directions: if A1 still wins, tools matter even against a toolless arm handed
every advantage; if A0-V catches up, tool access was never the binding
constraint. A design where only one outcome is interesting is not worth running.

The text is written for a reader, not parsed by anything. It says what was
required, what the design achieves, and by how much it misses - never what to do
about it, because choosing the fix is the thing under test.
"""

from __future__ import annotations

from typing import Any

from ..sizing.certify import SizingCertificate


def _margin_phrase(margin: float | None) -> str:
    """Turn a signed margin into something a reader can act on.

    Positive is slack above what was required; negative is the shortfall. Very
    large ratios are reported as 'far above' rather than as a number, because a
    criterion with a near-zero denominator produces figures like 4.5e5 that carry
    no information and invite the model to chase them.
    """
    if margin is None:
        return ""
    if margin > 10:
        return "  (far above the requirement)"
    if margin >= 0:
        return f"  (slack {margin * 100:+.1f} %)"
    return f"  (SHORT by {abs(margin) * 100:.1f} %)"


def render_criteria(certificate: SizingCertificate) -> str:
    if not certificate.certificates:
        return "  (no criterion could be evaluated)"
    lines = []
    for item in certificate.certificates:
        lines.append(
            f"  [{item.verdict:9}] {item.criterion_id}\n"
            f"      required  {item.required}\n"
            f"      achieved  {item.achieved}{_margin_phrase(item.margin)}"
        )
        if item.note:
            lines.append(f"      note      {item.note}")
    return "\n".join(lines)


def render_findings(certificate: SizingCertificate) -> str:
    if not certificate.findings:
        return ""
    lines = [
        f"  [{finding.severity:7}] {finding.code}: {finding.message}"
        for finding in certificate.findings
    ]
    return "FINDINGS\n" + "\n".join(lines)


def render_operating_points(certificate: SizingCertificate) -> str:
    """Where the design actually sits, phase by phase.

    Included because a refuted velocity is not actionable on its own - the model
    needs to see whether the phase was flow-limited or pressure-limited to know
    which knob is the wrong one.
    """
    if not certificate.operating_points:
        return ""
    lines = []
    for phase_id, point in sorted(certificate.operating_points.items()):
        if not isinstance(point, dict):
            continue
        # The solver already records these in display units; re-scaling here
        # would only introduce a second convention to keep in step with the first.
        parts = []
        for key, label, unit in (
            ("velocity_m_min", "v", "m/min"),
            ("pump_pressure_bar", "p_pump", "bar"),
            ("supply_pressure_bar", "p_supply", "bar"),
            ("exhaust_pressure_bar", "p_exhaust", "bar"),
            ("relief_flow_lpm", "relief", "L/min"),
        ):
            value = point.get(key)
            if isinstance(value, (int, float)):
                parts.append(f"{label} {value:.3g} {unit}")
        regime = point.get("regime")
        if regime:
            # The regime is the actionable half: a flow_limited phase will not
            # respond to more pressure, and a pressure_limited one will not
            # respond to more flow.
            parts.append(f"regime {regime}")
        if point.get("feasible") is False:
            parts.append("NOT FEASIBLE")
        if parts:
            lines.append(f"  {phase_id}: " + "   ".join(parts))
    return ("OPERATING POINTS THIS DESIGN REACHES\n" + "\n".join(lines)) if lines else ""


def render_certificate_feedback(
    certificate: SizingCertificate | None,
    *,
    install_errors: list[str] | None = None,
    catalog_violations: list[str] | None = None,
    oversizing_index: float | None = None,
    over_ceiling: bool = False,
) -> str:
    """The whole certificate, as text, for a model that is about to revise."""
    blocks: list[str] = []

    if install_errors:
        blocks.append(
            "THE DESIGN COULD NOT BE INSTALLED\n"
            + "\n".join(f"  - {message}" for message in install_errors)
            + "\n\nNothing was evaluated. Fix these before anything else."
        )
        return "\n\n".join(blocks)

    if certificate is None:
        return "The design could not be evaluated at all. Return a complete, self-consistent sizing."

    counts = certificate.counts()
    blocks.append(
        f"VERDICT: {certificate.verdict}\n"
        f"  proved {counts.get('PROVED', 0)}   "
        f"undecided {counts.get('UNDECIDED', 0)}   "
        f"refuted {counts.get('REFUTED', 0)}"
    )
    blocks.append("CRITERION BY CRITERION\n" + render_criteria(certificate))

    findings = render_findings(certificate)
    if findings:
        blocks.append(findings)

    points = render_operating_points(certificate)
    if points:
        blocks.append(points)

    if catalog_violations:
        blocks.append(
            "OFF THE STANDARD SERIES (these sizes cannot be bought)\n"
            + "\n".join(f"  - {message}" for message in catalog_violations)
        )

    if oversizing_index is not None:
        line = (f"SIZE RELATIVE TO THE SMALLEST DESIGN THAT WOULD MEET THE LOADS: "
                f"{oversizing_index:.2f}x")
        if over_ceiling:
            # Said plainly, because a number on its own is not a rejection. The
            # v0.7.1 runs rejected designs on this and never told the loop, and
            # ten seeds then produced the identical refused design.
            line += ("\n  This is REJECTED. Meeting the requirements by being"
                     " large is not meeting them - come down in size and find"
                     " the margin somewhere else.")
        blocks.append(line)

    return "\n\n".join(blocks)


def summarise_round(certificate: SizingCertificate | None,
                    score: float, oversizing_index: float | None) -> dict[str, Any]:
    """One row of the convergence trajectory, for H5 and the convergence curves."""
    counts = certificate.counts() if certificate is not None else {}
    return {
        "verdict": certificate.verdict if certificate is not None else "ERROR",
        "counts": counts,
        "score": round(score, 3),
        "oversizing_index": oversizing_index,
        "refuted": [
            item.criterion_id for item in (certificate.certificates if certificate else [])
            if item.verdict == "REFUTED"
        ],
        "undecided": [
            item.criterion_id for item in (certificate.certificates if certificate else [])
            if item.verdict == "UNDECIDED"
        ],
    }
