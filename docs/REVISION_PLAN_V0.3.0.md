# Complete-Circuit Reliability Plan — v0.3.0

## Goal and validation boundary

The workflow must return the smallest complete generic hydraulic circuit that
implements the requested behavior and print it as direct
`Component.port -> Component.port` connections. It must not select ratings,
flows, bores, strokes, line sizes, filters, coolers, manifolds, motors or other
sizing/layout hardware. A result may be declared valid only when deterministic
phase graphs prove the required supply, exhaust/recirculation, metering, load
control, sequence and synchronization behavior.

## Failure-to-control matrix

| Observed run | Root cause | Implemented control | Acceptance evidence |
| --- | --- | --- | --- |
| P7-01 | A missing exact combined source was encoded as a blocking catalog gap; numeric phase targets also encouraged extra controls | Separate `EvidenceGap` from true `CatalogGap`; allow composition of supported sub-patterns; typed `speed_realization`; minimality scoring | Available class cannot block as a catalog gap; position rapid/feed pattern carries one metering path plus parallel bypass |
| P7-02 | Repair closed the rapid bypass in every phase and destroyed the two-speed descent | `unrestricted_rapid` vs throttled decisions; rapid/feed pattern requires bypass open before trip and closed during feed; phase validator rejects forced metering in an unrestricted phase | Open/closed phase-state invariant and no unmetered path during feed |
| P7-03 | Operator commands were incorrectly marked as hydraulically enforced; the component planner could not repair an upstream requirement | Deterministic command/sequence normalization; requirements-scoped topology errors route upstream once; numeric directional speeds default to sizing only; unjustified flow controls/sequence valves are rejected | External-command steps cannot demand a hydraulic trigger; minimal holding circuit wins candidate selection |
| P7-04 | Validator required every moving chamber to exhaust to tank and could not recognize regeneration; designer overcomplicated a passive load-dependent speed change | Regenerative chamber-to-chamber recirculation proof; `load_sensitive_throttled` pattern; regeneration allowed only when explicit | Regenerative fixture validates without a tank exhaust; passive throttling uses one non-compensated control |
| P7-05 | Correct sequence was surrounded by extra gates/checks/flow controls | Typed sizing-only speed decisions, pressure-sequence pattern, component usefulness checks and stronger minimality penalty | Sequence valves require pressure triggers; flow controls require a typed throttled phase; deletion remains executable |
| P7-06 | Earlier system could choose series cylinders despite an explicit rigid platen | Deterministic `rigid_platen_parallel` rule plus shared cap/rod branch proof; explicit series remains supported only with matched displacement | Rigid platen rejects all chamber-to-chamber links; explicit matched series has a separate valid regression |
| P7-07 | User's load-character answer was visible to extraction but not to the critic; regenerated question ids caused a clarification loop; lower clamp pressure was not first-class | Structured clarification records, semantic topics/keys, authoritative answer context in extractor/critic/repair, repeated-question suppression, `max_working_pressure`, branch pressure-reduction rule | New question id for the answered topic is suppressed; lower branch ceiling requires a pressure-reducing valve |

## Implementation workstreams

### 1. Requirements contract

- Add mandatory `speed_realization` to every motion phase and propagate it in
  `MotionControlDecision`.
- Keep `metering_side`, chamber, flow, compensation and load control typed.
- Treat a numeric speed alone as `sizing_only`.
- Require a topology realization when one actuator has distinct speeds in the
  same motion direction.
- Add per-function `max_working_pressure` plus
  `branch_pressure_limit_required` for independently protected lower-pressure
  branches, without confusing ordinary sizing pressure targets with valve needs.
- Add semantic clarification topics and retain question, topic, related ids and
  answer as an authoritative record.
- Normalize initial/external/simultaneous commands so they cannot be mistaken
  for hydraulic automatic transitions.

### 2. Generic pattern library

Generate problem-independent hints from typed requirements for:

- open-circuit tank/pump/relief supply;
- sizing-only direct motion;
- pressure-compensated or load-sensitive metering;
- position-operated rapid/feed bypass;
- regenerative extension;
- pilot-check holding and counterbalance control;
- rigid-platen parallel, divider-parallel and explicit-series synchronization;
- lower-pressure branches; and
- pressure-triggered multi-function sequencing.

Each hint contains required generic types, connection rules, per-phase state
rules and prohibited substitutions. The library contains no problem ids and no
benchmark answer map. Hints are supplied to research, synthesis, design brief,
component planning, netlist construction and review.

### 3. Catalog and evidence policy

- `CatalogGap` is reserved for an actually absent topology-changing generic
  class and remains blocking.
- `EvidenceGap` records missing corroboration for a plausible available pattern.
- Deterministic policy downgrades a model-created catalog gap when the requested
  class/capability is already in the catalog or its reason is missing
  documentation/citations.
- Exact pre-existing schematics are not required; supported component and
  connection principles may be composed.

### 4. Candidate selection and minimality

- Reject a flow-control component when no phase has a throttled speed
  realization.
- Require the compensation class implied by the typed realization.
- Penalize more controls than distinct metering paths.
- Reject sequence and position valves without matching triggers.
- Require pressure reduction when a function ceiling is below the global
  system ceiling.
- Continue to reject sequence-as-unloader, sequence-as-check,
  pilot-check-as-counterbalance, series rigid-platen plumbing and all
  accessories.
- Keep executable add/delete/replace repair actions and structured-output
  recovery.

### 5. Directed validator

- Carry speed realization in the immutable decision signature.
- For regeneration, accept a directed exhaust-chamber to supply-chamber path in
  place of tank exhaust; reject a declared regenerative phase without it.
- For explicitly matched hydraulic series, add phase-local actuator
  displacement edges so every chamber is recognized as controlled. Never add
  those edges for an ordinary or rigid-parallel circuit.
- Check unjustified controls and lower-pressure branches deterministically.
- Treat only hydraulically enforced sequence steps as requiring a hydraulic
  trigger/state transition.

### 6. Repair routing and termination

- Requirements errors route to a single upstream requirements repair, not the
  component planner.
- Wiring errors return to the netlist builder; selection/safety errors return to
  the catalog planner; research errors receive at most one targeted search for
  the same validation fingerprint.
- Hash the error set, component keys, connections and phase states after every
  validation. If an identical invalid fingerprint repeats, stop with an
  explicit no-progress reason instead of spending all four rounds on the same
  topology.

### 7. Terminal and saved output

- Print `speed_realization` beside every phase decision.
- Print catalog and evidence gaps separately.
- Print the repair stop reason and retain validation fingerprints in final JSON.
- Preserve requirements, topology and all diagnostics in failed-run snapshots.

## Test and release gates

1. Compile every package module.
2. Run all unit and offline graph tests.
3. Prove the original position-bypass normal form remains valid.
4. Prove numeric-speed-only plans do not require flow controls.
5. Prove a repeated clarification topic with a new id does not block.
6. Prove command logic cannot become phantom hydraulic sequencing.
7. Prove explicit matched series and regenerative recirculation pass.
8. Prove lower branch pressure requires pressure reduction.
9. Prove a real missing class blocks while an available/missing-evidence class
   does not.
10. Prove an identical invalid topology exits on no progress.
11. Validate one canonical direct-port circuit for every P7-01 through P7-07
    requirement family (fixtures remain test-only).

The release is packaged only after all offline gates pass. Live model/web runs
remain probabilistic, but every known failure mode is now either prevented by a
typed contract, rejected by deterministic validation, or stopped with a useful
controlled diagnostic.
