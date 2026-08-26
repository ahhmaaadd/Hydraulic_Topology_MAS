#!/usr/bin/env python3
"""Regenerate the paper's evidence.

    python run_evidence.py                          offline tables only
    python run_evidence.py --runs runs.jsonl        include arm tables
    python run_evidence.py --sweep --seeds 10       run the arms, then report

The offline tables need no model and no network: coverage, seeded defects, the
detection curve and the transient cross-check all come from the deterministic arm
and the verifier. Only the arm comparison needs A0, A0.5 and A1, and that is the
one part that costs money, so it is opt-in and its raw records are kept.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from hydraulic_mas.ablation.report import build_evidence, render_markdown
from hydraulic_mas.ablation.runner import load_records, load_suite, run_sweep


REFERENCE = pathlib.Path(__file__).resolve().parent / "reference" / "validated_topologies.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--problems", default=str(REFERENCE))
    parser.add_argument("--out", default="evidence")
    parser.add_argument("--runs", default=None,
                        help="raw arm records from a previous sweep, as JSONL")
    parser.add_argument("--sweep", action="store_true",
                        help="run the model-backed arms now (needs an API key)")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--arms", default="A0,A0.5,A1,A2")
    args = parser.parse_args(argv)

    suite = load_suite(args.problems)
    records = load_records(args.runs) if args.runs else None

    if args.sweep:
        from hydraulic_mas.ablation.client import build_direct_client, build_full_planner
        from hydraulic_mas.config import Settings
        from hydraulic_mas.models import build_chat_model

        settings = Settings.from_env()
        model = build_chat_model(settings, fast=False)
        # Temperature is the only sampling knob most of these endpoints expose,
        # so it is what the seed varies. Seed 0 stays at the default so one cell
        # of the sweep is the configuration the system actually ships with.
        def factory(seed: int):
            return build_direct_client(model, temperature=None if seed == 0 else 0.2 + 0.05 * seed)

        out = pathlib.Path(args.out) / "runs.jsonl"
        records = run_sweep(
            suite,
            arms=tuple(args.arms.split(",")),
            seeds=tuple(range(args.seeds)),
            direct_client_factory=factory,
            full_planner=build_full_planner(model),
            on_result=lambda result: print(
                f"  {result.arm:5s} {result.problem_id} seed={result.seed} "
                f"-> {result.verdict} ({result.elapsed_s:.1f}s)", flush=True),
            out_path=out)
        print(f"wrote {len(records)} run records to {out}")

    evidence = build_evidence(json.loads(pathlib.Path(args.problems).read_text()),
                              args.out, records)
    print(render_markdown(evidence))
    print(f"\n[tables written to {pathlib.Path(args.out).resolve()}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
