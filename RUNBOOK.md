# The five arms, and how to run each

The ablation exists to answer one question: **which part of the system is actually doing
the work?** Each arm removes one thing and keeps everything else. Every arm is judged by
the *same* verifier on the *same* acceptance criteria — including the arms that never see
the verifier while they are designing. That is what makes a difference between arms a
difference in the designs rather than a difference in marking.

---

## What each arm is

| Arm | Neural | Arithmetic tools | Verifier | Repair | What it removes |
| --- | :---: | :---: | :---: | :---: | --- |
| **A0** | ✓ | ✗ | ✗ | ✗ | everything except the model |
| **A0.5** | ✓ | ✓ | ✗ | ✗ | the verifier and the repair loop |
| **A1** | ✓ | ✓ | ✓ | ✓ | nothing — this is the shipped system |
| **A2** | ✗ | ✓ | ✓ | ✓ | the model |
| **A-rand** | ✗ | ✓ | ✓ | ✗ | the *meaning* of the model's choices |

### A0 — the language model on its own

The model is given the problem, the validated topology, and the standard series and design
basis **as text**: the ISO 3320 bores, the ISO 4395 rods, the displacement series,
η_m = 0.90, and the force-balance identity. It has no calculator, no tools, no verifier and
no second chance. It writes down the finished design — bore, rod, displacement, relief
setting, motor — in one shot.

It is also asked what it believes its design will achieve, per phase, *before* anything
checks it. Nothing is built from that answer. It exists so the same verifier that judges
the design can also measure how far the model's own arithmetic was out.

**What it tests:** whether an LLM can select correct sizes from parametric memory alone.
Because the catalog is in the prompt, a failure is never for want of information.

### A0.5 — the model with a calculator

Identical to A0, plus the arithmetic tools: `area_for_force`, `choose_bore`, `choose_rod`,
`flow_for_speed`, `choose_pump`, `choose_motor`, `choose_valve`. Still no verifier, still no
repair round, still one shot.

**What it tests:** it separates *"cannot calculate"* from *"cannot check its own work"*.
If A0.5 fixes most of A0's failures, the problem was arithmetic. If it does not, the
problem was judgement, and a calculator was never going to help.

The verifier tool `evaluate_sizing_policy` is deliberately withheld — that single tool is
the difference between A0.5 and A1, and a test asserts it never leaks across.

### A1 — the full system

The shipped pipeline. The model chooses only *policy* — which phase governs a dimension,
what pressure to design against, whether a rod is chosen for force or for area ratio, how
much relief margin to carry, how far to aim off a one-sided speed bound — and tools compute
every number from that policy. It generates two or three candidate strategies, tests them
with the verifier, and a deterministic score selects between them. If the certificate is
not clean, a repair round revises the policy.

### A2 — the deterministic planner

No model anywhere. The same tools and the same verifier, driven by a fixed engineering
policy with one searched parameter (the relief setting, over a coarse fixed grid). This is
the control A1 has to beat.

### A-rand — the null control

**Added after the v0.7.0 traces showed A1 and A2 producing identical designs on five of
seven problems.** If the tools compute every number, the catalog is a short preferred
series, and the verifier scores the outcome, then how much of the result is the model's
judgement and how much is the scaffolding?

A-rand answers that by sampling a policy *uniformly at random* from the same schema the LLM
fills in, then running it through the same tools and the same verifier. Three candidates
are drawn per run and the best is kept, matching A1's candidate count so the control is not
handicapped.

It is a two-sided instrument. If random policies certify as often as reasoned ones, the
model contributed nothing and the paper must say so. They do not — see below — which is
what makes the rest of the ablation worth running.

---

## Should you run A0 and A0.5?

**Yes, and it is the next thing to do.** Right now no arm is baselined. Every claim about
what the model contributes rests on comparisons among A1, A2 and A-rand — and A-rand is the
only one that was ever a genuine control.

Concretely: the paper's stated thesis is *"an LLM cannot select correct sizes without
calculation."* There is currently **no data at all** on that. A0 is the experiment that
tests it. It costs roughly four hours of API time for 10 seeds × 7 problems × 2 arms.

Two things to expect, both of which are publishable:

- **A0 may do better than you think.** The seven problems come from one textbook family and
  are plausibly in pretraining, so the model may reproduce the book's answers from memory.
  That is why the sweep should be run *twice* — once on the originals and once on
  numeral-perturbed, unit-renamed variants. The gap between those two is a contamination
  measurement, and it is more interesting than the pass rate.
- **A0 may fail in a specific, characterisable way.** The harness records not just pass/fail
  but the *signed error* of the model's own predictions against what the verifier computes.
  "The LLM was wrong" is weak. "The LLM's chosen bore under-delivers force by a median 14 %
  with a long tail, while the same model behind tools is exact" is a result.

---

## How to run each

### Everything that needs no model

```bash
python -m pytest                      # 388 offline tests, no network, no API key
python run_evidence.py                # coverage, seeded defects, detection curve, transient
```

### A2 — deterministic, no key needed

```bash
python -c "
from hydraulic_mas.ablation import load_suite, run_cell
suite = load_suite('reference/validated_topologies.json')
for pid in sorted(suite.problems):
    r = run_cell('A2', suite, pid, 0)
    print(pid, r.verdict, r.counts, 'oversizing', r.oversizing_index)
"
```

Or regenerate the reference document and certificates in one step:

```bash
python reference/generate_report.py
```

### A-rand — the null control, no key needed

```bash
python -c "
import collections
from hydraulic_mas.ablation import load_suite, run_cell
suite = load_suite('reference/validated_topologies.json')
for pid in sorted(suite.problems):
    tally = collections.Counter(run_cell('A-rand', suite, pid, s).verdict for s in range(10))
    print(pid, dict(tally))
"
```

### A0, A0.5 and A1 — these need an API key

```bash
export OPENAI_API_KEY=...             # or the Azure / gateway variables in hydraulic_mas/config.py

# the whole sweep: every arm, 10 seeds, writes raw records as it goes
python run_evidence.py --sweep --seeds 10

# or a subset
python run_evidence.py --sweep --seeds 10 --arms A0,A0.5

# re-analyse later without touching the model again
python run_evidence.py --runs evidence/runs.jsonl
```

Raw per-run records land in `evidence/runs.jsonl`, one JSON object per
(arm, problem, seed) cell, written as each completes — so an exception at cell 340 does not
cost you the first 339. Every table is computed from that file afterwards, so a summary can
never disagree with the data it came from.

One arm alone, if you want to watch it:

```bash
python -c "
from hydraulic_mas.ablation import load_suite, run_cell
from hydraulic_mas.ablation.client import build_direct_client
from hydraulic_mas.config import Settings
from hydraulic_mas.models import build_chat_model

model = build_chat_model(Settings.from_env(), fast=False)
suite = load_suite('reference/validated_topologies.json')
r = run_cell('A0', suite, 'P7-01', 0, direct_client_factory=lambda s: build_direct_client(model))
print(r.verdict, r.counts, r.catalog_violations, r.errors)
"
```

---

## Where the arms stand today

| Arm | Result | Notes |
| --- | --- | --- |
| A0 | **not run** | the paper's central claim has no data |
| A0.5 | **not run** | separates "cannot calculate" from "cannot self-check" |
| A1 | 7/7 PROVED on v0.7.0 | but two of those were oversized designs the ceiling now rejects |
| A2 | **5/7 PROVED**, nothing refuted | P7-04 and P7-07 remain UNDECIDED for substantive reasons |
| A-rand | **17/70 = 24 % PROVED** | 0/10 on P7-04, P7-05 and P7-07 |

The A-rand number is the important one. A policy drawn at random certifies about a quarter
of the time and never on the three problems with tight pressure ceilings, while the fixed
engineering policy certifies 71 % of the time. **The scaffolding alone does not carry the
result** — which is what makes the A0 / A0.5 comparison worth the API budget.

---

## What changed in v0.7.1

Four defects, all of the same shape: something the system already knew never reached the
check that needed it.

1. **The stated load variation never reached the force criterion.** P7-01 declares ±15 %
   cutting-load variation; the envelope applied it, the force check did not. Reported margin
   on the governing phase was 1.20× where the honest figure is 1.04×. Force targets are now
   compiled against the top of the stated band, and the certificate prints the derivation.
   Root cause: two implementations of the same rule, one in the planner and one implied by
   the envelope. There is now exactly one, and a test asserts the second does not come back.
2. **A generic audit for that defect class.** A test now collapses each envelope parameter
   in turn and asserts it measurably moves at least one enclosure. A parameter that changes
   nothing is either unnecessary or being dropped before it reaches a check.
3. **Oversizing became a constraint instead of a price.** Priced at −2 against +10 per
   proved criterion, brute force won: the v0.7.0 LLM run designed a 12 kN clamp against
   5 bar, forcing a 200 mm bore — 6.3× the required piston area — and certified everything.
   Designs above an oversizing index of 1.75 are now refused outright, on every arm. The
   regression test replays that exact design from its trace.
4. **The repair path never marked its own winner as selected.** P7-07's final trace reported
   the adopted design with `selected: False`.
