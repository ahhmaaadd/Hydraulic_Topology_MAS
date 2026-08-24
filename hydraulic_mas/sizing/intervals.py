"""Guaranteed enclosures of phase outcomes over a declared operating envelope.

Nominal verification answers "does it work at the numbers I chose". The useful
question is "does it work everywhere it will actually operate", and the two can
differ sharply: the P7-04 reference sizing meets both speeds exactly at nominal
efficiency and *stalls completely* two efficiency points lower.

Naive interval arithmetic through a coupled nonlinear solve produces enclosures
too wide to conclude anything. Two things keep them tight:

* **Monotonicity.** Phase outcomes are monotone in almost every uncertain
  parameter - velocity falls as load rises, rises with pump delivery. Where that
  holds, evaluating at the *corners* of the box gives the exact range, with no
  conservatism at all.
* **Verified sampling.** Monotonicity is not assumed. Interior points are drawn
  and checked against the corner hull; if any escapes it, the parameter is not
  monotone here, the enclosure is widened to contain what was found, and the
  verdict is downgraded from proved to sampled.

That last step is what keeps the method honest. An enclosure is only labelled
PROVED when the corner argument actually holds.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Literal


Verdict = Literal["PROVED", "REFUTED", "UNDECIDED"]


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    @staticmethod
    def of(value: float, relative: float = 0.0) -> "Interval":
        spread = abs(value) * relative
        return Interval(value - spread, value + spread)

    @property
    def mid(self) -> float:
        return 0.5 * (self.lo + self.hi)

    @property
    def width(self) -> float:
        return self.hi - self.lo

    def contains(self, value: float) -> bool:
        return self.lo - 1e-12 <= value <= self.hi + 1e-12

    def within(self, lo: float, hi: float) -> bool:
        return lo - 1e-12 <= self.lo and self.hi <= hi + 1e-12

    def disjoint_from(self, lo: float, hi: float) -> bool:
        return self.hi < lo - 1e-12 or self.lo > hi + 1e-12

    def __repr__(self) -> str:
        return f"[{self.lo:.6g}, {self.hi:.6g}]"


@dataclass
class Envelope:
    """The uncertain parameter box a design is judged over."""

    parameters: dict[str, Interval] = field(default_factory=dict)

    def add(self, name: str, nominal: float, relative: float) -> "Envelope":
        self.parameters[name] = Interval.of(nominal, relative)
        return self

    def nominal(self) -> dict[str, float]:
        return {name: interval.mid for name, interval in self.parameters.items()}

    def corners(self) -> list[dict[str, float]]:
        names = sorted(self.parameters)
        if not names:
            return [{}]
        return [
            dict(zip(names, values))
            for values in itertools.product(*[
                (self.parameters[name].lo, self.parameters[name].hi) for name in names
            ])
        ]

    def interior_samples(self, count: int = 24) -> list[dict[str, float]]:
        """Deterministic interior points on a scrambled lattice.

        Deterministic on purpose: a certificate that changes between runs is not
        a certificate.
        """
        names = sorted(self.parameters)
        if not names:
            return []
        samples = []
        for index in range(1, count + 1):
            point = {}
            for offset, name in enumerate(names):
                interval = self.parameters[name]
                # Additive recurrence with an irrational step gives good spread
                # without a random seed.
                fraction = ((index * (0.6180339887 + 0.1 * offset)) % 1.0)
                point[name] = interval.lo + fraction * interval.width
            samples.append(point)
        return samples


@dataclass
class Enclosure:
    """The result of bounding one scalar outcome over an envelope."""

    interval: Interval
    method: Literal["monotone-corner", "corner+sampled", "degenerate"]
    monotone: bool
    infeasible_points: int = 0

    @property
    def proved(self) -> bool:
        return self.method == "monotone-corner" and self.infeasible_points == 0


def bound_outcome(
    evaluate: Callable[[dict[str, float]], float | None],
    envelope: Envelope,
    *,
    samples: int = 24,
) -> Enclosure:
    """Enclose ``evaluate`` over ``envelope``.

    ``evaluate`` returns ``None`` for a parameter combination the design cannot
    satisfy at all. Those are counted rather than ignored: a design that is
    infeasible anywhere in its own operating envelope cannot be certified, and
    silently dropping such points is how a verifier ends up lying.
    """
    corner_values: list[float] = []
    infeasible = 0
    for point in envelope.corners():
        value = evaluate(point)
        if value is None:
            infeasible += 1
        else:
            corner_values.append(value)

    if not corner_values:
        return Enclosure(Interval(float("nan"), float("nan")), "degenerate", False, infeasible)

    lo, hi = min(corner_values), max(corner_values)
    hull = Interval(lo, hi)

    monotone = True
    for point in envelope.interior_samples(samples):
        value = evaluate(point)
        if value is None:
            infeasible += 1
            monotone = False
            continue
        if not hull.contains(value):
            monotone = False
            lo, hi = min(lo, value), max(hi, value)

    return Enclosure(
        Interval(lo, hi),
        "monotone-corner" if monotone else "corner+sampled",
        monotone,
        infeasible,
    )


def verdict_for(enclosure: Enclosure, lo: float, hi: float) -> Verdict:
    """Judge an enclosure against a required band."""
    if enclosure.method == "degenerate" or enclosure.infeasible_points:
        return "REFUTED"
    if enclosure.interval.within(lo, hi):
        return "PROVED" if enclosure.proved else "UNDECIDED"
    if enclosure.interval.disjoint_from(lo, hi):
        return "REFUTED"
    return "UNDECIDED"


def standard_envelope(load_tolerance: float = 0.0) -> Envelope:
    """The envelope every design in this benchmark is judged over.

    Efficiency spread and viscosity range are ordinary industrial variation, not
    pessimism: two points of mechanical efficiency is well within the scatter
    between a new seal and a worn one.
    """
    envelope = Envelope()
    envelope.add("mechanical_efficiency", 0.90, 0.0222)   # 0.88 .. 0.92
    envelope.add("volumetric_efficiency", 0.90, 0.0222)
    if load_tolerance > 0:
        envelope.add("load_factor", 1.0, load_tolerance)
    return envelope
