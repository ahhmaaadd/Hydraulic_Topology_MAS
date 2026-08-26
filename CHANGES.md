# v0.7.0 — soundness, and the experiment the paper actually needs

Two kinds of change. The first fixes things that were wrong inside the
contribution the paper is about. The second builds the evidence the paper's
central claim rests on and did not have.

---

## 1. The certificates were only two-thirds certificates

### Force and hold checks were nominal point evaluations

`velocity` criteria went through the interval machinery, as advertised. `force`,
`hold` and `pressure_ceiling` criteria did not: each was a single evaluation at
nominal efficiency returning PROVED or REFUTED with **no UNDECIDED band at all**.
They were reported in the same table, with the same label, as the enclosures. Of
44 checks across the seven problems, only 15 — 34 % — used the method the work
claims as its contribution.

It was not a cosmetic gap. The force check read the exhaust pressure off the
*solved* operating point, and on any pressure-limited phase the supply pressure
has by definition already risen until force equals load. So the formula recovered
the load and handed the requirement back as though it were a capability. Two
designs were certified on that basis with margins of **0.21 N on 2.5 kN** and
**10.2 N on 12.5 kN**, and their enclosures came out `[2.5, 2.5]` and
`[12.51, 12.51]` — the efficiency spread cancelling exactly, which is the
signature of a circular check.

Force is now a stall calculation — supply ceiling against the areas, with only a
genuinely holding valve loading the exhaust side — bounded over the same envelope
as everything else. Every force criterion now has a real interval and the
thinnest surviving margin is 9.7 %.

**Every check is now an interval method. 44 of 44.**

Fixing it exposed a second error in the same place: a pressure-reducing valve on
a *return* line was being counted as back pressure. That took 9 kN off P7-07's
clamp and refuted a design that is correct.

### A relief set at the ceiling cannot prove the ceiling

Three `pressure_ceiling` criteria certified with margin exactly `0.0`, because the
planner set the relief *to* the ceiling and the verifier then checked the relief
against that same ceiling. It restated a choice rather than testing it. Such a
criterion is now UNDECIDED, with the reason recorded on it.

### `RELIEF_MARGIN_THIN` now downgrades the verdict

It was a warning that left the overall verdict PROVED — a design sitting on a
boundary being certified as clear of it.

The same check was also firing wrongly. A *pressure-limited* phase sits at the
relief by design: the surplus of a fixed pump over a metered feed has to cross
the valve. Its pressure equals the setting wherever the setting is put, so the
margin was always zero and always meaningless. The check now applies only to
phases the relief is meant to stay shut for.

### `velocity_range` was unprovable by construction

An adjustable feed such as "0.5 .. 1 m/min" was **hardcoded to UNDECIDED** —
a fixed throttle sits at one point, so comparing it to a whole span could never
succeed. It cost P7-01 its verdict on *both* arms, for a reason that had nothing
to do with either arm's sizing.

The requirement asks whether the operator can reach both ends. Throttling down is
always available, so the binding question is the fast end: with the control backed
fully off, does the circuit still deliver the top of the span everywhere in the
envelope? That is now the check, and it still refutes a circuit that is too slow.

### Coverage is generated instead of unstated

"6 of 7 PROVED" read as "six designs fully verified". It meant six designs
verified against the checkable subset, and nothing said how large that subset was.

Every stated acceptance criterion is now classified exactly once and the partition
is carried on the certificate. Across the benchmark: **73 stated, 44 answered
(60 %)**. The 29 that are not break down as 19 geometric choices (a stroke the
design fixes, with nothing to solve for), 4 settled at the topology stage, 2
non-quantitative, 2 outside quasi-static scope, and 2 restated by another
criterion. **None is a quantitative in-scope statement simply left unchecked** —
and a test now fails if one appears. The terminal prints the coverage line beside
every verdict.

### A crash on total infeasibility

`_certify_power` took `max()` over an empty sequence when *every* phase was
infeasible. The deterministic planner never produces such a design, so it had
never been hit — but it is the normal case for an arm with no verifier, which is
how the ablation found it.

---

## 2. The deterministic arm no longer loses on a technicality

The baseline set the relief by a fixed rule, `min(ceiling, worst × 1.12)`. When
the worst working pressure came within that margin of the ceiling, the rule
clamped the relief *to* the ceiling — the circular case above, and a setting with
no band for the valve to regulate in.

It was also the wrong control to freeze. Where a load-actuated speed ratio is
wanted, the attainable ratio is governed almost entirely by how close the relief
sits to the slow phase's stall pressure. A baseline forbidden from moving the
relief cannot reach requirements that are perfectly reachable, and an ablation
against it measures the freedom rather than the intelligence.

So the baseline searches too: a coarse fixed grid over the same one-dimensional
policy, no gradient, every point scored by the same verifier. **P7-04 goes from
REFUTED to UNDECIDED and P7-05 from UNDECIDED to PROVED.** The deterministic arm
now stands at 5 of 7 proved with nothing refuted, which is a baseline worth
beating rather than a strawman.

---

## 3. The ablation the central claim needs

There was no arm that sizes without tools. `grep` for `baseline`, `ablation` or
`A0` returned three comments, one of which mislabelled the *deterministic* planner
as "the A0 control arm" — that is symbolic-without-neural, the opposite of what
the claim requires.

`hydraulic_mas/ablation/` now defines four conditions:

| Arm | Neural | Arithmetic tools | Verifier | Repair |
| --- | --- | --- | --- | --- |
| A0 | ✓ | ✗ | ✗ | ✗ |
| A0.5 | ✓ | ✓ | ✗ | ✗ |
| A1 | ✓ | ✓ | ✓ | ✓ |
| A2 | ✗ | ✓ | ✓ | ✓ |

Every arm is judged by the *same* verifier on the *same* contract, including the
two that never see it while designing. A0 and A0.5 receive the standard series and
the design basis as text, so a failure is never for want of information.

**Nothing is corrected on the way in.** A bore off the preferred series is
installed as given and reported; a relief above the ceiling is installed and
refuted. Rounding a number to the nearest legal value would repair exactly the
errors the experiment exists to count.

### Measuring how wrong, not just whether

Each direct arm states what it believes its design will achieve, before anything
checks it. That yields two independent quantities:

- **prediction error** — predicted against actual: the model's arithmetic alone;
- **requirement error** — actual against required: design quality.

An arm can be fine on one and bad on the other, and a pass rate collapses them.
`unreachable_rate` sits beside both, because a design so far out that no phase
solves contributes no rows to either distribution — without that count the error
columns are survivorship-biased in favour of catastrophic failure.

### Testable without a model

The harness is exercised offline against stubs. A certified design fed back
through the direct path reproduces its own certificate on all seven problems, so
a difference between arms is a difference in the designs and not an artefact of
installation. Deliberately damaged designs are refuted; a design whose numbers are
right and whose stated arithmetic is wrong moves the prediction error and nothing
else.

`run_evidence.py --sweep` runs the live arms; without it every offline table is
still produced.

---

## 4. E2 — seeded defects, with a false-positive rate and a curve

E1 (rediscovering the audit's defects) is strong but small and hand-selected. E2
injects defects mechanically at known magnitudes into designs that certify
cleanly, and includes *null* injections that change nothing.

- **false-positive rate 0 %** over 10 null injections
- subtle defects: 15/15 detected; gross: 15/15

100 % across the board says the injections were coarse, not that the detector is
perfect, so a graded sweep finds where detection actually turns on:

| Magnitude | relief low | control shut |
| ---: | ---: | ---: |
| 0.5 % | 0 % | 0 % |
| 1 % | 40 % | 0 % |
| 2 % | 60 % | 0 % |
| 3 % | 80 % | 100 % |
| 5 % | 100 % | 100 % |

Both families cross 50 % within a factor of about three of the ±2.22 % envelope
half-width. That is what the method predicts — the enclosure widens by the
envelope, so a defect must exceed it before the interval can clear the bound — and
it means sensitivity is a stated modelling choice rather than an accident.

---

## 5. The transient cross-check

A numerical integration of the same circuits — piston mass under the same force
balance, fluid compliance charging the chambers, the relief opening and reseating
— to bound what the quasi-static assumption omits.

Across all 22 motion phases:

- **settled velocity agrees with the quasi-static solve to a median of 0.06 % and
  a worst case of 3.24 %**;
- acceleration occupies a median 5 % of the stroke.

Two things had to be got right for that to mean anything.

**Settling is detected from the acceleration, never from proximity to the answer.**
The first version stopped when the integration came close to the quasi-static
velocity, which would have guaranteed agreement and proved nothing. A test now
inspects the loop to keep it that way.

**Both invented parameters are swept.** Line length and moving mass are not in the
briefs. The settling *time* is roughly proportional to the assumed mass; the
settled *velocity* moves by under 2 % across a 20× mass range and a 4× line-length
range. So the agreement claim stands on its own, and the transient-fraction
finding is reported hedged.

The study also found something the verifier could not see: **P7-07's clamp
accelerates for longer than its whole 50 mm stroke**, so its certified velocity is
one the clamp never reaches. Three more phases are mostly transient.

A cheap closed-form proxy for this was tried and **under-predicted the settling
distance by two orders of magnitude**, because what dominates is the asymptotic
approach rather than chamber charging or mass acceleration. It was removed rather
than tuned into agreement — tuning a two-parameter estimate against the very
integration it stands in for produces a number that fits the benchmark and nothing
else. The real integration is available as layer V5 behind `transient_check=True`,
off by default because the quasi-static battery exists precisely so it need not
run.

---

## 6. Evidence generation

`run_evidence.py` regenerates every table from data. `reference/SIZED_SOLUTIONS.md`
is now generated too — it had drifted a full release behind the code, quoting
verdicts and methods the system no longer produced.

---

## Results

| Problem | v0.6.0 deterministic | v0.7.0 deterministic |
| --- | --- | --- |
| P7-01 | UNDECIDED | **PROVED** |
| P7-02 | PROVED | PROVED |
| P7-03 | PROVED | PROVED |
| P7-04 | REFUTED | **UNDECIDED** |
| P7-05 | PROVED | PROVED |
| P7-06 | PROVED | PROVED |
| P7-07 | UNDECIDED | UNDECIDED |
| | 4/7 | **5/7, nothing refuted** |

Both remaining UNDECIDED results are substantive, not artefacts. P7-04's feed
velocity encloses widely because the operating point sits close to the relief.
P7-07's working advance overshoots a ±10 % band by 1.2 % because the phase is fed
straight from the pump and the attainable speed steps with the displacement
series — the planner now says so, and says that a speed control would close it
where a larger cylinder would not.

376 tests, all passing.
