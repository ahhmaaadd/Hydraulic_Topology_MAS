# LLM Sizing Agent in the Graph — v0.6.0 — 2026-08-20

## What is new

The sizing stage from v0.5.0 is now an agent inside the LangGraph workflow, so a
single run goes from a problem statement to a certified set of standard
components.

### Graph

Four nodes after `finalize_topology`: `plan_sizing`, `repair_sizing`,
`finalize_sizing`, and the routing between them. Sizing runs only on a topology
that validated - an unresolved topology has no settled valve states, so every
phase model would be a guess and there is nothing worth sizing.

### The planner

An agent with ten tools and no arithmetic of its own. It decides which phase
governs a dimension, what pressure to design against, whether a rod is chosen for
force or for area ratio, and how much margin to carry; every number comes from a
tool. That split is deliberate: the defects the reference audit found were unit
and convention errors - hydraulic power quoted as shaft power, force divided by
area without the mechanical efficiency - and those are exactly the mistakes a
language model makes fluently. Behind a tool they are impossible rather than
unlikely.

`evaluate_sizing_policy` is the important tool. It applies a candidate policy and
returns the full certificate, so the planner can test a design before committing
to it. That turns the model from a guesser into a searcher.

### Selection and repair

The planner returns two or three strategies and a deterministic score decides
between them. The score is the certificate: more criteria proved wins, and among
candidates that verify equally the smaller design wins. Without that last clause
the winning strategy is always "go up three bore sizes", which passes everything
and is a bad design.

The repairer sees what did not hold *and the operating point of every phase*,
because the regime is what says which change can help - a pressure-limited phase
needs a larger bore, and a bigger pump does nothing for it.

### Deterministic fallback

With no sizing model configured, the deterministic planner runs instead. That is
graceful degradation, and it is also the A0 control arm: both paths reach the
same certificate through the same graph, so comparing them is controlled.

### Empty certificates can no longer read as success

A requirements extraction carrying no loads or speeds, or one whose criteria all
resolve to nothing because no phase could be solved, previously produced an empty
certificate reporting PROVED. That is the most dangerous output this stage could
produce, because it looks exactly like success. It now raises
`NO_CHECKABLE_CRITERIA` and verdicts UNDECIDED.

### CLI and terminal

`--no-sizing` and `--max-sizing-rounds`. The terminal prints the selected
components, the certificate, the operating point of every phase, the findings and
the candidate scores. Exit code 3 distinguishes "topology valid but sizing not
certified" from both success and topology failure.

## Results

All seven problems reach the same certificates through the graph as through the
direct planner: four PROVED, two UNDECIDED on a single marginal criterion, one
REFUTED with a closed-form proof.

## Tests

261 offline tests pass, up from 230. `tests/test_sizing_agent.py` stubs the
planner and repairer to exercise the tools, the scoring, the repair loop, the
routing, the fallback and the rendering. What the model *chooses* is not testable
offline; what the system does with a choice is, and that is the part that has to
be right.

# Sizing and Certification — v0.5.0 — 2026-08-20

## What is new

A sizing stage that takes a validated topology to a set of standard components
and a machine-checkable certificate for every acceptance criterion. The topology
stage is unchanged; this builds on it.

### Phase-Resolved Quasi-Static verification (PQV)

The enabling observation: the topology stage already emits and *proves* the
discrete valve state of every phase. That is the combinatorial part of hydraulic
analysis, and with it fixed each phase reduces to a small algebraic system in
pressures, flows and one velocity. The only discreteness left is whether the
relief is cracked or shut; both regimes are enumerated, solved and checked
against their own consistency conditions, so the answer is certified rather than
assumed. Two degenerate outcomes are reported rather than hidden: no consistent
regime means the sizing cannot deliver that phase, and two consistent regimes
mean the circuit has more than one stable operating point.

`validation.phase_flow_paths` exposes the proved supply and exhaust paths so the
two stages cannot disagree about the circuit.

### Interval certification

Every criterion is bounded over an operating envelope - two points either way on
each efficiency, plus the load tolerance where a brief states one - using corner
evaluation where the outcome is monotone and verified interior sampling where it
is not. A verdict is PROVED only when the corner argument actually holds;
otherwise it is UNDECIDED and says so. Infeasible points inside the envelope
refute outright rather than being dropped.

### Parametric catalog

ISO 3320 bores, ISO 4395 rods, standard displacements, NG valve sizes, tube
series, IEC motor ratings and reservoir sizes, all vendor-neutral. Components
excluded from the topology stage because they do not change topology - filters,
reservoirs, prime movers, lines - enter here, which is the clean split between
topology-changing classes and sizing-only components.

### Deterministic planner

Sizes from the theoretical minimum bore upward and lets verification drive the
growth, so the result is the smallest design that passes rather than the first
one a conservative design pressure lands on. Most repairs are closed form: a
refuted force gives the required area directly, and a load-actuated speed ratio
that no bore can meet is diagnosed with the derived window rather than by
enlarging parts at random.

## Results on the seven problems

Four certify clean (P7-02, P7-03, P7-05, P7-06); two are UNDECIDED on a single
marginal criterion; one is REFUTED with a proof.

40 of 44 criteria PROVED, 3 UNDECIDED, 1 REFUTED.

**P7-04 is refuted for a provable reason.** A 3:1 load-actuated speed ratio
through one fixed orifice needs 9:1 in orifice pressure drop. Writing the force
balance at both operating points with the cap pinned at the relief, the annulus
area cancels and the cap area is squeezed into 6944..7639 mm2 at a 20 bar
ceiling. No ISO preferred bore lies in that window, so the requirement is
unreachable for any rod diameter. That is a closed-form infeasibility proof, not
a failure to converge.

## E1 - the reference audit as ground truth

The seven defects found by hand before this verifier existed are rediscovered on
**six of seven** reference designs, each by the layer that owns the physics it
violates:

| Problem | Layer | What was found |
| --- | --- | --- |
| P7-01 | V6 | worst phase needs 2.46 kW at the shaft against a 1.5 kW prime mover |
| P7-02 | V6 | 5.76 kW needed against 3.0 kW specified |
| P7-04 | V6/V7 | 0.41 kW against 0.37 kW, and 0.15 bar from the relief |
| P7-05 | V4/V3 | the drill needs 9.57 bar in an 8 bar system and cannot move |
| P7-06 | V4/V3 | the platen needs 72.84 bar against a 70 bar relief |
| P7-07 | V3 | the clamp branch reaches 43.1 bar against a 40 bar ceiling |

The seventh, P7-03, certifies clean and that is the correct answer: the audit's
finding there was that the *prose* quoted hydraulic power as shaft power, but the
1.5 kW motor actually specified does cover the 1.45 kW the design needs. A
verifier that flagged it anyway would be reporting a false positive, so the test
suite asserts it stays clean.

## Correction to the earlier audit

The audit claimed P7-04's reference 100/50 cylinder could not produce the
required speed ratio. That was computed on the reference pack's own mixed basis,
with efficiency omitted from the rod-side pressure but applied to the ceiling
check. Solved consistently, the 100/50 *is* nominally feasible - and far worse
than infeasible in practice: at 0.88 mechanical efficiency it stalls completely,
and at 0.92 it runs 49 percent fast. The operating point sits 0.4 bar from stall.
Nominal feasibility with no margin is the more useful finding, and it is exactly
what the envelope check exists to expose.

## Tests

230 offline tests pass, up from 157. `tests/test_sizing.py` pins the physics
against independently computed hand calculations, pins the reporting contract so
the verifier keeps the ability to say REFUTED and UNDECIDED, and runs E1.

# Common-Command Interlock and Subject Stall Detection — v0.4.5 — 2026-08-20

## Run set diagnosed

Three traces from v0.4.4.

| Problem | Status | Rounds |
| --- | --- | --- |
| P7-04 | **`validated_with_warnings`** | 1 |
| P7-05 | `unresolved` | 4 |

P7-04 is fixed. Both v0.4.4 normalizations fired on it exactly as designed - the
approach phase was promoted to share the throttle, and no sequence valve was
demanded - and the minimal six-component circuit validated in a single round.

P7-05 also improved: **deterministic validation now passes it completely.** The
only blocking error in the final round came from the LLM design reviewer.

## P7-05 - a pressure interlock needs one command, not two

The run used two directional valves, a float-centre `ClampDCV` and a
closed-centre `DrillDCV`, and then spent four rounds alternating between the only
two places it could take the forward sequence pilot from:

| Round | Pilot source | Rejected because |
| --- | --- | --- |
| 1 | `ClampDCV.A` | dies when the clamp valve leaves extend |
| 2 | `Pump.P` | pump pressure proves nothing about clamp force |
| 3 | `ClampLoadLock.A2` | isolated by the lock once the clamp valve centres |
| 4 | `Pump.P` | same as round 2 |

Neither option can work, and that is a property of the two-valve arrangement
rather than of the wiring. With separate valves the clamp branch can be
de-commanded independently, so no clamp-referenced node stays live through every
phase where the sequenced branch must be open, and the only always-live node -
the pump line - carries no information about the clamp. Sharing one directional
valve removes both horns: the clamp line stays live for the whole forward
command, so the pilot is clamp-referenced *and* alive. It is also the only
arrangement in which the ordering is genuinely hydraulic; two independently
switched valves make the sequence a property of the control wiring, which these
briefs forbid.

Both earlier successful runs of this circuit family - the v0.4.1 P7-05 and the
v0.4.1 P7-07 - used one valve.

- Candidate scoring now rejects a plan whose pressure-sequenced functions are
  commanded by more than one directional valve. Functions with no pressure
  interlock between them are unaffected.
- `pressure_sequence_between_functions` states the rule and adds the
  corresponding prohibitions, including not piloting from the common pump line.

## The stall detector was blind to renamed findings

Four rounds produced four error codes for one fault:

```
FWD_SEQ_PILOT_LOST_IN_CLAMP_RELEASE
FWD_SEQ_NOT_CLAMP_PRESSURE_INTERLOCKED
FWD_SEQ_PILOT_IS_ISOLATED_BY_LOAD_LOCK
FWD_SEQUENCE_NOT_CLAMP_PRESSURE_REFERENCED
```

`_topology_error_signature` hashes codes, so every round looked like progress and
neither escalation nor the no-progress stop ever fired. Deterministic codes are
stable; free-form reviewer codes are not.

- New `_topology_error_subjects` records which *component ids* an unresolved
  error set is about. Component ids are stable across renames.
- Two consecutive rounds whose error subjects overlap by 60 % or more count as no
  progress and escalate the repair scope, whatever the codes say.
- Replaying the four P7-05 rounds, the stall is now detected at **round 2**
  (overlap 0.67), escalating from `wiring` to `selection` - which is where the
  two-valve fault actually lives, and where the new common-command rule rejects
  it.

## Tests

157 offline tests pass, up from 152.

# Rule-Contradiction Repair — v0.4.4 — 2026-08-19

## Run set diagnosed

Eight traces from v0.4.3. Five carry a topology:

| Problem | Status | Rounds |
| --- | --- | --- |
| P7-01 | `validated_with_warnings` | 1 |
| P7-02 | `validated_with_warnings` | 1 |
| P7-03 | **`validated`** | 1 |
| P7-04 | `unresolved` | 4 |
| P7-05 | `unresolved` | 4 (twice) |

P7-03 is now clean, confirming the v0.4.0 float-centre work. Neither remaining
failure is a model mistake. In both, two deterministic rules demanded opposite
things, so no topology could satisfy both and the repair loop spent its whole
budget moving a component it was simultaneously required to have and forbidden
to have.

## P7-04 - a pressure trigger inside one actuator is not sequencing

`pressure_sequence_required` was `any(step.trigger == "pressure")`. P7-04's brief
says "the speed change must result from the change in load", which the extractor
correctly typed as a hydraulically enforced pressure trigger. Both phases belong
to the same actuator, and the mechanism is a plain throttle: a non-compensated
orifice passes less flow as its pressure drop collapses. But the rule saw a
pressure trigger and demanded a sequence valve, at which point the minimality
rules and the design reviewer rejected that same valve
(`UNAUTHORIZED_SEPARATE_APPROACH_CONTROL`,
`LOAD_TRIGGERED_SPEED_CHANGE_NOT_ENSURED`). Deadlock.

- New `gap_policy.interfunction_sequence_steps` / `requires_pressure_sequence_valve`.
  A step counts as sequencing only when the functions it activates differ from
  those its predecessor activates.
- `validation.py`, `candidate_selection.py` and `patterns.py` all read the same
  helper, so the validator, the scorer and the hints can no longer disagree.
- A *position* trigger inside one actuator still requires a valve state change -
  a cam-operated bypass cannot be a throttle characteristic - so P7-01 and P7-02
  are unaffected.

### The other half

P7-04's approach was typed `unrestricted_rapid` and its feed
`load_sensitive_throttled`, both extending. That pair cannot be built: a throttle
in the rod line is in circuit for the whole extend stroke, and with no position
trigger nothing can take it out for one phase. The unmetered phase demanded a
bypass, the metered phase forbade one.

New `_normalize_load_actuated_group` promotes the unmetered phase to share the
throttle, unless a position trigger exists - in which case the bypass is real.
With both fixes the minimal seven-component circuit validates.

## P7-05 - a parked hold and a maintained hold need opposite things

`LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD`, added in v0.4.0, was written for P7-03's
carriage: the command is released, the spool centres over the load, and the lock
must reseat, so its pilot has to reach tank. P7-05's clamp is the other kind of
hold. It stays commanded and supplied while the drill feeds; live pressure holds
the load and venting the work line would release the very force the phase exists
to maintain.

Applying the parked-hold rule to a maintained hold left no way out. Centring the
clamp valve satisfied the venting rule but killed the pilot feeding the forward
sequence valve (`SEQUENCE_PILOT_NOT_PRESSURIZED`, and in the second run
`REVERSE_INTERLOCK_PILOT_DROPS_DURING_CLAMP_RELEASE`). Keeping it commanded fixed
the pilot and tripped the venting rule.

- The hold branch now decides which kind of hold it is first. If the held
  actuator has a directed pump path in that phase it is a maintained hold and the
  venting rule does not apply; if it does not, it is parked and the rule stands.
- New `CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED`: a hold that runs concurrently
  with another active function and has no pump path is rejected. Oil checked in
  behind a load lock holds only until it leaks, and anything piloted from it dies
  with it.
- `reverse_order_interlock` and `pressure_sequence_between_functions` now state
  the pilot-liveness rule directly: take a sequence pilot from a line that stays
  commanded and pump-supplied for the whole sequenced phase, never from a chamber
  isolated by a load lock or a centred spool.

Replaying the returned P7-05 topology with the clamp valve kept commanded gives
`valid`. As returned it now reports `CONCURRENT_HOLD_NOT_PRESSURE_MAINTAINED`
alongside the pilot errors, which says what to change rather than only what is
missing.

## Note on the modified P7-05 run

One trace ran P7-05 with the "no electrical sensing device" sentence removed.
That did not help - the planner used the freedom to add a second directional
valve, which broke the pilot chain in a new place. The constraint was not what
made the problem hard.

## Tests

152 offline tests pass, up from 145.

# Crash Repair and Rare-Branch Hardening — v0.4.3 — 2026-08-19

## Reported crash

A live P7-01 run died in `plan_components`:

```
File "hydraulic_mas/candidate_selection.py", line 172, in _candidate_evaluation
  "topology catalog gaps: " + ", ".join(sorted({gap.capability for gap in topology_gaps}))
AttributeError: 'dict' object has no attribute 'capability'
```

`blocking_catalog_gaps` takes and returns plain dicts, but the caller read an
attribute. Fixed by reading `gap.get("capability")`.

The bug shipped in v0.3.0 and is unchanged from that release - it is not a
regression from any v0.4.x work. It survived every trace reviewed so far for one
reason: the branch only executes when the component planner emits a genuinely
*blocking* `CatalogGap`, and until this run none ever had. Every earlier trace
carried `catalog_gaps: []`.

## The defect class, and what now prevents it

Most of this pipeline's optional fields - `catalog_gaps`, `evidence_gaps`,
`repair_actions`, `external_interfaces`, `port_terminations`,
`component_planner_recovery` - are empty in a healthy run. The code that consumes
them therefore only runs when something has already gone wrong, which is exactly
when a crash costs the most. Two additions close that hole.

### `tests/test_rare_branch_smoke.py`

Populates the optional fields and runs the real deterministic code over them. It
pins the reported traceback directly, and covers alongside it: a blocking gap
through `evaluate_and_select_candidates` as the graph actually calls it; gap
helpers given empty, null-valued and sizing-scoped payloads; all six repair
action kinds applying; malformed repair payloads being rejected at parse time
rather than reaching `apply_repair_actions`; validation with gaps, interfaces and
terminations all populated; `repair_scope` over every scope and over models,
dicts and empty input; and terminal rendering of a fully populated final output
and of a failure payload.

It also pins the `min_length=2` constraint on `ComponentPlanSet.candidates`,
because `evaluate_and_select_candidates` ends with `ranked[0][0]` and is only
safe while that holds.

### `tests/test_dict_model_boundary_lint.py`

A static check over every module in the package. Within each function it tracks
names that provably hold dicts - assigned from a known dict-returning helper,
from `.model_dump(...)`, or from a comprehension over one of those - and fails if
any is read with attribute syntax.

Two Python details had to be handled correctly, and both produced false positives
in the first cut:

* a comprehension has its own scope, so `[g.model_dump() for g in gaps]` must not
  leak `g` into the enclosing function; and
* an assignment's right-hand side is evaluated before the name is rebound, so
  `value = value.model_dump()` is not a dict read with attribute syntax.

The lint is deliberately conservative: it follows only sources it is certain
about, so it will not catch every possible instance, but it reports no false
positives on the current tree and it detects the line that shipped. Two
self-tests guard it - one asserts it still catches the original bug, one asserts
it does not flag `plan.evidence_gaps`, whose elements really are models.

## Audit result

The package was swept for the same pattern. `candidate_selection.py:172` was the
only occurrence. `candidate_selection.py:246` reads `gap.capability` off
`plan.evidence_gaps`, which are Pydantic models, and is correct. `terminal.py`
consumes `final_output` consistently through `.get()`, which is correct because
that payload is JSON. `mypy` was run over the package; its remaining findings are
kwargs-splat and `Literal` narrowing noise, none of them reachable crashes.

## Tests

144 offline tests pass, up from 95.

# Shared-Supply Speed Repair — v0.4.2 — 2026-08-19

## Run diagnosed

P7-05 on v0.4.1 returned `validated_with_warnings` — a structurally valid
circuit, reached in three topology rounds. But it contains no flow control on
the drill feed. Both motions were typed `sizing_only`:

```
workpiece_clamp.clamp_advance       25.0   mm/s   sizing_only
drill_feed.drill_advance_feed        1.667 mm/s   sizing_only
```

`sizing_only` means "choose the pump and the bores to reach this speed". On a
shared supply that is not something sizing can deliver. Both actuators run
sequentially from one fixed pump in the same command direction, so each in turn
takes the whole delivery and moves at `Q_pump / A`. Their areas are already
fixed by their force requirements and the pump is already fixed by the faster
motion, which leaves the slower actuator's speed over-determined:

```
Pump sized for the clamp   12 272 mm2 x 25 mm/s        = 18.41 L/min
Drill fed unthrottled      18.41 L/min / 3117 mm2      = 98.4 mm/s
Required                                                 1.667 mm/s   -> 59x over
Bore that would fix it by sizing alone                   484 mm
```

The drill needs 0.31 L/min, so a throttle has to absorb 18.1 L/min. The
reference solution has exactly this element ("drill one-way flow control,
approximately 0.216 L/min meter-out"); the run omitted it and nothing caught it.

This failure mode was identified when the v0.3.0 P7-05 trace was first reviewed
and a rule was planned for it, but the rule was never written. v0.4.2 writes it.

## Changes made

- New `requirements_quality._normalize_shared_supply_speeds`. Within one motion
  direction, when two or more distinct functions carry explicit numeric speeds
  and all are typed as unmetered, only the fastest may stay `sizing_only` — the
  pump is sized for that one. Every phase at least 3x slower is promoted to
  `adjustable_throttled` with `meter_out` on its exhaust chamber and a
  non-compensated throttle.
- Meter-out is the conservative default: it holds the actuator back rather than
  letting it run away when a drill breaks through or a load drops off.
- Speeds are normalized to mm/s before comparison, so `1.5 m/min` against
  `0.1 m/min` is compared correctly.
- The 3x guard keeps the rule off cases a bore choice really can reconcile, and
  a single function is never affected — one actuator owns the whole delivery, so
  its speed genuinely is a sizing choice, and distinct speeds within one function
  are already covered by the existing rule.
- Each promotion is recorded in `requirements_normalizations` with the reason.

Replaying the live P7-05 requirements through the fix produces:

```
drill_advance_feed promoted to an adjustable meter-out throttle: one supply also
serves a 25 mm/s extend motion, so a 1.66667 mm/s motion on the same pump cannot
be realized by pump and bore sizing alone.
```

The clamp stays `sizing_only`. Adding the implied one-way flow control to the
run's own topology and revalidating gives `valid` with no errors.

## Tests

95 offline tests pass, up from 91. Added: the exact P7-05 speed pair is promoted
while the faster motion is left alone; a modest speed difference is not touched;
a single function is never affected; and mixed speed units compare correctly.

# Requirements-Gate Repair — v0.4.1 — 2026-08-19

## Run set diagnosed

Two LangSmith traces of P7-07 on v0.4.0. Neither reached the topology stage, so
neither exercised any v0.4.0 change.

* `01a01971...` — extract, critique, repair, critique, then `clarify_requirements`
  raised the interactive `GraphInterrupt` asking whether any phase is
  overrunning. Expected interactive behaviour, not a failure.
* `01a01981...` — the resumed run, with "horizontal / non-overrunning" answered.
  It reached `finalize_requirements` and stopped at `requirements_failure`.

## Root cause

The gate blocked on exactly one structural issue:

```
Motion phase 'clamp_hold_during_work' has an unspecified load_control decision.
```

Everything else was in order. The critic returned `proceed_with_assumptions`
with zero blocking questions, `clamp_actuator.holding.must_hold_position` was
true, `load_holding` was already a derived design driver, and the clarification
about load character had been answered and recorded. The extractor simply
declined to name a load-control strategy, the repair round did not name one
either, and `LoadControlStrategy.unspecified` reached a hard structural check
with no deterministic resolution behind it. The run ended before research.

`unspecified` means "the extractor did not decide". It is derivable from the
load type and the holding contract, so it should never have been able to end a
run.

## Changes made

- New `requirements_quality._normalize_load_control` resolves every
  `load_control=unspecified` phase deterministically, before the structural
  check runs. An overrunning or gravity load takes `counterbalance`; a hold
  phase on a function that declares a holding requirement takes `pilot_check`;
  anything else takes `none`. Every rule can only add load control, never remove
  it, and an explicit decision is never overwritten. Each resolution is appended
  to `requirements_normalizations` so the choice stays auditable.
- `repair_requirements` now normalizes its output and recomputes the structural
  issues before the second critique. A deterministic correction no longer has to
  survive another LLM round to take effect, and the critic sees a spec whose
  typed decisions are already resolved.
- Extractor prompt: `load_control` must always be decided, `unspecified` will
  fail the gate, and the choice follows from the load and the holding contract
  rather than from the valve that will eventually be selected.

## Fix to a v0.4.0 rule

The v0.4.0 candidate-selection rule that requires a venting valve centre
alongside a pilot-operated load lock keyed off "the function has a hold phase".
That is too broad. P7-07's clamp is held at reduced pressure while the
directional valve stays commanded forward, so its lock pilot is fed from a live
line rather than trapped by a centred spool; the rule would have forced a float
centre onto every clamp-then-work station.

It now triggers only on an *unpowered* hold — a stated hold duration, a drift
tolerance, a no-creep clause, or power-loss/hose-burst retention — which is what
actually centres the spool over the load. P7-03's ten-minute drift-free hold
still triggers it; P7-07's powered clamp hold no longer does.

The deterministic validator rule `LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD` is
unchanged. It works from the phase graph rather than a heuristic, and it already
passes P7-07 correctly: the clamp lock's pilot reaches tank through the reverse
sequence valve's integral check and the commanded directional valve.

## Tests

91 offline tests pass, up from 84. Added: the exact blocking issue no longer
reaches the gate; overrunning phases resolve to counterbalance and unheld phases
to none; an explicit decision is never overwritten; a powered hold does not
demand a venting centre while an unpowered timed hold still does; and an
end-to-end case that normalizes the live requirement shape and then validates
the corrected clamp-then-work topology against it with zero issues.

# Live-Run Failure Repair — v0.4.0 — 2026-08-19

## Run set diagnosed

Eleven LangSmith traces from the v0.3.0 release. Seven carry a topology: P7-01,
P7-02 and P7-06 reached `validated_with_warnings` in a single topology round;
P7-03, P7-04, P7-05 and P7-07 exhausted all four rounds and returned
`unresolved`. The remaining four traces stopped at the clarification interrupt.

The four failures reduce to five root causes, documented with their controls in
`docs/REVISION_PLAN_V0.4.0.md`.

## Changes made

### Catalog (21 -> 24 topology-changing classes)

- Added `GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV`: neutral blocks P and connects
  A, B and T. This is the class P7-03 needed and could not find. A pilot-operated
  check only reseats when its pilot can decay to tank, and every previous 4/3
  class blocked both work ports in neutral.
- Added `GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV`: neutral connects P, T, A and B,
  so a fixed pump unloads and both work lines vent at the same time.
- Added `GENERIC_VENTED_PILOT_OPERATED_RELIEF_VALVE`: a vent port gives true
  single-pump unloading and staged decompression without adding a second pump.

### Validation

- New `LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD`. In a hold phase, every load lock's
  pilot port - or, for a cross-piloted dual lock, its valve-side ports - must
  have a directed path to tank in that phase's graph.
- The forbidden-overlap rule now computes which *direction* a residual path
  could drive an actuator, and subtracts directions covered by a declared and
  proven end-of-stroke completion. This removes the P7-05 false positive that
  fired against a drill already sitting on its stop.
- New `UNPROVEN_COMPLETED_FUNCTION` guards the new field: a completion claim must
  be backed by an earlier phase in the operational sequence that actually drives
  that function in that direction.
- Completion is evaluated in declared sequence order rather than function
  declaration order.
- `EvidenceGap.blocking` is no longer trusted verbatim. `classify_evidence_gap`
  decides deterministically, and the model's flag can only downgrade. The
  downgrade reason is written into the issue description.

### Requirements normalization

- New `_normalize_flow_compensation` promotes a throttle to pressure-compensated
  when a phase declares its speed load-independent, or when two throttled phases
  of one function in the same direction are commanded to the same speed while
  the force differs by more than 25 %. This catches the latent P7-02 error: a
  plain throttle cannot hold 2 m/min through both a 12 kN and a 50 kN stage.
  Every promotion is recorded in `requirements_normalizations`.

### Candidate selection

- Two or more pumps are rejected without an energy-saving/hi-lo driver. A hi-lo
  supply is an energy pattern, not a speed-control mechanism.
- Plain check valves are rejected unless they serve pump combining or
  regeneration; the sequence, reducing-with-reverse-check and one-way flow
  control classes already carry integral reverse checks.
- A load lock assigned to a function with a neutral hold phase is rejected when
  it is paired with a directional valve whose neutral blocks the work ports.

### Pattern library

- New `<function>_load_actuated_speed_change_minimal`: exactly one
  non-compensated one-way flow control, with explicit prohibitions on a parallel
  bypass, a feed-enable sequence valve and a second pump.
- `reverse_order_interlock` now carries the concrete wiring recipe - DCV.B to the
  working retract chamber and to a second sequence valve whose A port feeds both
  the clamp release chamber and the clamp lock's pilot - plus prohibitions on
  position valves, redundant checks and relying on solenoid energisation order.
- `load_lock` states the required pump/valve-centre pairing.

### Repair routing

- New `_topology_error_signature` hashes only unresolved error codes and their
  subjects. The previous fingerprint included components and connections, so any
  cosmetic change looked like progress and the loop never noticed it was stuck.
- When a signature survives a repair round, the scope escalates one level
  outward: `wiring -> selection -> requirements`, and `research -> selection`.
  The escalation and its reason are appended to the validation summary.

### Prompts and schema

- `PhaseConfiguration.completed_function_ids` added and documented.
- Component-planner prompt: hi-lo is an energy pattern; a load-actuated speed
  change is one throttle; plain checks need a combining or regenerative reason;
  position valves need a position trigger; valve-centre selection is deliberate.
- Netlist-builder prompt: declare a finished actuator instead of isolating it,
  and pair load locks with a venting neutral.

## Tests

84 offline tests pass, up from 58. `tests/test_v040_failure_modes.py` pins both
halves of every fix: the defect is still rejected and the correct circuit is now
accepted. The P7-03 acceptance fixture was corrected to a float centre - its
previous tandem centre was the defect this revision found.

# Complete-Circuit Reliability Revision — v0.3.0 — 2026-08-17

## Run set diagnosed

Reviewed the P7-01 through P7-07 traces and compared their behavior with the
functional benchmark circuits. The revision targets correctness rather than
literal benchmark similarity. The detailed failure/control/acceptance matrix is
in `docs/REVISION_PLAN_V0.3.0.md`.

## Changes made

- Added mandatory typed `speed_realization` and propagated it from requirements
  through the final netlist. Numeric speed targets alone are now sizing-only;
  distinct speeds in the same direction require an explicit topology mechanism.
- Added an auditable generic pattern library for rapid/feed bypass, typed
  metering, passive load-dependent throttling, load locks, counterbalance,
  regeneration, pressure sequencing, lower-pressure branches and synchronization.
- Made clarification answers authoritative to the extractor, critic and repair
  stages. Added semantic clarification topics/keys so a model cannot re-ask an
  answered question under a new id.
- Added `max_working_pressure` and deterministic pressure-reducing-valve checks
  for a function whose ceiling is below the global system ceiling.
- Split `EvidenceGap` from true `CatalogGap`. Missing an exact cited schematic
  no longer masquerades as a missing component class; a genuinely absent class
  still blocks.
- Added deterministic candidate rejection for unjustified flow controls,
  sequence valves and position valves, plus a stronger component-count penalty.
- Added regenerative chamber-recirculation validation in place of mandatory
  tank exhaust, and explicit matched-series actuator displacement reachability.
- Restricted hydraulic trigger validation to genuinely hydraulically enforced
  steps and deterministically normalizes operator/external command mistakes.
- Added requirements-scoped topology repair, error/topology fingerprints,
  one-targeted-search-per-fingerprint behavior and no-progress termination.
- Reduced catalog-tool churn by encouraging grouped inspection and accepting a
  single final `get_port_reference` audit call.
- Extended terminal/final JSON output with speed realization, separate gap
  classes, repair stop reason and validation fingerprints.
- Packaged the seven default problem statements inside `hydraulic_mas.data` so
  the installed console command works from a wheel as well as an editable source
  checkout.
- Added regression coverage for every observed root-cause family, including the
  exact repeated-clarification, phantom-sequence, regeneration, series-coupling,
  branch-pressure, gap-classification and stalled-repair failures.

## Verification

- Full offline suite: **58 passed**, including accepted canonical P7-01 through
  P7-07 topology fixtures.

---

# Component-Planner Structured-Output Recovery — v0.2.2 — 2026-08-14

## Failure diagnosed

The live component planner returned two `add_connection` repair actions with
`replacement_connection=null`. `RepairAction` correctly rejected those
objects, but provider-native structured-output parsing raised
`StructuredOutputValidationError` inside the LangChain agent before
`plan_components` could inspect or retry the candidate set.

## Changes made

- Switched the tool-using component planner to LangChain `ToolStrategy`, which
  returns schema validation feedback to the same agent for correction.
- Added a three-attempt outer recovery boundary specifically for
  `StructuredOutputValidationError`; unrelated API/network failures are not
  silently swallowed.
- Added an explicit action-to-payload contract to the component planner prompt.
  Initial candidates must keep `repair_actions=[]`; repair actions are emitted
  only during an explicit repair pass.
- Added a safe pre-validation normalization for the common field mix-up where
  an `add_connection` puts its new connection in `target_connection` (or
  `connection`) instead of `replacement_connection`.
- Kept a truly payload-free action invalid, so no connection is invented or
  silently executed.
- Added `component_planner_recovery` to terminal output, graph state, failed-run
  snapshots and final JSON.
- Added regression tests for field normalization, genuinely empty payload
  rejection, and successful planner recovery after the exact exception class.

## Verification

- Full offline suite: **41 passed**.

---

# Requirements-Gate Hotfix — v0.2.1 — 2026-08-14

## Failure diagnosed

P7-03 repeatedly stopped at `requirements_failure` even though the critic
returned `proceed_with_assumptions` with zero blocking questions. The finalizer
incorrectly treated every free-form `consistency_issues` entry as a hard error,
so topology-neutral notes about verification tolerances prevented all research
and design.

## Changes made

- Separated deterministic `requirements_structural_issues` from critic
  `requirements_advisories` throughout graph state and routing.
- Restricted automatic requirement repair to structural errors. Advisory
  review notes no longer consume the single repair attempt.
- Restricted the final stop condition to a genuine `needs_clarification`
  result or structural errors that survive repair.
- Added sequence-reference checks for unknown functions/phases, duplicate
  ordering, phase/function mismatch, and invalid predecessors.
- Deterministically removes an impossible active-and-forbidden function
  self-reference while recording the normalization in the gate audit.
- Merges nonblocking assumptions and downgrades stale blocking flags on open
  questions when the critic explicitly decides the topology can proceed.
- Resets structural-repair eligibility after a human clarification and fresh
  extraction.
- Prints a classified requirements-gate audit in the terminal.
- Failed-run JSON now preserves requirements and all available downstream
  diagnostic snapshots.
- Added regression coverage for advisory, genuinely blocking, structurally
  invalid, normalization, and full offline graph cases.

## Verification

- Full offline suite: **38 passed**.

---

# Phase-Aware Correctness Revision — 2026-08-14

## Why another revision was required

Runs P7-01 through P7-07 showed that the first topology-only revision removed
sizing clutter but still could not prove circuit operation. The deterministic
validator used undirected reachability and merged extend, retract and neutral
DCV paths. As a result, it could accept a chamber as “controlled” using a valve
state that was not active in the phase being checked. Meter-in/meter-out was
described in prompts but was not a typed decision, catalog gaps encouraged
non-equivalent workarounds, and repairs could not remove an unnecessary part.

## Changes made in this revision

### Typed motion and synchronization decisions

- Added mandatory phase ids, motion direction, `metering_side`, metered chamber,
  metered flow, compensation class, load-control strategy, justification and
  source to `MotionPhase`.
- Added canonical `MotionControlDecision` records and deterministic propagation
  requirements → design brief → candidate plan → selected plan → netlist → final
  output. Every intermediate LLM result is overwritten with the finalized
  decision values so downstream agents cannot reinterpret them.
- Added explicit synchronization strategies: `rigid_platen_parallel`,
  `hydraulic_series`, and `flow_divider_parallel`, including actuator count and
  series-displacement compatibility status.
- Added typed sequence triggers, predecessor phases, hydraulic-enforcement flags
  and forbidden-overlap function ids.
- Circuit-changing structural errors that remain after requirement repair now
  stop at a controlled requirements failure instead of leaking into design.

### Expanded generic topology catalog

- Expanded the sizing-free catalog from 15 to 21 classes.
- Added a plain check valve, externally piloted unloading valve,
  counterbalance/overcenter valve, flow divider/combiner, pressure-reducing
  valve with reverse check, and externally piloted sequence valve.
- Added machine-readable directed fixed/state/conditional flow paths to catalog
  entries. These are behavioral topology facts, not ratings or sizing data.
- Corrected the capability distinction: pilot-operated checks implement static
  load locking; only a counterbalance class satisfies dynamic
  `counterbalance_overrunning` control.
- Every topology-scoped `CatalogGap` is now blocking even if a model labels a
  workaround nonblocking.

### Candidate selection and executable repair

- The catalog-aware designer now returns two or three candidate
  component/connection plans.
- Added deterministic scoring for catalog identity, minimum inventory,
  capability coverage, synchronization, topology gaps, true hi-lo check/unload
  functions, unjustified special valves and component count.
- An invalid model-preferred candidate can no longer override a valid simpler
  candidate.
- Added executable add/delete/replace component and add/delete/replace
  connection repair actions. `delete_component` removes the target even if it
  remains in the model's returned full plan.
- Candidate scores, selected candidate and executed repair history are printed
  and included in final JSON.

### Directed phase-state validator

- Replaced union-of-states undirected actuator validation with one directed flow
  graph per `PhaseConfiguration`.
- Physical connections are bidirectional lines; direction comes only from the
  selected component state and check/metering behavior.
- Each motion phase now proves pump-to-commanded-chamber supply and
  opposite-chamber-to-tank exhaust.
- Validation enforces the exact meter-in/meter-out path and compensation class,
  detects a reversed one-way control, and rejects an open parallel bypass that
  defeats metering.
- Added state validation for DCVs, position valves, sequence valves, unloading
  valves and counterbalance valves, plus conditional pilot-release checks.
- Added pressure/position sequence transition proofs and forbidden-function
  motion detection.
- Added deterministic rigid-platen invariants: all caps share one work branch,
  all rods share the opposite branch, and any cylinder-to-cylinder connection
  is rejected. Legitimate hydraulic series requires an explicit series strategy
  and explicitly matched displacement compatibility.
- Added true hi-lo invariants requiring an unloading valve and a plain check
  valve; sequence-valve workarounds are rejected.

### Research and terminal behavior

- Tightened research coverage: a critical circuit-pattern need requires verified
  connection/placement evidence plus an operating-principle or safety claim.
- Added bounded targeted-research routing for a reviewer issue explicitly marked
  `scope=research`; known selection and wiring errors remain local repairs.
- The terminal now prints typed motion decisions, exact phase states, candidate
  comparisons and repair history in addition to components and connections.

### Verification

- Added regression/property tests for decision mutation, reversed flow control,
  active metering bypass, wrong DCV state, blocking catalog gaps,
  pilot-check/counterbalance distinction, rigid parallel cylinders, rejected
  rigid-platen series plumbing, executable component deletion, and bad hi-lo
  candidate rejection.
- Offline result for this revision: **34 passed**.

---

# Topology-Only Revision — 2026-08-14

## Why the previous run failed

The inspected run at `runs/20260814-075941/P7-01.json` became a sizing and
procurement exercise instead of a topology exercise. It selected 16 component
instances, including an OEM pump and cylinder, manifold, suction strainer,
filter, cooler, pressure gauge, filler/breather, level/temperature device, line
set and other auxiliary hardware.

The resulting topology was then rejected for sizing-level questions such as
pressure-compensator margin, exact position-valve dropout behavior and whether
the selected flow setting met the rapid-approach criterion. Those are not valid
reasons to reject a generic circuit topology before the sizing agent exists.

The main root causes were:

1. The runtime catalog contained 58 exact/engineered choices with ratings,
   dimensions, manufacturers and accessories, so the designer was encouraged to
   optimize parts rather than circuit functions.
2. The component-planner prompt explicitly requested a complete power unit with
   prime mover, filtration, return hardware and accessories.
3. The deterministic validator checked valve pressure/flow ratings and cylinder
   stroke/force capacity.
4. The engineering reviewer was not told that all sizing questions were outside
   its verdict boundary.
5. The terminal emphasized manufacturer and sizing fields instead of the direct
   connection schedule.

## Changes made

### Generic topology catalog

- Replaced the exact-part catalog with 15 generic class entries tailored to the
  seven bundled benchmark prompts.
- Retained only topology-changing functions: tank, fixed or
  pressure-compensated pump, relief valve, three DCV center/position choices,
  double-acting cylinder, compensated and non-compensated one-way speed control,
  position bypass, dual/single pilot-operated checks, hydraulic sequence valve
  and pressure-reducing valve.
- Removed manufacturer, model, part number, dimensions, pressure/flow rating,
  displacement, calculated duty, source and sizing parameter fields.
- Explicitly forbade manifolds, tees, pipes/hoses, filters/strainers, coolers,
  gauges, temperature/level devices, breathers, electric motors, couplings,
  shafts and pressure switches in the topology phase.
- Kept solenoid and mechanical-position actuation as properties of hydraulic
  valve classes rather than separate electrical/mechanical components.

### Designer and netlist prompts

- The designer now selects the smallest complete set of generic hydraulic
  functions only.
- Branches are represented by repeated direct endpoints. For example,
  `Pump.P -> ReliefValve.P` and `Pump.P -> DCV.P` share `Pump.P`; no tee or
  manifold is created.
- The netlist builder receives a normal-form example and must produce direct
  `Component.port -> Component.port` connections.
- The one-way speed-control classes include their reverse check internally, so a
  second check valve is not added merely to model free reverse flow.
- The reviewer is prohibited from failing or warning about sizing, ratings,
  dimensions, lines, filtration, cooling, power or exact procurement choices.

### Validation

- Preserved strict checks for unique ids, exact generic keys, legal ports,
  complete port use, required functional inventory, pump suction, relief path,
  DCV pressure/return, actuator control paths, sequencing/holding drivers and
  function traceability.
- Added deterministic rejection of accessory and sizing-stage component types.
- Removed all pressure-rating, flow-rating, pump-delivery, cylinder stroke and
  cylinder force-capacity checks.
- Catalog gaps block validation only when they describe a missing topology
  function; sizing gaps are deferred.
- A rigid two-cylinder platen can satisfy synchronization by documenting the
  shared rigid structure, without inventing a flow divider or mechanical shaft
  component.

### Output and schemas

- Removed manufacturer, part number, source URL and sizing-verification fields
  from final components.
- The terminal now shows the generic class table and a prominent direct
  connection table in `Component.port -> Component.port` form.
- The final scope statement explicitly says that pump/cylinder/reservoir sizing,
  ratings, lines, filtration, cooling, prime mover, simulation and fabrication
  decisions are deferred.

### Documentation and tests

- Updated `README.md`, `docs/ARCHITECTURE.md` and `docs/FLOW.md` to match the new
  phase boundary.
- Reworked validation fixtures around the requested Problem 1 normal form.
- Added regression tests proving that shared-port branches require no manifold,
  accessory types are rejected, catalog data contain no sizing/procurement
  fields, and extreme sizing values do not change a topology verdict.
- Offline test result after the revision: **23 passed**.

## Problem 1 expected normal form

The functional component set is:

- Tank
- Pump
- Relief Valve
- 4/3 solenoid DCV (tandem center)
- Pressure-compensated one-way speed control
- Position-operated bypass/changeover
- Double-acting cylinder

The core connection schedule is:

```text
Tank.S -> Pump.S
Pump.P -> ReliefValve.P
ReliefValve.T -> Tank.R
Pump.P -> DCV.P
DCV.T -> Tank.R
DCV.A -> Cylinder.Cap
Cylinder.Rod -> FeedControl.A
FeedControl.B -> DCV.B
Cylinder.Rod -> PositionBypass.P
PositionBypass.A -> DCV.B
```

`Tank.S -> Pump.S` and `ReliefValve.T -> Tank.R` use the functional port
semantics in the new catalog. A pump has suction `S` and pressure `P`; the
relief valve returns from `T`, not from its pressure inlet `P`.

## Benchmark scope note

The repository's executable problem file contains seven prompts (`P7-01` through
`P7-07`), and the generic catalog covers their functional needs. The attached
PDF title/index says “10 application-only problems,” but the rendered 18-page
body contains complete material for Problems 1–3 followed by a different
Problems 4–7 set and no complete Problems 8–10. No absent reference solution was
embedded or guessed.
