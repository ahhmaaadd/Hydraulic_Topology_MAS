"""Deterministic scoring and selection of sizing candidates.

Mirrors the topology stage: the model proposes several strategies, and a
deterministic score decides between them. The score is not a preference - it is
the certificate. A candidate that proves more criteria wins, and among
candidates that prove the same set, the smaller design wins.

That last clause matters. Without it the winning strategy is always "go up three
bore sizes", which passes everything and is a bad design.
"""

from __future__ import annotations

from typing import Any

from .apply import apply_candidate, oversizing_index


# A design this far above the smallest that meets the requirements is not a
# design, it is a way of passing the verifier. Priced as a penalty it lost to
# brute force: +10 per proved criterion against -2 per unit of waste means a
# 5.8x oversized clamp still outscores a tight one. So it is a constraint.
#
# The number is a policy choice, not physics. Rounding a bore up to the ISO
# series already costs about 1.3x on area in the worst case, and a second size
# up costs about 1.6x - so anything past that is deliberate, not granularity.
OVERSIZING_CEILING = 1.75
from .certify import SizingCertificate
from .contract import AcceptanceContract
from .schemas_sizing import SizingCandidate, SizingCandidateScore, SizingPlanSet


def score_candidate(
    candidate: SizingCandidate,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    load_tolerance: float = 0.0,
) -> tuple[SizingCandidateScore, dict[str, Any], dict[str, float], SizingCertificate | None]:
    try:
        sizing, throttles, certificate, problems = apply_candidate(
            candidate, topology, requirements, contract, load_tolerance=load_tolerance)
    except Exception as error:  # noqa: BLE001 - an unusable candidate is a score, not a crash
        return (
            SizingCandidateScore(
                candidate_id=candidate.candidate_id, eligible=False, score=-1e6,
                errors=[f"{type(error).__name__}: {error}"],
            ),
            {}, {}, None,
        )

    counts = certificate.counts()
    index = oversizing_index(sizing, contract, topology, requirements)
    errors = list(problems)
    if index > OVERSIZING_CEILING:
        errors.append(
            f"oversizing index {index:.2f} exceeds the {OVERSIZING_CEILING:.2f} ceiling: "
            "the design meets the requirements by being large rather than by being right")
    errors.extend(
        finding.message for finding in certificate.findings if finding.severity == "error"
    )
    warnings = [
        finding.message for finding in certificate.findings if finding.severity == "warning"
    ]
    # Proved criteria earn; unresolved ones cost; bulk costs a little. The
    # oversizing term is small on purpose - it should break ties between designs
    # that verify equally, never override a verification result.
    score = (
        10.0 * counts.get("PROVED", 0)
        - 25.0 * counts.get("REFUTED", 0)
        - 8.0 * counts.get("UNDECIDED", 0)
        - 20.0 * len(problems)
        - 2.0 * max(index - 1.0, 0.0)
    )
    return (
        SizingCandidateScore(
            candidate_id=candidate.candidate_id,
            eligible=not problems and not counts.get("REFUTED", 0),
            score=score,
            proved=counts.get("PROVED", 0),
            undecided=counts.get("UNDECIDED", 0),
            refuted=counts.get("REFUTED", 0),
            oversizing_index=round(index, 3),
            errors=errors,
            warnings=warnings,
        ),
        sizing, throttles, certificate,
    )


def evaluate_and_select_sizing(
    plan_set: SizingPlanSet,
    topology: dict,
    requirements: dict,
    contract: AcceptanceContract,
    load_tolerance: float = 0.0,
):
    """Return ``(candidate, sizing, throttles, certificate, scores)``."""
    evaluated = []
    for candidate in plan_set.candidates:
        score, sizing, throttles, certificate = score_candidate(
            candidate, topology, requirements, contract, load_tolerance)
        evaluated.append((candidate, score, sizing, throttles, certificate))

    ranked = sorted(
        evaluated,
        key=lambda item: (
            item[1].eligible,
            item[1].score,
            item[0].candidate_id == plan_set.preferred_candidate_id,
        ),
        reverse=True,
    )
    winner = ranked[0]
    for _, score, _, _, _ in evaluated:
        score.selected = score.candidate_id == winner[1].candidate_id
    return winner[0], winner[2], winner[3], winner[4], [item[1] for item in evaluated]
