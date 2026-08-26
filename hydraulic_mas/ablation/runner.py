"""Run the ablation across arms, problems and seeds, and write the raw record.

Seeds matter more here than they usually do. The claim is not that a model got
one design wrong; it is that a model without arithmetic is *unreliable* at this,
and unreliability is a distribution. One run per cell cannot distinguish a system
that always works from one that worked once.

Everything is written out as raw per-run records. Tables are computed from the
file afterwards by ``report.py``, so a summary can never disagree with the data
it came from, and re-analysis never needs the model again.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..sizing.contract import compile_contract
from .arms import (
    ARMS,
    ArmResult,
    run_deterministic_arm,
    run_direct_arm,
    run_full_arm,
)


@dataclass
class Suite:
    """The benchmark, as the harness sees it."""

    problems: dict[str, dict[str, Any]]
    load_tolerance: Callable[[dict], float] = lambda requirements: 0.0
    problem_ids: list[str] = field(default_factory=list)

    def ids(self) -> list[str]:
        return self.problem_ids or sorted(self.problems)


def load_suite(path: str | pathlib.Path) -> Suite:
    from ..sizing.planner import _load_tolerance

    problems = json.loads(pathlib.Path(path).read_text())
    return Suite(problems=problems, load_tolerance=_load_tolerance)


def run_cell(arm: str, suite: Suite, problem_id: str, seed: int, *,
             direct_client_factory: Callable[[int], Any] | None = None,
             full_planner: Callable | None = None) -> ArmResult:
    entry = suite.problems[problem_id]
    topology = entry["topology"]
    requirements = entry["requirements"]
    tolerance = suite.load_tolerance(requirements)
    query = str(entry.get("user_query") or problem_id)

    if arm == "A2":
        return run_deterministic_arm(problem_id, topology, requirements, seed=seed)
    if arm == "A1":
        if full_planner is None:
            raise ValueError("A1 needs a planner; pass full_planner=")
        return run_full_arm(full_planner, problem_id, topology, requirements,
                            seed=seed, load_tolerance=tolerance)
    if arm in {"A0", "A0.5"}:
        if direct_client_factory is None:
            raise ValueError(f"{arm} needs a model; pass direct_client_factory=")
        return run_direct_arm(
            arm, direct_client_factory(seed), problem_id, query, topology, requirements,
            seed=seed, load_tolerance=tolerance, with_tools=(arm == "A0.5"))
    raise ValueError(f"unknown arm {arm!r}")


def run_sweep(suite: Suite, *, arms: Iterable[str] = ARMS, seeds: Iterable[int] = (0,),
              direct_client_factory: Callable[[int], Any] | None = None,
              full_planner: Callable | None = None,
              on_result: Callable[[ArmResult], None] | None = None,
              out_path: str | pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Every (arm, problem, seed) cell, written out as it completes.

    Written incrementally on purpose: a sweep across four arms and ten seeds is
    hundreds of model calls, and losing all of them to one exception at cell 340
    is not an acceptable failure mode.
    """
    records: list[dict[str, Any]] = []
    handle = None
    if out_path is not None:
        path = pathlib.Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w", encoding="utf-8")
    try:
        for arm in arms:
            # A2 is deterministic; running it ten times measures nothing.
            arm_seeds = [0] if arm == "A2" else list(seeds)
            for problem_id in suite.ids():
                for seed in arm_seeds:
                    result = run_cell(
                        arm, suite, problem_id, seed,
                        direct_client_factory=direct_client_factory,
                        full_planner=full_planner)
                    record = result.to_dict()
                    records.append(record)
                    if handle is not None:
                        handle.write(json.dumps(record) + "\n")
                        handle.flush()
                    if on_result is not None:
                        on_result(result)
    finally:
        if handle is not None:
            handle.close()
    return records


def load_records(path: str | pathlib.Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
