from __future__ import annotations

from pathlib import Path

from hydraulic_mas.problems import load_problems


def test_loads_all_seven_numbered_problems() -> None:
    path = Path(__file__).resolve().parents[1] / "problems" / "Problems_7.txt"
    problems = load_problems(path)
    assert list(problems) == [f"P7-{number:02d}" for number in range(1, 8)]
    assert "horizontal machine-tool slide" in problems["P7-01"]
    assert "no electrical pressure switch" in problems["P7-07"].lower()


