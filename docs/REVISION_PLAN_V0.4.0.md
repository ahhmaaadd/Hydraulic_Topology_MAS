# Live-Run Failure Repair Plan — v0.4.0

## What this revision responds to

v0.3.0 was released against offline gates only. The eleven supplied LangSmith
traces are its first full live evidence, and they split cleanly:

| Problem | Status | Rounds | Blocking codes |
| --- | --- | --- | --- |
| P7-01 | `validated_with_warnings` | 1 | — |
| P7-02 | `validated_with_warnings` | 1 | — |
| P7-03 | `unresolved` | 4 | `MISSING_FUNCTION_TRACEABILITY`, review: pilot-check reseat |
| P7-04 | `unresolved` | 4 | `METERING_PATH_NOT_REALIZED`, `METERING_BYPASSED_IN_PHASE`, 2× `BLOCKING_EVIDENCE_GAP` |
| P7-05 | `unresolved` | 4 | `FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE` |
| P7-06 | `validated_with_warnings` | 1 | — |
| P7-07 | `unresolved` | 4 | `UNJUSTIFIED_POSITION_VALVE` |

Four further traces are short interactive runs that stopped at the clarification
interrupt and carry no topology.

The four failures are **not** four independent bugs. They are five root causes,
and one of them (the repair loop) is what turned each of the others from a
one-round correction into a four-round stall.

## Root causes and controls

### 1. The catalog could not express a venting neutral (P7-03)

Every 4/3 valve in the 21-class catalog blocked both work ports in neutral —
tandem centre unloads P to T but still blocks A and B, closed centre blocks
everything. A pilot-operated check or dual load lock only reseats when its pilot
pressure can decay to tank. With no venting centre available, the reviewer was
correct to refuse the circuit and the planner had nothing to replace it with, so
the loop proposed the same two valves for four rounds.

This is a genuine engineering finding, not a model error, and it applies to the
reference solutions as well: PDF Problem 3 pairs a closed-centre valve with a
dual pilot-operated lock block, and PDF Problem 2 pairs a closed centre with a
single pilot-operated check on the rod.

**Controls**

- New `GENERIC_4_3_SOLENOID_FLOAT_CENTER_DCV` — neutral blocks P, connects A, B
  and T. Correct with a pressure-compensated pump.
- New `GENERIC_4_3_SOLENOID_OPEN_CENTER_DCV` — neutral connects P, T, A and B.
  Correct with a fixed-displacement pump, since it unloads and vents at once.
- New `GENERIC_VENTED_PILOT_OPERATED_RELIEF_VALVE` — a vent port gives true
  single-pump unloading and staged decompression without a second pump.
- New deterministic check `LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD`: in any hold
  phase, every load lock's pilot port (or, for a cross-piloted dual lock, its
  valve-side ports) must have a directed path to tank in that phase's graph.
- The same pairing is rejected at candidate scoring, so the netlist builder is
  never asked to wire a hold it cannot prove.
- The `load_lock` pattern hint now states the pump/centre pairing explicitly.

### 2. The overlap rule had no concept of an end stop (P7-05)

`FORBIDDEN_FUNCTION_ACTIVE_IN_PHASE` fired because, during the clamp-release
phase, the drill's rod line was still connected to a pressurised work port. The
drill had finished retracting in the previous phase and was sitting against its
stop; no amount of pressure was going to move it further. The rule was a false
positive, and the only way the planner could satisfy it was to invent isolating
hardware — which then failed the minimality rules.

**Controls**

- `PhaseConfiguration.completed_function_ids` records functions already driven to
  an end position by an earlier phase.
- The overlap rule now computes *which direction* the residual path could drive
  the actuator and only subtracts directions that a declared, proven completion
  covers.
- The claim is verified against the operational sequence order, not the order
  functions happen to be listed in — a later clamp-release phase must see the
  drill retract that precedes it in the real cycle.
- A completion claim with no earlier phase to back it raises the new
  `UNPROVEN_COMPLETED_FUNCTION` error, so the field cannot be used to silence a
  real overlap.

### 3. A load-actuated speed change was built out of valves (P7-04)

The brief asks for a speed that falls when the load rises, with no electrical
sensing. That is what a plain non-compensated throttle does by itself: the flow
through a fixed restriction follows its pressure drop, and the pressure drop
collapses as the load pressure climbs. The planner instead assembled a hi-lo
two-pump supply, an unloading valve, two combining checks, a feed-enable
sequence valve, a rod-side throttle *and* a parallel bypass check — at which
point the bypass made the metered path irrelevant and validation correctly
reported `METERING_BYPASSED_IN_PHASE`.

**Controls**

- New pattern `<function>_load_actuated_speed_change_minimal`, emitted whenever a
  function has a `load_sensitive_throttled` phase and no pressure/position
  sequence step. It requires exactly one `one_way_flow_control` and explicitly
  forbids a parallel bypass, a feed-enable sequence valve and a second pump.
- Candidate scoring rejects two or more pumps without an energy-saving/hi-lo
  driver, and rejects plain check valves that serve neither pump combining nor
  regeneration.
- The component-planner prompt states the physics directly, so the reasoning is
  available before the candidate is built rather than only in the rejection.

### 4. The model decided for itself what counted as blocking (P7-04, P7-07)

`EvidenceGap.blocking` was trusted verbatim. Having been told "the class exists,
so record an evidence gap rather than a catalog gap", the designer re-emitted
the same complaint as a *blocking* evidence gap. That produced a `scope=research`
error, one targeted search that could not possibly return a schematic for this
exact machine, and then a stall.

**Controls**

- New `gap_policy.classify_evidence_gap` makes blocking a deterministic decision:
  a gap about missing citations, sources, manuals or schematics is never
  blocking, and a gap whose capability maps onto an available generic class is
  never blocking.
- The model's own `blocking` flag can now only ever downgrade, never promote.
- The downgrade reason is written into the issue description, so the audit trail
  shows why the gap stopped blocking.

### 5. The repair loop could not change its mind (all four)

`topology_no_progress` compared a fingerprint over the full error set *and* every
component and connection. Any cosmetic change — a moved valve, a renamed role,
one more component — produced a fresh fingerprint with an identical error set,
so the loop never detected that it was stuck. It then spent its whole budget
inside a scope that could not fix the fault: rewiring cannot repair a wrong
component choice, and reselecting components cannot repair a contradictory
requirement.

**Controls**

- New `_topology_error_signature` hashes only unresolved error codes and their
  subjects, ignoring components, connections and prose.
- When the same signature survives a repair round, `_ESCALATED_SCOPE` moves the
  repair one level outward: `wiring → selection → requirements`, and
  `research → selection` because an evidence complaint that outlives a targeted
  search is a design decision, not a missing document.
- The escalation and its reason are appended to the validation summary and
  retained in the saved run.

## Two latent errors found in the passing runs

Both P7-02 and P7-06 returned `validated_with_warnings`, but the traces contain
engineering faults the validator did not have a rule for.

**P7-02** selected a *non-compensated* rod-side meter-out for a press commanded
to hold 2 m/min through both a 12 kN stage and a 50 kN stage. A plain throttle
cannot hold one speed across a four-fold load change — that is the same physics
the P7-04 circuit relies on to *change* speed.

`requirements_quality._normalize_flow_compensation` now promotes a throttle to
pressure-compensated in two deterministic cases: a phase that declares its speed
load-independent, and two throttled phases of one function in the same direction
commanded to the same speed while the force differs by more than 25 %. The
normalization is recorded in `requirements_normalizations`, so the change is
visible rather than silent.

**P7-06** is correct as returned. It is worth noting that its tandem-centre 4/3
valve is *better* than the reference solution's 4/2 valve, which has no neutral
and therefore no way to stop the platen mid-stroke or unload the pump at the end
of stroke.

## Test and release gates

All v0.3.0 gates still apply. Added:

12. A blocked neutral paired with a load lock is rejected in a hold phase, for
    both the tandem and the closed centre, and both venting centres are accepted.
13. The same pairing is rejected at candidate scoring.
14. A proven end-of-stroke completion is accepted; an undeclared one still fails;
    an unproven claim raises `UNPROVEN_COMPLETED_FUNCTION`.
15. A hi-lo plan for a load-actuated brief is rejected, the minimal single-
    throttle plan is eligible, and the pattern carries all three prohibitions.
16. A position-operated isolator is rejected for a pressure-triggered release
    order, and the reverse-order pattern carries a concrete wiring recipe.
17. A model-declared blocking evidence gap about missing citations is downgraded
    and the topology validates.
18. One speed across a large load change is promoted to pressure-compensated; a
    small load change is left alone.
19. The error signature ignores cosmetic churn, is empty when only warnings
    remain, and the escalation map terminates at `requirements`.
20. The three new catalog classes are well formed, carry no sizing language, and
    both venting centres reach tank from A and B while only the open centre
    unloads P.

84 offline tests pass. Live model and web runs remain probabilistic, but each of
the five root causes is now either impossible to express, rejected
deterministically, or escalated out of the scope that could not repair it.
