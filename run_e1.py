#!/usr/bin/env python
"""E1 — the sweep registered in ``docs/PREREGISTRATION_E1.md``.

    python run_e1.py --dry-run            # no API key: stub client, checks the wiring
    python run_e1.py --arms A0-V          # the one cell E1 exists to fill
    python run_e1.py --arms A0-V --seeds 10

Three things this does that ``run_sweep`` does not, all of them because a 70-run
sweep against a paid API is not something you want to start twice.

**It is resumable.** Every completed cell is appended to ``evidence/E1_runs.jsonl``
immediately, and a restart skips whatever is already in the file. An exception at
cell 55 costs you cell 55, not the first 54.

**It writes a manifest first.** Model, temperature, code state, prompt hashes and
the ceiling setting, recorded before the first call. Section 5 of the
pre-registration asks for it, and a sweep whose configuration is only recoverable
from memory is a sweep that cannot be reported honestly.

**It turns the oversizing ceiling off.** Registered in section 4.5, and applied
here rather than in the module so that the v0.7.1 records still reproduce exactly
against the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

from hydraulic_mas.ablation import arms as arms_module
from hydraulic_mas.ablation import load_suite, run_cell

REFERENCE = "reference/validated_topologies.json"
EVIDENCE = pathlib.Path("evidence")
RUNS = EVIDENCE / "E1_runs.jsonl"
MANIFEST = EVIDENCE / "E1_manifest.json"

# Arms that need a live model. The rest are free and run in seconds.
NEEDS_MODEL = {"A0", "A0.5", "A0-V", "A1"}


def _git_state() -> dict[str, str]:
    def run(*command: str) -> str:
        try:
            return subprocess.run(command, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001 - a missing git is not a reason to abort
            return ""

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
    }


def _prompt_hashes() -> dict[str, str]:
    """Hash the prompts, so a silent edit mid-sweep is detectable afterwards."""
    from hydraulic_mas.ablation import prompts

    return {
        name: hashlib.sha256(getattr(prompts, name).encode()).hexdigest()[:16]
        for name in ("SYSTEM_A0", "SYSTEM_A05", "SYSTEM_A0V",
                     "CATALOG_TEXT", "DESIGN_BASIS", "INSTRUCTIONS",
                     "CANDIDATE_INSTRUCTIONS", "REPAIR_INSTRUCTIONS")
    }


def _done_cells() -> set[tuple[str, str, int]]:
    if not RUNS.exists():
        return set()
    done = set()
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        done.add((record["arm"], record["problem_id"], record["seed"]))
    return done


def _build_stub(topology: dict):
    """A stub bound to one problem's cylinders."""
    from hydraulic_mas.ablation.schemas import DirectCylinder, DirectSizing, DirectSizingSet

    ids = [str(c["id"]) for c in topology.get("components", [])
           if c.get("comp_type") == "cylinder"]
    ladder = [(63, 36), (80, 45), (100, 56), (125, 70)]

    def factory(seed: int):
        state = {"i": 0}

        def client(system: str, prompt: str, tools: list, schema: type = DirectSizing):
            assert not tools, "the stub must never be handed tools"
            bore, rod = ladder[min(state["i"], len(ladder) - 1)]
            state["i"] += 1
            one = DirectSizing(
                cylinders=[DirectCylinder(cylinder_id=i, bore_mm=bore, rod_mm=rod)
                           for i in ids],
                displacement_cm3=28.0, pump_speed_rpm=1500.0, relief_bar=70.0,
                motor_kw=5.5, reservoir_l=100, valve_size="NG10",
                reasoning="dry-run stub, not a model")
            return DirectSizingSet(candidates=[one]) if schema is DirectSizingSet else one

        return client

    return factory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arms", default="A0-V",
                        help="comma separated (default: A0-V, the cell E1 adds)")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--problems", default="",
                        help="comma separated; default is all seven")
    parser.add_argument("--dry-run", action="store_true",
                        help="stub client, no API key, no cost")
    parser.add_argument("--keep-ceiling", action="store_true",
                        help="leave the oversizing ceiling on (NOT the registered E1 setting)")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore and overwrite any existing E1_runs.jsonl")
    args = parser.parse_args()

    # Registered in PREREGISTRATION_E1 section 4.5. Set here rather than in the
    # module so the v0.7.1 records still reproduce against the default.
    arms_module.APPLY_OVERSIZING_CEILING = args.keep_ceiling
    if args.keep_ceiling:
        print("!! running WITH the oversizing ceiling - this is not the registered E1 setting")

    suite = load_suite(REFERENCE)
    problem_ids = [p.strip() for p in args.problems.split(",") if p.strip()] or suite.ids()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    EVIDENCE.mkdir(exist_ok=True)
    if args.fresh and RUNS.exists():
        RUNS.unlink()
    done = _done_cells()
    if done:
        print(f"resuming: {len(done)} cells already recorded in {RUNS}")

    model = None
    if not args.dry_run and any(arm in NEEDS_MODEL for arm in arms):
        from hydraulic_mas.config import Settings
        from hydraulic_mas.models import build_chat_model

        settings = Settings.from_env()
        model = build_chat_model(settings, fast=False)

    MANIFEST.write_text(json.dumps({
        "study": "E1",
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "arms": arms, "problems": problem_ids, "seeds": args.seeds,
        "dry_run": args.dry_run,
        "oversizing_ceiling_applied": arms_module.APPLY_OVERSIZING_CEILING,
        "candidates": arms_module.DEFAULT_CANDIDATES,
        "repair_rounds": arms_module.DEFAULT_REPAIR_ROUNDS,
        "model": None if model is None else getattr(model, "model_name", str(model)),
        "git": _git_state(),
        "prompt_sha256_16": _prompt_hashes(),
    }, indent=2), encoding="utf-8")
    print(f"manifest -> {MANIFEST}")

    tally: dict[str, int] = {}
    with RUNS.open("a", encoding="utf-8") as handle:
        for arm in arms:
            seeds = [0] if arm == "A2" else range(args.seeds)
            for problem_id in problem_ids:
                stub = _build_stub(suite.problems[problem_id]["topology"])
                for seed in seeds:
                    if (arm, problem_id, seed) in done:
                        continue
                    started = time.monotonic()
                    try:
                        if args.dry_run:
                            factories = {"verified_client_factory": stub,
                                         "direct_client_factory": stub}
                        elif model is not None:
                            from hydraulic_mas.ablation.client import (
                                build_direct_client,
                                build_verified_client,
                            )
                            factories = {
                                "verified_client_factory":
                                    lambda s: build_verified_client(model),
                                "direct_client_factory":
                                    lambda s: build_direct_client(model),
                            }
                        else:
                            factories = {}
                        result = run_cell(arm, suite, problem_id, seed, **factories)
                    except Exception as error:  # noqa: BLE001 - a dead cell is a data point
                        from hydraulic_mas.ablation import ArmResult

                        result = ArmResult(arm=arm, problem_id=problem_id, seed=seed)
                        result.errors.append(f"{type(error).__name__}: {error}")
                        result.elapsed_s = time.monotonic() - started
                    handle.write(json.dumps(result.to_dict()) + "\n")
                    handle.flush()
                    tally[result.verdict] = tally.get(result.verdict, 0) + 1
                    extra = ""
                    if isinstance(result.proposal, dict) and "repair_rounds_used" in result.proposal:
                        extra = (f"  rounds={result.proposal['repair_rounds_used']}"
                                 f" calls={result.proposal['model_calls']}")
                    print(f"  {arm:6} {problem_id} seed {seed:2}  "
                          f"{result.verdict:9} {result.elapsed_s:6.1f}s{extra}")

    print(f"\n{RUNS}  ->  {dict(sorted(tally.items()))}")
    proved = tally.get("PROVED", 0)
    total = sum(tally.values())
    if total:
        print(f"proved {proved}/{total} = {proved / total:.1%}")
    print("\nAnalysis is a separate step and is not run from here: "
          "PREREGISTRATION_E1 section 13 requires the analysis script to be "
          "written and tested against synthetic ground truth first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
