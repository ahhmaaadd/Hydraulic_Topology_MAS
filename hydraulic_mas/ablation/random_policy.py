"""A-rand: the null control.

The question this arm exists to answer is uncomfortable and was not asked until
the v0.7.0 traces showed A1 and A2 producing identical designs on five of seven
problems. If the tools compute every number, the catalog is a short preferred
series, and the verifier scores the outcome, then how much of the result is the
model's judgement and how much is the scaffolding?

The way to find out is to remove the judgement and keep everything else. This arm
samples a policy uniformly at random from the same schema the LLM fills in, runs
it through the same tools, and scores it with the same verifier. Several
candidates are drawn and the best is selected, exactly as the LLM arm's
candidates are - so the comparison is like for like, and the only thing withheld
is that the choices mean anything.

Read the result carefully, because it is a two-sided instrument:

* if random policies certify as often as the LLM's, the model contributed
  nothing at this stage and the paper should say so plainly;
* if they do not, the gap is the first honest measurement of what the model's
  judgement is worth here.

Deterministic given a seed, so the control is reproducible.
"""

from __future__ import annotations

import random
from typing import Any

from ..sizing.contract import AcceptanceContract, compile_contract, load_tolerance
from ..sizing.schemas_sizing import (
    ActuatorDecision,
    SettingDecision,
    SizingCandidate,
    SupplyDecision,
)
from ..sizing.selection import score_candidate
from .arms import ArmResult, _finish


# Sampling ranges. The pressure range is the one judgement call in this module:
# a policy may design against anything from a fifth of the ceiling to just under
# it. Sampling far below the ceiling is what produces the enormous bores, and
# excluding that region would quietly make the control smarter than random.
DESIGN_PRESSURE_FRACTION = (0.20, 0.95)
RELIEF_MARGIN = (1.0, 1.6)
SPEED_AIM_OFF = (0.0, 0.30)
AREA_RATIO = (1.2, 4.0)


def _ceiling_bar(contract: AcceptanceContract, function_id: str | None) -> float:
    values = [
        criterion.target.to("bar")
        for criterion in contract.of_kind("pressure_ceiling")
        if criterion.function_id in (None, function_id)
    ]
    return min(values) if values else 250.0


def sample_candidate(topology: dict, requirements: dict, contract: AcceptanceContract,
                     rng: random.Random, candidate_id: str) -> SizingCandidate:
    """One policy drawn uniformly from the schema the LLM would have filled in."""
    cylinders = [
        (str(component["id"]), str(component.get("function_id")))
        for component in topology.get("components", [])
        if component.get("comp_type") == "cylinder"
    ]
    phase_ids = [
        str(phase.get("id"))
        for function in requirements.get("functions", []) or []
        for phase in function.get("motion_phases") or []
        if str(phase.get("motion")) in {"extend", "retract"}
    ] or ["unknown"]

    actuators = []
    for cylinder_id, function_id in cylinders:
        ceiling = _ceiling_bar(contract, function_id)
        strategy = rng.choice(["force", "area_ratio"])
        actuators.append(ActuatorDecision(
            cylinder_id=cylinder_id,
            design_pressure_bar=round(ceiling * rng.uniform(*DESIGN_PRESSURE_FRACTION), 1),
            governing_phase_id=rng.choice(phase_ids),
            rod_strategy=strategy,
            target_area_ratio=round(rng.uniform(*AREA_RATIO), 2) if strategy == "area_ratio" else None,
            justification="sampled uniformly from the policy schema; no engineering intent",
        ))

    settings = [
        SettingDecision(
            component_id=str(component["id"]),
            setting_bar=round(_ceiling_bar(contract, str(component.get("function_id")))
                              * rng.uniform(0.5, 1.0), 1),
            basis="sampled uniformly",
        )
        for component in topology.get("components", [])
        if component.get("comp_type") in {"pressure_reducing_valve", "sequence_valve",
                                          "counterbalance_valve"}
    ]

    return SizingCandidate(
        candidate_id=candidate_id,
        approach="uniform random policy (null control)",
        actuator_decisions=actuators,
        supply_decision=SupplyDecision(
            governing_phase_id=rng.choice(phase_ids),
            relief_margin=round(rng.uniform(*RELIEF_MARGIN), 3),
            speed_aim_off=round(rng.uniform(*SPEED_AIM_OFF), 3),
            justification="sampled uniformly from the policy schema; no engineering intent",
        ),
        setting_decisions=settings,
        engineering_notes=["null control: this policy was not reasoned about"],
        open_issues=[],
    )


def run_random_arm(problem_id: str, topology: dict, requirements: dict, *,
                   seed: int = 0, candidates: int = 3) -> ArmResult:
    """Draw ``candidates`` random policies, score them all, keep the best.

    Matched to the LLM arm's candidate count on purpose. Giving the control one
    draw against the model's three would understate it and flatter the result.
    """
    import time

    started = time.monotonic()
    result = ArmResult(arm="A-rand", problem_id=problem_id, seed=seed)
    contract = compile_contract(requirements, problem_id)
    tolerance = load_tolerance(requirements)
    rng = random.Random(f"{problem_id}:{seed}")

    best: tuple[float, Any, Any, Any] | None = None
    scores = []
    for index in range(candidates):
        candidate = sample_candidate(topology, requirements, contract, rng, f"rand_{index}")
        try:
            score, sizing, throttles, certificate = score_candidate(
                candidate, topology, requirements, contract, tolerance)
        except Exception as error:  # noqa: BLE001 - an unusable draw is a data point
            result.errors.append(f"{type(error).__name__}: {error}")
            continue
        scores.append(score)
        if certificate is not None and (best is None or score.score > best[0]):
            best = (score.score, sizing, throttles, certificate)

    result.proposal = {"scores": [item.model_dump(mode="json") for item in scores]}
    if best is None:
        result.verdict = "REFUTED"
        result.elapsed_s = time.monotonic() - started
        return result
    _finish(result, best[3], best[1], contract, topology, requirements)
    result.elapsed_s = time.monotonic() - started
    return result
