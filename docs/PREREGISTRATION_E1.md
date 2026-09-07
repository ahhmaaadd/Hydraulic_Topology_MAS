# Pre-registration — E1: Does a verifier substitute for tools?

| | |
| --- | --- |
| **Study ID** | E1 |
| **Title** | Isolating the contribution of arithmetic tools from that of a deterministic verifier in LLM-driven hydraulic circuit sizing |
| **System** | `Hydraulic_Topology_MAS` v0.7.2 (to be tagged before the first run) |
| **Registered** | 2026-08-31 |
| **Status** | Registered — **no E1 data collected** |
| **Author** | Muhammad Ahmed |

**This document is written before the new arm is run and must not be edited after the
first run completes.** Changes go in [§12 Deviations](#12-deviations), appended with a date
and a reason, never by rewriting the text above them.

---

## 1. Motivation

Prior work on this system compared four arms: an LLM alone (A0), an LLM with arithmetic
tools (A0.5), the full system with tools, a deterministic verifier and a repair loop (A1),
and a fixed engineering policy with no LLM (A2).

That grid cannot answer the question it was built for. **A0 and A1 differ in two ways at
once** — tool access *and* verifier-plus-repair — so the observed gap is unattributable.
The design is confounded, and no re-analysis of the existing runs can un-confound it.

E1 adds the missing cell. With `A0-V` (verifier and repair, but no arithmetic tools) the
design becomes a 2×2 factorial, and the tools manipulation can be read with the verifier
held constant.

|  | **no verifier** | **verifier + repair** |
| --- | --- | --- |
| **no tools** | A0 *(collected)* | **A0-V — this study** |
| **tools** | A0.5 *(collected)* | A1 *(collected)* |

---

## 2. What is already known

**This is a data-dependent pre-registration.** Three of the four cells are already
collected and have been inspected in detail. This document therefore binds only the
predictions concerning `A0-V` and the comparisons that involve it. Everything below was
observed *before* registration and is disclosed so that no analysis presented later can be
mistaken for a blind prediction.

Observed, 217 runs, v0.7.1:

| Arm | Runs | PROVED | Per problem P7-01 … P7-07 |
| --- | ---: | ---: | --- |
| A0 | 70 | 30.0 % | 0.9 · 0.4 · 0.8 · 0.0 · 0.0 · 0.0 · 0.0 |
| A0.5 | 70 | 34.3 % | 1.0 · 0.9 · 0.5 · 0.0 · 0.0 · 0.0 · 0.0 |
| A1 | 70 | 68.6 % as scored · **100 %** with the oversizing ceiling removed | 1.0 across all seven |
| A2 | 7 | 71.4 % | fails P7-04 and P7-07 on physics |

Also already established:

- **A0 vs A0.5 is null.** Exact McNemar on 70 paired cells, discordant 4 / 7, *p* = 0.549.
  Arithmetic tools alone do not change the outcome.
- **The toolless cliff is sharp.** A0 and A0.5 together score **0 of 80** runs on P7-04,
  P7-05, P7-06 and P7-07, while reaching 0.9 and 1.0 on P7-01.
- **The model computes correctly and specifies wrongly.** A0's prediction of its own
  design's performance is within 1 % on 73 % of criteria, while its distance from the
  stated requirement has a median of 13.1 %.
- **All 21 of A1's refutations were the oversizing ceiling firing alone** — zero refuted
  criteria, zero findings. That ceiling has been removed from the certificate path (§4.4).
- **A1 certified 440 of 440 criteria** under a repair loop that iterates until the
  certificate is clean.

The pattern that motivates H1 — that the model's arithmetic is sound while its
specification is not — is an observation, not a prediction. The prediction under test is
that supplying a verifier repairs the specification failure that a calculator cannot.

---

## 3. Hypotheses

Stated as directional, falsifiable predictions. Each names the comparison that tests it
and the outcome that would refute it.

**H1 — the verifier does what the calculator cannot.**
`A0-V` proves a higher fraction of runs than `A0.5`, by at least 20 percentage points.
*Refuted if* the A0.5 → A0-V difference is under 10 points, or its confidence interval
spans zero.

**H2 — tools still add something on top of the verifier.**
`A1` proves a higher fraction than `A0-V`.
*Refuted if* `A0-V` matches or exceeds `A1`. That refutation is an informative result, not
a failure: it would show tool access is unnecessary once feedback is available.

**H3 — the verifier's contribution exceeds the calculator's.**
(A0-V − A0) > (A0.5 − A0), evaluated as a paired difference across problems.
*Refuted if* the two increments are indistinguishable.

**H4 — tools matter where the physics is coupled.**
Any A1-over-A0-V advantage concentrates on P7-04, P7-05, P7-06 and P7-07 — the four
problems on which both toolless arms currently score 0/80 — rather than being spread
evenly.
*Refuted if* the advantage is uniform across problems, or concentrated on P7-01 … P7-03.

**H5 — the loop reasons rather than thrashes.**
`A0-V`'s self-prediction error decreases monotonically in expectation across repair
rounds.
*Refuted if* the error is flat or rises while the verdict improves — which would indicate
resampling until something passes rather than reading the certificate.

**H6 — the loop alone is not sufficient.**
A random policy through the same verifier and the same repair budget does not reach A1's
proved rate.
*Refuted if* it does, in which case neither the tools nor the model carry the result and
the paper's claim reduces to the verifier and the search loop.

---

## 4. Design

### 4.1 Factors

Two binary factors, fully crossed.

- **Tools** — access to `area_for_force`, `choose_bore`, `choose_rod`, `flow_for_speed`,
  `choose_pump`, `choose_motor`, `choose_valve`, `select_tube`, `select_reservoir`.
- **Verifier** — access to the certificate and up to three repair rounds.

`evaluate_sizing_policy` is the tool that constitutes verifier access. A regression test
asserts it never leaks into a no-verifier arm.

### 4.2 The new arm

`A0-V` receives the problem, the validated topology, and the standard series and design
basis as text — identical to A0's prompt. It emits a complete design directly
(`DirectSizing`: bore, rod, displacement, relief setting, motor, reservoir, valve size,
component settings). It computes every number itself. It then receives the certificate and
may revise, up to three times.

### 4.3 The certificate leak, and why it is deliberate

The certificate contains tool-computed quantities: achieved intervals, operating points,
margins. Handing it to a toolless arm therefore leaks computation, and no configuration
avoids this — the feedback *is* tool output.

Rather than attempt a partial disclosure that would be arbitrary and hard to defend,
**`A0-V` receives the full certificate, identical to what A1's repair loop sees.** This
makes `A0-V` a deliberate **upper bound** on toolless performance, and it makes the study
interpretable in both directions:

- if `A0-V` still loses to `A1`, the tools result is strong, because the toolless arm was
  given every advantage including leaked computation and lost anyway;
- if `A0-V` ties or wins, tool access is unnecessary once sound feedback is available.

This is recorded here so that the leak is read as a design decision rather than as an
oversight found later.

### 4.4 Held constant

| Held constant | Value | Why |
| --- | --- | --- |
| Repair budget | exactly 3 rounds, all verifier arms | otherwise the comparison measures budget |
| Candidates per run | 2–3, proposed then scored, all arms | A1 would otherwise hold a search advantage unrelated to tools |
| Verifier build | one tagged commit, all arms | a moving oracle invalidates every comparison |
| Problems | the same 7, unmodified | — |
| Seeds | 0 … 9 | matches the three collected cells; keeps the factorial balanced |
| Model, temperature, prompt scaffold | identical except the tool list and the repair channel | the manipulation must be the only difference |

### 4.5 Verifier configuration

E1 runs against a verifier that differs from v0.7.1 in two respects, both fixed before
any E1 run and both applied to **every** arm including re-scored historical runs:

1. **The 1.75 oversizing ceiling is removed from the certificate path.** It produced 100 %
   of A1's refutations, and its value is a hand-set constant. The oversizing index is
   retained as a descriptive statistic only, never as a criterion.
2. **A resolution floor is added.** Any criterion whose certified margin falls below the
   verifier's measured seeded-defect sensitivity returns UNDECIDED rather than PROVED. The
   floor is set from the detection curve before E1 begins and is not adjusted afterwards.

Historical A0, A0.5 and A1 runs are re-scored under this configuration from stored records
before any comparison is made. **If re-scoring changes the historical proved rates, the
re-scored values are the ones used throughout, and both are reported.**

### 4.6 Controls

- **A-rand-V** — a policy sampled uniformly from the same schema, through the same
  verifier and the same 3-round budget. No API cost. Tests H6.
- **A1-capped** — A1 with the repair loop capped at one iteration, giving the
  un-optimised A1 number that the current 440/440 does not provide.

---

## 5. Materials

- **Benchmark** — P7-01 … P7-07, unmodified, from `reference/validated_topologies.json`.
  All seven originate from one textbook chapter family and are plausibly present in
  pretraining data. See [§11 Limitations](#11-limitations).
- **Topologies** — the validated topology is supplied to every arm. E1 tests sizing only;
  topology synthesis is not under test.
- **Model** — a single model and temperature, recorded in `evidence/E1_manifest.json` at
  run time, unchanged for the duration.
- **Code** — one tagged commit. The tag is recorded in the manifest before the first run.

---

## 6. Procedure

1. Tag the verifier build. Record the commit, model identifier and prompt hashes in
   `evidence/E1_manifest.json`.
2. Re-score stored A0, A0.5, A1 and A2 records under the E1 verifier configuration.
   Report re-scored and original values side by side.
3. Run `A-rand-V` (free) and `A1-capped`.
4. Run `A0-V`: 7 problems × 10 seeds = 70 runs, written to `evidence/E1_runs.jsonl` one
   record per cell as it completes.
5. Run every analysis in §8 from `evidence/E1_runs.jsonl` alone, so that no reported table
   can disagree with the data it came from.

Runs that raise an exception are recorded with `verdict: ERROR` and **counted as
not-proved** in the primary analysis. They are reported separately and never silently
dropped.

---

## 7. Outcomes

### 7.1 Primary

**Run-level certification.** A run is PROVED when every criterion in its certificate is
PROVED and no install fault is recorded. Binary, one value per (arm, problem, seed).

### 7.2 Secondary

- **Criterion-level certification** — binary per criterion. Roughly 440 observations per
  arm, and the only outcome in this study with real statistical power.
- **Rounds to clean** — repair rounds consumed before the first clean certificate, or
  censored at 3.
- **Per-problem proved fraction** — 7 values per arm.

### 7.3 Exploratory

Declared exploratory in advance; reported without inferential claims.

- Self-prediction error by repair round (H5).
- Requirement error distribution, split by direction — shortfall against the requirement
  versus surplus beyond it. The v0.7.1 metric conflated the two and is not interpretable;
  E1 reports them separately.
- Oversizing index distribution, descriptive only.
- Catalogue violation rate.
- Wall-clock and token cost per arm.

---

## 8. Analysis plan

All tests two-sided at α = 0.05.

### 8.1 Primary test

**Exact McNemar, `A0.5` vs `A0-V`**, paired on (problem, seed), 70 pairs. This is the
head-to-head of a calculator against a verifier and is the study's single primary
comparison.

### 8.2 Secondary tests

- Exact McNemar, `A0` vs `A0-V` — the verifier's effect with no tools present.
- Exact McNemar, `A0-V` vs `A1` — what tools add once a verifier is present.

The primary and the two secondary tests form a family of three. **Holm–Bonferroni
correction across all three.** Corrected and uncorrected *p* values are both reported.

### 8.3 The factorial

Stratified permutation test on the tools × verifier interaction: shuffle arm labels
**within problem**, 10,000 permutations, test statistic the difference of differences in
proved rate. Chosen over a parametric interaction test because 7 clusters will not support
the asymptotics.

### 8.4 Criterion level

Mixed-effects logistic regression:

```
proved ~ tools * verifier + (1 | problem) + (1 | criterion_id)
```

Random intercepts absorb the clustering that the run-level tests cannot. Convergence
failures are reported; if the full model does not converge, the fallback is a
random-intercept-for-problem model only, and that substitution is disclosed.

### 8.5 Effect sizes

Paired risk difference per comparison, with a bias-corrected accelerated bootstrap over
**problems** (7 clusters, 10,000 resamples). The interval will be wide. It is reported
because a wide honest interval is more use than a point estimate that implies precision
the design cannot deliver.

### 8.6 Descriptive, no test

Convergence curves — fraction certified against repair round, one line per verifier arm.
Expected to carry more information than any *p* value this design can produce.

---

## 9. Inference criteria

What each outcome licenses, decided now rather than after seeing it.

| Result | Reading |
| --- | --- |
| `A0-V` ≫ `A0.5`, `A1` ≳ `A0-V` | **Predicted.** The verifier supplies what the calculator cannot; tools add reliability on top. H1, H2 supported. |
| `A0-V` ≈ `A1` | Tool access is unnecessary given sound feedback. The verifier is the contribution; the tools section becomes an engineering convenience argument. H2 refuted, and reported as such. |
| `A0-V` ≈ `A0.5` | The verifier does not rescue a toolless proposer. The tools result stands, and H1 is refuted. |
| `A-rand-V` ≈ `A1` | Neither the model nor the tools carry the result. The paper's claim reduces to the verifier plus the search loop. Reported prominently, not buried. |
| `A0-V` advantage spread evenly across problems | H4 refuted; the coupled-physics explanation is dropped rather than rescued post hoc. |

**No outcome in this table is a reason not to publish.** Three of the five would require
the paper's claim to change, and the change is specified here in advance so that it cannot
be presented later as what was expected all along.

---

## 10. Power

The study has 70 pairs for the primary McNemar, but observations cluster hard by problem —
the per-problem proved fractions in §2 are mostly 0.0 or 1.0, so the effective sample is
much nearer 7 than 70.

Consequently:

- The design can detect **large** effects on the primary comparison — a risk difference
  above roughly 25 percentage points — and is **not powered** for anything smaller.
- The **interaction is under-powered** by any standard. The permutation test in §8.3 is
  reported for completeness and its result will not be used to support a positive claim.
- The **criterion-level model (§8.4) is where inference actually rests**, and it is
  labelled as such rather than being presented as a supporting analysis.

A null result on the primary comparison will be reported as *"not powered to detect an
effect below ~25 points"*, never as *"no effect"*.

---

## 11. Limitations

Declared in advance so they appear in the paper as design decisions rather than as
concessions extracted in review.

1. **n = 7, one textbook family.** No claim of generality across hydraulic design follows
   from this benchmark. E1 measures a mechanism, not a capability level.
2. **Pretraining contamination is plausible.** All seven problems are published. This cuts
   *toward* the negative results and *against* the positive ones: if A0 has seen these
   problems and still scores 30 %, the specification-failure finding is strengthened,
   while any A1 success is correspondingly weakened. A numeral-perturbed contamination
   probe on A0 is planned separately and is not part of E1.
3. **Output-space confound.** `A0-V` emits raw numbers; `A1` emits a policy that tools
   execute. These are different output spaces. The confound is inherent to the tools
   manipulation — a policy schema without tools is incoherent, since the tools are what
   execute the policy — and it cannot be removed. It is stated rather than controlled.
4. **Certificate leakage** (§4.3), deliberate, making `A0-V` an upper bound.
5. **Coverage.** 44 of 73 stated criteria are certified. The 29 remaining are excluded for
   documented structural reasons — 19 fix a dimension the design itself chooses, 4 are
   settled by the topology validator, 2 are non-quantitative, 2 fall outside quasi-static
   scope, 2 restate another criterion — and **none** is quantitative-and-in-scope-yet-
   unchecked. The exclusion table appears in the paper's main text, not an appendix.
6. **The verifier's own admissibility is under separate audit.** E1's results are
   conditional on that audit; if it invalidates a class of certificates, E1 is re-scored
   and the re-scoring is reported.

---

## 12. Deviations

Any departure from this document is appended below with a date and a reason. Entries are
added, never edited. An empty section at submission means the study ran as registered.

### D1 — 2026-08-31 — the repair budgets are not structurally comparable

Found while implementing `A0-V`, before any E1 data was collected.

§4.4 registers "repair budget: exactly 3 rounds, all verifier arms". **A1 as implemented
in the ablation harness has no outer repair loop at all.** `run_full_arm` calls the planner
once; the planner invokes a ReAct agent that may call `evaluate_sizing_policy` as often as
it likes and then hands the candidate set to `evaluate_and_select_sizing`. A1's iteration is
therefore *internal to the agent and unbounded*, while `A0-V`'s is *external and capped at
three*. The graph's `repair_sizing` node — which is bounded at three — is not on the path
the ablation takes.

The two are consequently matched in kind (both may revise against the certificate) but not
in count, and no configuration of the current code makes them identical.

**Resolution.** No change to `A0-V`, which keeps its registered 3-round cap. Instead:

1. Both arms record how many times a certificate was computed — `model_calls` and
   `repair_rounds_used` for `A0-V`, the `evaluate_sizing_policy` call count for A1 — and
   the comparison is reported against that, not against a nominal round count.
2. §10's power statement is unaffected.
3. The paper states the asymmetry in the limitations rather than claiming a match that the
   code does not implement.

This favours A1, which may iterate more. That direction is stated so it cannot later be
presented as a neutral choice: any A1 advantage is partly an advantage in attempts.

### D2 — 2026-08-31 — the ceiling now reaches the repair loop

Also found during implementation. In v0.7.1 the repair loop terminated on
`certificate.verdict == "PROVED"` while the reported verdict applied the oversizing ceiling
*afterwards*. A design that proved every criterion and was then rejected for bulk therefore
stopped the loop dead — the mechanism behind all ten P7-07 seeds returning the identical
refused design.

`A0-V` treats "clean" as *would be reported as proved*, so an over-ceiling design keeps the
loop running, and the feedback says in words that the design is rejected rather than
printing a bare ratio. A regression test asserts `converged` and the final verdict can never
disagree.

This is a fix to a defect, not a change of design, and it applies only to the new arm.
Whether to backport it to A1 is a separate decision and is **not** made here, because
changing A1 would invalidate the three collected cells.

---

## 13. Analysis code

Every table and figure in the E1 section of the paper is produced by
`run_evidence.py --study E1` from `evidence/E1_runs.jsonl`. The analysis script is written
and unit-tested on **synthetic data with a known ground truth** before the first real run,
so that the analysis cannot be tuned to the result it produces.
