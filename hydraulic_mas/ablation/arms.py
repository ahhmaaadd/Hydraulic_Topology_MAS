"""The conditions the paper compares, behind one interface.

                       tools   verifier   repair
    A0    neural only    no       no        no     one shot
    A0.5  + arithmetic   yes      no        no     one shot
    A0-V  + verifier     no       yes       yes    the E1 cell
    A1    full system    yes      yes       yes
    A2    symbolic only  yes      yes       -      no model at all

Every arm returns the same record and every arm is judged by the same verifier on
the same contract, including the ones that never see it while designing. That is
the whole design of the experiment: the judge is constant, so a difference
between arms is a difference in the designs, not in how they were marked.

**A0-V is what makes this a factorial rather than a ladder.** A0 and A1 differ in
two ways at once - tools *and* the verifier - so the gap between them cannot be
attributed to either. Crossing the two factors puts a fourth cell in the corner
that was empty, and `A0-V vs A1` then reads the tools manipulation with the
verifier held constant. See ``docs/PREREGISTRATION_E1.md``.

The model arms take a client callable rather than constructing one, so the
harness can be exercised offline against a stub - a suite that cannot run without
an API key is a suite that stops being run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..sizing.apply import oversizing_index
from ..sizing.certify import SizingCertificate
from ..sizing.contract import AcceptanceContract, compile_contract
from ..sizing.planner import size_problem
from ..sizing.selection import OVERSIZING_CEILING
from ..sizing.tools import build_sizing_tools
from .apply_direct import apply_direct
from .feedback import render_certificate_feedback, summarise_round
from .prompts import (
    SYSTEM_A0,
    SYSTEM_A05,
    SYSTEM_A0V,
    build_candidate_prompt,
    build_direct_prompt,
    build_repair_prompt,
)
from .schemas import DirectSizing, DirectSizingSet


ARMS = ("A0", "A0.5", "A0-V", "A1", "A2", "A-rand")

ARM_DESCRIPTION = {
    "A0": "neural only: catalog as text, no tools, no verifier, single shot",
    "A0.5": "neural plus arithmetic tools, no verifier and no repair, single shot",
    "A0-V": "neural plus the verifier but no arithmetic tools: scored candidates and a bounded repair loop",
    "A1": "full system: tools, interval verifier, scored candidates, repair loop",
    "A2": "symbolic only: deterministic planner with a searched relief policy",
    "A-rand": "null control: policy sampled uniformly from the same schema, same tools, same verifier",
}

# E1 runs with the oversizing ceiling out of the certificate path: it produced
# 100% of A1's refutations in v0.7.1 while being identical across every arm and
# seed on four of seven problems, so it discriminated designs barely at all and
# verdicts entirely. Registered in PREREGISTRATION_E1 §4.5. Left switchable
# rather than deleted so the historical runs can still be reproduced exactly.
APPLY_OVERSIZING_CEILING = True

# Matched to A1's candidate count. Giving A0-V one shot against A1's three would
# measure how many attempts each arm had, not whether tools helped.
DEFAULT_CANDIDATES = 3
DEFAULT_REPAIR_ROUNDS = 3


@dataclass
class ArmResult:
    arm: str
    problem_id: str
    seed: int
    verdict: str = "ERROR"
    counts: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, int] = field(default_factory=dict)
    sizing: dict[str, Any] = field(default_factory=dict)
    certificate: dict[str, Any] = field(default_factory=dict)
    proposal: dict[str, Any] = field(default_factory=dict)
    catalog_violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    oversizing_index: float | None = None
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm, "problem_id": self.problem_id, "seed": self.seed,
            "verdict": self.verdict, "counts": self.counts, "coverage": self.coverage,
            "sizing": self.sizing, "certificate": self.certificate,
            "proposal": self.proposal, "catalog_violations": self.catalog_violations,
            "errors": self.errors, "oversizing_index": self.oversizing_index,
            "elapsed_s": round(self.elapsed_s, 2),
        }


# A callable that takes (system_prompt, user_prompt, tools) and returns a
# DirectSizing. Kept this narrow so a stub can stand in for a model.
DirectClient = Callable[[str, str, list[Any]], DirectSizing]


def _finish(result: ArmResult, certificate: SizingCertificate | None,
            sizing: dict, contract: AcceptanceContract,
            topology: dict, requirements: dict, *,
            apply_ceiling: bool | None = None) -> ArmResult:
    if certificate is None:
        result.verdict = "ERROR"
        return result
    result.verdict = certificate.verdict
    result.counts = certificate.counts()
    result.coverage = certificate.coverage()
    result.certificate = certificate.to_dict()
    result.sizing = sizing
    try:
        result.oversizing_index = round(
            oversizing_index(sizing, contract, topology, requirements), 3)
    except Exception:  # noqa: BLE001 - a metric failing must not lose the result
        result.oversizing_index = None
    # Meeting the requirements by being enormous is not meeting them. Applied
    # here rather than inside the certificate so that every arm is held to the
    # same ceiling, including the ones that never see a scoring function.
    ceiling = APPLY_OVERSIZING_CEILING if apply_ceiling is None else apply_ceiling
    if (ceiling and result.oversizing_index is not None
            and result.oversizing_index > OVERSIZING_CEILING):
        result.verdict = "REFUTED"
        result.errors.append(
            f"oversizing index {result.oversizing_index:.2f} exceeds the "
            f"{OVERSIZING_CEILING:.2f} ceiling")
    return result


def run_direct_arm(arm: str, client: DirectClient, problem_id: str, user_query: str,
                   topology: dict, requirements: dict, *, seed: int = 0,
                   load_tolerance: float = 0.0, with_tools: bool = False) -> ArmResult:
    """A0 and A0.5: the model states the finished design and nothing repairs it."""
    started = time.monotonic()
    result = ArmResult(arm=arm, problem_id=problem_id, seed=seed)
    contract = compile_contract(requirements, problem_id)
    tools: list[Any] = []
    if with_tools:
        # Arithmetic only. The verifier tool is what separates A0.5 from A1 and
        # must not leak across.
        tools = [
            tool for tool in build_sizing_tools(topology, requirements, contract, load_tolerance)
            if getattr(tool, "name", "") != "evaluate_sizing_policy"
        ]
    system = SYSTEM_A05 if with_tools else SYSTEM_A0
    prompt = build_direct_prompt(user_query, topology, requirements, contract)
    try:
        proposal = client(system, prompt, tools)
    except Exception as error:  # noqa: BLE001 - a failed generation is a data point
        result.errors.append(f"{type(error).__name__}: {error}")
        result.elapsed_s = time.monotonic() - started
        return result

    result.proposal = proposal.model_dump(mode="json")
    applied = apply_direct(proposal, topology, requirements, contract, load_tolerance)
    result.catalog_violations = applied.catalog_violations
    result.errors.extend(applied.install_errors)
    if applied.install_errors:
        # A design that cannot even be installed is not an error in the harness;
        # it is the arm's result, and it counts as one.
        result.verdict = "REFUTED"
        result.elapsed_s = time.monotonic() - started
        return result
    _finish(result, applied.certificate, applied.sizing, contract, topology, requirements)
    result.elapsed_s = time.monotonic() - started
    return result


def run_deterministic_arm(problem_id: str, topology: dict, requirements: dict,
                          *, seed: int = 0) -> ArmResult:
    """A2: no model anywhere in the loop."""
    started = time.monotonic()
    result = ArmResult(arm="A2", problem_id=problem_id, seed=seed)
    contract = compile_contract(requirements, problem_id)
    try:
        planned = size_problem(problem_id, topology, requirements)
    except Exception as error:  # noqa: BLE001
        result.errors.append(f"{type(error).__name__}: {error}")
        result.elapsed_s = time.monotonic() - started
        return result
    _finish(result, planned.certificate, planned.sizing, contract, topology, requirements)
    result.proposal = {"notes": planned.notes, "repairs": planned.repairs}
    result.elapsed_s = time.monotonic() - started
    return result


def run_full_arm(planner: Callable[[dict, dict, AcceptanceContract, float], Any],
                 problem_id: str, topology: dict, requirements: dict,
                 *, seed: int = 0, load_tolerance: float = 0.0) -> ArmResult:
    """A1: the shipped system, driven through the same record as the others.

    ``planner`` returns whatever ``evaluate_and_select_sizing`` returns, so the
    graph and this harness stay one implementation rather than two that drift.
    """
    started = time.monotonic()
    result = ArmResult(arm="A1", problem_id=problem_id, seed=seed)
    contract = compile_contract(requirements, problem_id)
    try:
        candidate, sizing, _throttles, certificate, scores = planner(
            topology, requirements, contract, load_tolerance)
    except Exception as error:  # noqa: BLE001
        result.errors.append(f"{type(error).__name__}: {error}")
        result.elapsed_s = time.monotonic() - started
        return result
    result.proposal = {
        "selected": candidate.model_dump(mode="json"),
        "scores": [item.model_dump(mode="json") for item in scores],
    }
    _finish(result, certificate, sizing, contract, topology, requirements)
    result.elapsed_s = time.monotonic() - started
    return result


# ---------------------------------------------------------------------------
# A0-V: the verifier without the tools
# ---------------------------------------------------------------------------

# The model states finished numbers, so the client must be told which schema to
# fill: a set of candidates on the opening call, a single design on each repair.
VerifiedClient = Callable[[str, str, list[Any], type], Any]


def _direct_score(certificate: SizingCertificate | None, index: float | None,
                  install_faults: int) -> float:
    """The selection score from ``sizing/selection.py``, over a stated design.

    Reimplemented rather than imported because ``score_candidate`` takes a
    *policy* and applies it with the tools - which is precisely what this arm is
    defined as not having. The weights are copied deliberately: if they diverge,
    the two arms are being ranked by different objectives and the comparison
    stops meaning anything. A test asserts they stay equal.
    """
    if certificate is None:
        return -1e6
    counts = certificate.counts()
    score = (
        10.0 * counts.get("PROVED", 0)
        - 25.0 * counts.get("REFUTED", 0)
        - 8.0 * counts.get("UNDECIDED", 0)
        - 20.0 * install_faults
    )
    if index is not None:
        score -= 2.0 * max(index - 1.0, 0.0)
    return score


@dataclass
class _Attempt:
    """One design that was tried, whether it came from the opening set or a repair."""

    proposal: Any
    applied: Any
    score: float
    index: float | None
    over_ceiling: bool = False

    @property
    def clean(self) -> bool:
        """Would this be *reported* as proved?

        Not merely "does the certificate say PROVED". Under the default
        configuration a design above the oversizing ceiling is reported REFUTED,
        and a loop that stopped on the certificate alone would halt on a design
        it is about to be marked down for - which is exactly the defect found in
        A1's v0.7.1 runs, where ten seeds produced the identical rejected design
        because the rejection never reached the thing that could act on it.
        """
        return (self.applied.certificate is not None
                and self.applied.certificate.verdict == "PROVED"
                and not self.applied.install_errors
                and not self.over_ceiling)


def _evaluate(proposal: DirectSizing, topology: dict, requirements: dict,
              contract: AcceptanceContract, load_tolerance: float,
              *, apply_ceiling: bool = True) -> _Attempt:
    applied = apply_direct(proposal, topology, requirements, contract, load_tolerance)
    index = None
    if applied.certificate is not None and not applied.install_errors:
        try:
            index = round(oversizing_index(applied.sizing, contract, topology, requirements), 3)
        except Exception:  # noqa: BLE001 - a metric failing must not lose the attempt
            index = None
    score = _direct_score(
        None if applied.install_errors else applied.certificate,
        index, len(applied.install_errors))
    over = bool(apply_ceiling and index is not None and index > OVERSIZING_CEILING)
    return _Attempt(proposal=proposal, applied=applied, score=score, index=index,
                    over_ceiling=over)


def run_direct_verified_arm(
    client: VerifiedClient, problem_id: str, user_query: str,
    topology: dict, requirements: dict, *, seed: int = 0,
    load_tolerance: float = 0.0,
    candidates: int = DEFAULT_CANDIDATES,
    rounds: int = DEFAULT_REPAIR_ROUNDS,
    apply_ceiling: bool | None = None,
) -> ArmResult:
    """A0-V: the model computes every number itself, and the verifier answers back.

    Structure mirrors A1 exactly - propose a set, score them all deterministically,
    keep the best, then revise against the certificate - with one factor removed:
    no arithmetic tool is ever bound to the call. The only thing that differs is
    who does the multiplying.

    Termination is the first clean certificate. Failing that, the best-scoring
    attempt across every round is returned, not the last one, so a repair that
    makes matters worse cannot be reported as the arm's answer. Every round is
    recorded either way, which is what the convergence curves and H5 are read from.
    """
    started = time.monotonic()
    result = ArmResult(arm="A0-V", problem_id=problem_id, seed=seed)
    contract = compile_contract(requirements, problem_id)
    base_prompt = build_direct_prompt(user_query, topology, requirements, contract)
    trajectory: list[dict[str, Any]] = []
    calls = 0

    # --- opening round: several candidates, scored, best kept ---------------
    try:
        proposed = client(SYSTEM_A0V, build_candidate_prompt(
            user_query, topology, requirements, contract), [], DirectSizingSet)
        calls += 1
    except Exception as error:  # noqa: BLE001 - a failed generation is a data point
        result.errors.append(f"{type(error).__name__}: {error}")
        result.elapsed_s = time.monotonic() - started
        return result

    ceiling = APPLY_OVERSIZING_CEILING if apply_ceiling is None else apply_ceiling
    attempts = [
        _evaluate(candidate, topology, requirements, contract, load_tolerance,
                  apply_ceiling=ceiling)
        for candidate in proposed.candidates[:candidates]
    ]
    if not attempts:
        result.errors.append("the model returned no candidates")
        result.elapsed_s = time.monotonic() - started
        return result

    best = max(attempts, key=lambda item: item.score)
    trajectory.append({
        "round": 0, "kind": "candidates", "n": len(attempts),
        "scores": [round(item.score, 3) for item in attempts],
        **summarise_round(best.applied.certificate, best.score, best.index),
    })

    # --- repair rounds ------------------------------------------------------
    used = 0
    while not best.clean and used < rounds:
        used += 1
        feedback = render_certificate_feedback(
            best.applied.certificate,
            install_errors=best.applied.install_errors,
            catalog_violations=best.applied.catalog_violations,
            oversizing_index=best.index,
            over_ceiling=best.over_ceiling,
        )
        try:
            revised = client(SYSTEM_A0V, build_repair_prompt(
                base_prompt, best.proposal, feedback,
                attempt=used, attempts_left=rounds - used + 1), [], DirectSizing)
            calls += 1
        except Exception as error:  # noqa: BLE001
            # A failed revision ends the loop but does not lose the design that
            # prompted it; the arm is scored on what it actually produced.
            result.errors.append(f"round {used}: {type(error).__name__}: {error}")
            break
        attempt = _evaluate(revised, topology, requirements, contract, load_tolerance,
                            apply_ceiling=ceiling)
        trajectory.append({
            "round": used, "kind": "repair",
            **summarise_round(attempt.applied.certificate, attempt.score, attempt.index),
        })
        if attempt.score > best.score:
            best = attempt

    # --- record -------------------------------------------------------------
    result.proposal = {
        "selected": best.proposal.model_dump(mode="json"),
        "candidates": [item.proposal.model_dump(mode="json") for item in attempts],
        "candidate_scores": [round(item.score, 3) for item in attempts],
        "trajectory": trajectory,
        "repair_rounds_used": used,
        "repair_rounds_allowed": rounds,
        "model_calls": calls,
        "converged": best.clean,
    }
    result.catalog_violations = best.applied.catalog_violations
    result.errors.extend(best.applied.install_errors)
    if best.applied.install_errors:
        result.verdict = "REFUTED"
        result.elapsed_s = time.monotonic() - started
        return result
    _finish(result, best.applied.certificate, best.applied.sizing, contract,
            topology, requirements, apply_ceiling=apply_ceiling)
    result.elapsed_s = time.monotonic() - started
    return result
