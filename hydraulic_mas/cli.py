from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from .config import Settings
from .graph import build_graph, summarize_research_audit
from .problems import load_problems
from .terminal import TerminalReporter


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROBLEMS = PROJECT_ROOT / "problems" / "Problems_7.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research, design, validate, and print a hydraulic circuit topology with LangGraph agents."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--problem", metavar="P7-01", help="Run one id from the numbered problems file.")
    source.add_argument("--all", action="store_true", help="Run every problem in the numbered problems file.")
    source.add_argument("--text", help="Run one custom problem statement.")
    parser.add_argument("--problems-file", type=Path, default=DEFAULT_PROBLEMS)
    parser.add_argument("--list-problems", action="store_true", help="List ids and exit; no API keys required.")
    parser.add_argument("--model", help="Design/review model or Azure deployment name.")
    parser.add_argument("--fast-model", help="Extraction/distillation model or Azure deployment name.")
    parser.add_argument("--max-research-rounds", type=int)
    parser.add_argument("--max-searches", type=int)
    parser.add_argument("--max-topology-rounds", type=int)
    parser.add_argument("--non-interactive", action="store_true", help="Do not pause for requirement answers.")
    parser.add_argument("--compact", action="store_true", help="Print summaries instead of every intermediate JSON field.")
    parser.add_argument("--no-save", action="store_true", help="Do not save the final JSON under runs/.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "runs")
    return parser


def _interrupt_payload(update: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = update.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"kind": "unknown", "value": value}


def _initial_state(problem_id: str, problem: str, settings: Settings, *, interactive: bool) -> dict[str, Any]:
    return {
        "problem_id": problem_id,
        "user_query": problem,
        "interactive": interactive,
        "clarification_answers": [],
        "requirements_round": 0,
        "requirements_repair_attempted": False,
        "max_requirements_rounds": settings.max_requirements_rounds,
        "research_findings": [],
        "research_search_log": [],
        "research_query_history": [],
        "searches_used": 0,
        "research_round": 0,
        "research_stalled_rounds": 0,
        "targeted_research_attempts": 0,
        "max_research_rounds": settings.max_research_rounds,
        "max_searches": settings.max_searches,
        "topology_round": 0,
        "max_topology_rounds": settings.max_topology_rounds,
        "repair_history": [],
    }


def run_problem(
    graph: Any,
    reporter: TerminalReporter,
    settings: Settings,
    problem_id: str,
    problem: str,
    *,
    non_interactive: bool,
    save_path: Path | None,
) -> int:
    reporter.run_header(problem_id, problem, settings)
    config = {
        "configurable": {"thread_id": f"{problem_id}-{uuid4()}"},
        "recursion_limit": 200,
    }
    graph_input: Any = _initial_state(problem_id, problem, settings, interactive=not non_interactive)

    while True:
        interruption: dict[str, Any] | None = None
        for update in graph.stream(graph_input, config=config, stream_mode="updates"):
            payload = _interrupt_payload(update)
            if payload is not None:
                interruption = payload
            else:
                reporter.node_update(update)
        if interruption is None:
            break
        response = reporter.clarification(interruption, non_interactive=non_interactive)
        graph_input = Command(resume=response)

    state = graph.get_state(config).values
    if state.get("failure"):
        reporter.failure(state["failure"])
        if save_path is not None:
            record = {
                "problem_id": problem_id,
                "status": "failed",
                "failure": state["failure"],
                "research_audit": summarize_research_audit(state),
            }
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            reporter.saved(str(save_path.resolve()))
        return 2
    output = state.get("final_output")
    if not output:
        reporter.failure({"stage": "unknown", "message": "Graph completed without final_output."})
        return 2
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        reporter.saved(str(save_path.resolve()))
    return 0 if output.get("status") != "unresolved" else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problems = load_problems(args.problems_file)
    if args.list_problems:
        for problem_id, text in problems.items():
            print(f"{problem_id}: {text[:120]}{'...' if len(text) > 120 else ''}")
        return 0

    overrides = {
        "model": args.model,
        "fast_model": args.fast_model,
        "max_research_rounds": args.max_research_rounds,
        "max_searches": args.max_searches,
        "max_topology_rounds": args.max_topology_rounds,
    }
    settings = Settings.from_env(**overrides)
    try:
        settings.validate_live_run()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        print("Copy .env.example to .env, add the required keys, and run again.")
        return 2

    if args.text:
        selected = {"CUSTOM": args.text.strip()}
    elif args.all:
        selected = problems
    else:
        problem_id = (args.problem or "P7-01").upper()
        if problem_id not in problems:
            print(f"Unknown problem {problem_id!r}. Available: {', '.join(problems)}")
            return 2
        selected = {problem_id: problems[problem_id]}

    graph = build_graph(settings=settings)
    reporter = TerminalReporter(compact=args.compact)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    status = 0
    for problem_id, problem in selected.items():
        save_path = None if args.no_save else args.output_dir / run_stamp / f"{problem_id}.json"
        status = max(
            status,
            run_problem(
                graph,
                reporter,
                settings,
                problem_id,
                problem,
                non_interactive=args.non_interactive,
                save_path=save_path,
            ),
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
