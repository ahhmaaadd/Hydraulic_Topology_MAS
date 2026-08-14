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
