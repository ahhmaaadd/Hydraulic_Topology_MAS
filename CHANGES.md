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
