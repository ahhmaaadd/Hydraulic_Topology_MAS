"""The four conditions the paper compares, behind one interface.

    A0    neural only            no tools, no verifier, one shot
    A0.5  neural + arithmetic    tools, no verifier, no repair, one shot
    A1    the full system        tools, verifier, repair loop
    A2    symbolic only          deterministic planner, no model at all

Every arm returns the same record and every arm is judged by the same verifier on
the same contract, including the two that never see it while designing. That is
the whole design of the experiment: the judge is constant, so a difference
between arms is a difference in the designs, not in how they were marked.

A0 and A0.5 need a live model. They take a client callable rather than
constructing one, so the harness can be exercised offline against a stub - a
suite that cannot run without an API key is a suite that stops being run.
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
from .prompts import SYSTEM_A0, SYSTEM_A05, build_direct_prompt
from .schemas import DirectSizing


ARMS = ("A0", "A0.5", "A1", "A2", "A-rand")

ARM_DESCRIPTION = {
    "A0": "neural only: catalog as text, no tools, no verifier, single shot",
    "A0.5": "neural plus arithmetic tools, no verifier and no repair, single shot",
    "A1": "full system: tools, interval verifier, scored candidates, repair loop",
    "A2": "symbolic only: deterministic planner with a searched relief policy",
    "A-rand": "null control: policy sampled uniformly from the same schema, same tools, same verifier",
}


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
            topology: dict, requirements: dict) -> ArmResult:
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
    if result.oversizing_index is not None and result.oversizing_index > OVERSIZING_CEILING:
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
