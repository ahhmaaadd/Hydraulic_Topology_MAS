from __future__ import annotations

import re
from pathlib import Path


PROBLEM_PATTERN = re.compile(r"(?ms)^\s*(\d+)\.\s+(.*?)(?=^\s*\d+\.\s+|\Z)")


def load_problems(path: str | Path) -> dict[str, str]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    problems = {
        f"P7-{int(number):02d}": body.strip()
        for number, body in PROBLEM_PATTERN.findall(text)
    }
    if not problems:
        raise ValueError(f"No numbered problems found in {source}")
    return problems


def get_problem(path: str | Path, problem_id: str) -> str:
    problems = load_problems(path)
    normalized = problem_id.upper()
    if normalized not in problems:
        raise KeyError(f"Unknown problem {problem_id!r}; available: {', '.join(problems)}")
    return problems[normalized]

