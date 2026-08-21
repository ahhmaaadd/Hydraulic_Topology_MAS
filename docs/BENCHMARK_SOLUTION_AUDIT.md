# Hydraulic Circuit Design Benchmark — Solution Audit and Corrected Reference Designs

Companion to *Hydraulic Circuit Design Benchmark Pack* and to
`Hydraulic_Topology_MAS` v0.4.0. Every figure below was recomputed from the
problem statements using the pack's own common design basis:
η_m = 0.90, η_v = 0.90, η_overall = 0.85, 1500 rpm, 5 bar circuit loss on the
linear systems, 4 bar on the small clamp/drill system, valves ≥ 125 % and
filters ≥ 150 % of maximum flow, reservoir = 3 × delivered pump flow, line
velocities 1 / 4 / 3 m/s for suction / pressure / return.

---

## Part A — Audit summary

| # | Application | Mathematics | Topology | Verdict |
| --- | --- | --- | --- | --- |
| 1 | Two-stage machine-tool slide | 1 error | correct | **Correct apart from prime-mover sizing** |
| 2 | Three-stage production press | correct | 1 error | **Needs a valve-centre change** |
| 3 | Bidirectional positioning fixture | 1 error | 1 error | **Needs a valve-centre change** |
| 4 | Load-dependent machine slide | 3 errors | 1 error | **Not achievable as published** |
| 5 | CNC clamping and drilling | 2 errors | correct | **Both cylinders undersized** |
| 6 | Synchronized wide platen | 1 error | 1 weakness | **Cannot develop the stated load** |
| 7 | Clamp-then-work station | 2 errors | 1 error | **Both cylinders undersized; order not hydraulic** |

Problems 1, 2, 3 and 6 are close to right and need targeted corrections.
Problems 4, 5 and 7 need new component sizes before their circuits work at all.

### Document-level defects

These are worth fixing before the pack is used for scoring, because they change
what a system is being asked to do.

**A1. The problem index on page 2 does not describe Problems 4–7.**
The index lists *load-triggered automatic feed change*, *large forming press with
controlled decompression*, *energy-saving high-low forging press* and *hydraulic
clamp-then-drill workstation*. The document actually contains *load-dependent
machine-slide speed*, *CNC clamping and drilling*, *synchronized wide platen* and
*clamp-then-work machine station*. The index also promises ten problems; seven
are present.

**A2. Every equation in Problems 4–7 failed to render.**
Problem 6 reads "Each actuator carries:", "Using a nominal 70 bar design
pressure:", "Actual extension pressure:", "Extension velocity:", "Total pump
flow:", "Return velocity:", "Return pressure:" — each followed by nothing.
Problem 5 has "Reservoir: approximately , therefore about 75 L" and
"Pump-drive requirement:" with no value. Problem 7 has the same gaps. Problems
1–3 render correctly, so this is confined to the second half of the pack.

**A3. Problems 4–7 have no port-to-port connection schedule and no schematic.**
Problems 1–3 each carry a component table, a connection schedule and a figure.
Problems 4–7 carry a prose paragraph. Since the stated purpose is to score
"connectivity", the second half cannot currently be scored the way the first
half can. Part B supplies the missing schedules.

**A4. Efficiency is applied inconsistently.**
Problems 1, 2 and 3 divide by η_m = 0.90 throughout. Problems 4, 5, 6 and 7 do
not. That single inconsistency is the direct cause of four of the sizing errors
below, because in each case the load pressure computed without efficiency sits
just under a stated ceiling and the true pressure sits just over it.

---

## Part B — Findings and corrections, problem by problem

### Problem 1 — Two-stage machine-tool slide

**Everything checks out except the prime mover.**

Areas, pressures and flows all reproduce exactly: A_cap = 7854 mm²,
A_ann = 5027 mm², approach 21.22 bar and 23.56 L/min, cutting 49.51 bar and
3.93–7.85 L/min, retract 21.11 L/min in with 32.99 L/min out, displacement
18.52 cm³/rev. Valve rating 41.2 L/min and return filtration 49.5 L/min are
correctly based on the amplified cap return rather than pump flow. Line IDs
23.0 / 11.5 / 15.3 mm are right. The topology is right, and the position-operated
bypass around a pressure-compensated meter-out control is the correct answer to
the ±15 % load-variation clause.

**B1.1 — The 1.5 kW prime mover is undersized by a factor of two.**

P1 is specified as a *fixed-displacement* pump delivering 25 L/min, and DV1 is a
tandem-centre valve. During the cutting feed the cylinder accepts at most
7.85 L/min, so 17.15 L/min discharges over the 65 bar relief valve, and the pump
is still turning against 65 bar:

```
Shaft power during cutting = 25 L/min x 65 bar / 0.85 = 3.19 kW
```

The pack's 1.21 kW is exactly 23.56 L/min × 26.2 bar / 0.85 — the *approach*
phase computed as though the pump could destroke. That is the figure for a
pressure-compensated pump, not for the fixed pump the component table specifies.

**Correction — take either route:**

*Preferred.* Change P1 to a pressure-compensated variable pump, 0–25 L/min,
compensator 65 bar, and change DV1 to a closed centre (there is no load-holding
requirement here, so no venting centre is needed). Worst phase becomes the
approach at 23.56 L/min × 26.2 bar / 0.85 = **1.21 kW**, and the published
1.5 kW prime mover is then correct as stated. Cutting-phase power drops to
7.85 L/min × 54.5 bar / 0.85 = 0.84 kW.

*Alternative.* Keep the fixed pump and the tandem centre and raise PM1 to
**4.0 kW**. Note that 2.35 kW is then dissipated as heat through the relief valve
for the whole cutting stroke, which drives a much larger reservoir or a cooler.

Everything else in Problem 1 stands.

---

### Problem 2 — Three-stage production press

**The arithmetic is right; the neutral is not.**

Confirmed: 16.98 bar at 12 kN and 70.74 bar at 50 kN; 39.27 L/min fast down,
15.71 L/min at 2 m/min with 10.05 L/min rod exhaust; return in 7.68 s with
61.4 L/min cap discharge; 29.63 cm³/rev at 40 L/min; 2.33 kW pressing power with
a compensated pump, so the 3.0 kW prime mover is correct; 120 L reservoir.

**B2.1 — A closed-centre valve cannot hold PC1 closed for ten minutes.**

The circuit uses a single pilot-operated check PC1 on the rod line to retain the
ram at top, piloted from the cap line, with DV1 centred. DV1 is specified as a
**closed centre**, which blocks the cap line. The oil trapped between DV1 port A
and PC1's pilot has nowhere to go, so it can hold PC1 cracked open and the ram
creeps down. This is precisely the failure mode a load lock exists to prevent.

**Correction.** Change DV1 to a **four-way, three-position float centre**: P
blocked, A and B both connected to T. In neutral the cap line and the pilot
decay to tank, PC1 reseats positively, and the pressure-compensated pump
destrokes against the blocked P port — which is also what "power unit unloaded"
in the problem statement asks for. Rating unchanged at ≥ 100 bar and 77 L/min.

Replace this row in the connection schedule:

| From | Port/path | To | Purpose |
| --- | --- | --- | --- |
| DV1 | Neutral A and B | Return manifold | Vents the cap line and the PC1 pilot so the lock reseats |

**B2.2 — The relief margin is thinner than it looks.**

FC1 is a pressure-compensated control and needs roughly 8–10 bar across it to
regulate. Carrying 10 bar of rod backpressure:

```
p_cap = (50 000 / 0.90 + 10 bar x 5027 mm2) / 7854 mm2 = 77.1 bar
+ 5 bar circuit loss = 82.1 bar at the pump, against an 85 bar relief setting
```

2.9 bar of margin will make the relief simmer during every pressing stroke.
**Raise RV1 to 90 bar**, still comfortably inside the 100 bar problem limit.

---

### Problem 3 — Bidirectional positioning fixture

**Confirmed:** A_cap = 3117 mm², A_ann = 1861 mm²; 53.47 bar advancing and
56.73 bar returning; 10.91 L/min advance, 10.23 L/min return with 17.14 L/min cap
exhaust; 8.89 cm³/rev; 36 L reservoir rounded to 40 L; 16 / 10 / 12 mm lines. The
dual cross-piloted lock block at the actuator ports is the right answer to
"hold for 10 minutes with negligible drift".

**B3.1 — Same neutral defect as Problem 2, and here it is the whole point.**

DV1 is specified as a **closed centre** and LC1 is a dual pilot-operated lock.
Both work ports are blocked in neutral, so neither cross-pilot can decay. The
one requirement this problem exists to test — a ten-minute drift-free hold — is
the one the specified valve centre cannot deliver.

**Correction.** Change DV1 to a **four-way, three-position float centre** (P
blocked; A, B and T interconnected), ≥ 100 bar and 22 L/min. The pump is already
pressure-compensated, so blocking P in neutral is correct: the pump destrokes to
standby, both work lines vent, and both load checks seat. Releasing the command
then does place the system in the safe neutral condition the problem asks for.

**B3.2 — The prime mover is understated.**

The pack's 1.24 kW is 12 L/min × 61.7 bar = 1.234 kW, which is *hydraulic* power.
Applying the pack's own η_overall = 0.85:

```
Shaft power = 12 L/min x 61.7 bar / 0.85 = 1.45 kW
```

A 1.5 kW motor has 3 % headroom. **Specify 2.2 kW.**

**B3.3 — The compensator setting is too close to the working pressure.**

The component table gives a 65 bar compensator, but the return stroke needs
61.7 bar at the pump. The pump would begin destroking during normal return.
**Set the compensator to 70 bar and RV1 to 80 bar**, both inside the 100 bar
limit.

---

### Problem 4 — Load-dependent machine slide

This solution does not work as published. Three of its numbers and one of its
components have to change.

**B4.1 — Efficiency is missing.** 3.18 bar and 15.92 bar are F/A with no η_m.
With the pack's η_m = 0.90 they are **3.54 bar and 17.68 bar**. Against an 18 bar
relief setting, the 12.5 kN load alone now needs 17.68 bar, leaving 0.32 bar for
everything else.

**B4.2 — A 100/50 cylinder cannot produce the required 3:1 speed ratio inside
20 bar.** This is the substantive error. Solve the meter-out equilibrium rather
than treating the two operating points as independent. With
A_cap = 7854 mm², A_ann = 5890 mm² and the relief at 18 bar:

```
Working feed  p_cap = 18 bar (relief), p_rod = (18 bar x 7854 - 12 500 N) / 5890 = 2.78 bar
              Q_rod = 2.945 L/min  ->  throttle coefficient k = 1.766 L/min/sqrt(bar)
Approach      8.84 L/min through the same fixed orifice needs p_rod = 25.05 bar
              which needs p_cap = 21.97 bar  ->  ABOVE the 20 bar problem limit
```

Clamped at the 18 bar relief, the approach actually settles at
**1.33 m/min, not 1.5** — and it does so with the relief valve wide open for the
whole approach stroke, which the design never intends.

**B4.3 — A closed-centre valve deadheads the fixed pump.** With a fixed
displacement pump, a closed centre puts the full 11.78 L/min over the relief at
18 bar every time the slide stops. Use a **tandem centre**.

**B4.4 — 0.37 kW is hydraulic power.** 11.78 L/min × 18 bar = 0.353 kW
hydraulic; ÷ 0.85 = 0.42 kW shaft.

#### Corrected reference solution — Problem 4

The fix is to raise the area ratio. A larger rod makes the rod-side pressure far
more sensitive to load, which is exactly the effect the circuit trades on.

**Actuator sizing**

```
Select 100 mm bore, 70 mm rod, 300 mm stroke.
A_cap = 7854 mm2 ; A_ann = 4006 mm2 ; ratio = 1.961 (was 1.333)
Load pressures with eta_m = 0.90:
  2.5 kN  -> 3.54 bar      12.5 kN -> 17.68 bar
```

**Relief setting and throttle**

```
RV1 = 19 bar (below the 20 bar problem limit, above the 17.68 bar stall pressure)

Working feed  p_cap = 19 bar (relief), p_rod = (19 bar x 7854 - 13 889 N) / 4006 = 2.58 bar
              Q_rod = 2.00 L/min  ->  k = 1.247 L/min/sqrt(bar)  ->  0.500 m/min   exact
Approach      6.01 L/min through the same orifice needs p_rod = 23.23 bar
              -> p_cap = 15.38 bar, below the relief setting, so the relief stays shut
              -> Q_cap = 11.78 L/min = full pump flow  ->  1.500 m/min   exact
```

Both target speeds are met exactly with one fixed restriction, no relief loss
during the approach, and the load change alone drives the transition.

**Flow, power and auxiliaries**

```
Pump 11.78 L/min ; D = 8.73 cm3/rev at 1500 rpm, eta_v = 0.90
Return: 11.78 L/min into the annulus -> 2.94 m/min, 300 mm in 6.1 s, 23.10 L/min cap discharge
Return pressure 6.93 bar at the rod side
Shaft power = 11.78 L/min x 19 bar / 0.85 = 0.44 kW  ->  0.55 kW prime mover
Reservoir = 3 x 11.78 = 35.3 L
Valve rating >= 1.25 x 23.10 = 28.9 L/min ; return filtration >= 1.5 x 23.10 = 34.7 L/min
Lines: 16 mm suction, 8 mm pressure, 13 mm return
```

**Selected generic components**

| ID | Component | Value | Minimum rating |
| --- | --- | --- | --- |
| CY1 | Double-acting cylinder | 100/70 mm, 300 mm stroke | ≥ 20 bar |
| P1 | Fixed-displacement pump | 11.78 L/min, 8.73 cm³/rev | ≥ 19 bar |
| PM1 | Prime mover | 0.55 kW | Continuous duty |
| RV1 | Main relief valve | 19 bar | ≥ 20 bar, ≥ 12 L/min |
| DV1 | 4/3 DCV, **tandem centre** | Extend / neutral-unload / retract | ≥ 20 bar, ≥ 28.9 L/min |
| FC1 | **Non-compensated** one-way flow control | 2.00–6.01 L/min metered, free reverse | ≥ 20 bar |
| T1 | Reservoir | 35.3 L minimum | Breather, level and temperature indication |

**Port-to-port connection schedule**

| From | Port | To | Port | Purpose |
| --- | --- | --- | --- | --- |
| T1 | Tank outlet | P1 | Suction | Suction supply |
| P1 | Pressure outlet | RV1 | P | Overpressure protection |
| P1 | Pressure outlet | DV1 | P | Main supply |
| RV1 | T | T1 | Return | Relief return |
| DV1 | T | T1 | Return | Main return |
| DV1 | A | CY1 | Cap | Extend supply, retract exhaust |
| CY1 | Rod | FC1 | A | Metered rod exhaust (meter-out) |
| FC1 | B | DV1 | B | Controlled exhaust to valve |

Eight lines, seven components. No bypass valve, no sequence valve, no second
pump: the speed change is a property of the fixed restriction, not of a valve
that switches.

**Phase states**

| Phase | DV1 | Behaviour |
| --- | --- | --- |
| Approach, 2.5 kN | extend | p_cap 15.4 bar, relief shut, 11.78 L/min to the cap, 1.5 m/min |
| Working feed, 12.5 kN | extend | p_cap at the 19 bar relief, p_rod collapses to 2.58 bar, 0.5 m/min |
| Return, 2.5 kN | retract | Free reverse through FC1's integral check, 2.94 m/min |
| Stopped | neutral | Pump unloaded P to T |

**Acceptance check.** 1.500 m/min approach and 0.500 m/min feed are met exactly;
peak pressure 19 bar is inside the 20 bar limit; the transition is produced by
the load alone with no electrical pressure switch; the return completes 300 mm in
6.1 s.

---

### Problem 5 — CNC clamping and drilling

The circuit concept — pilot-operated check holding the clamp, forward sequence
valve gating the drill, reverse sequence valve enforcing drill-before-clamp on
return — is correct and matches the source. Both cylinders are the wrong size.

**B5.1 — The 63 mm drill cylinder cannot develop 2500 N inside 8 bar.**

```
2500 N / 3117 mm2 = 8.02 bar with no efficiency at all
2500 N / (3117 mm2 x 0.90) = 8.91 bar with eta_m = 0.90
```

The system ceiling is 8 bar. The specified cylinder is over the limit before any
line loss or meter-out backpressure is counted. Since the drill branch carries
only a fraction of a litre per minute its line loss is negligible, but even at
zero loss the cylinder cannot stall at the required force.

**B5.2 — The 125 mm clamp bore contradicts the 3.3 bar transition threshold.**

The problem fixes the drilling transition at a clamping pressure of ~3.3 bar. A
sequence valve set at 3.3 bar must open only *after* full clamping force exists.
With a 125 mm bore, 4000 N needs 3.62 bar (η_m = 0.90) — the sequence valve opens
at 3.3 bar, i.e. *before* the clamp has developed its force. The interlock fails
in the unsafe direction: drilling starts on an under-clamped workpiece.

**B5.3 — Reservoir.** "approximately , therefore about 75 L" has a hole in it.
3 × 18.4 = 55.2 L, not 75 L.

#### Corrected reference solution — Problem 5

**Actuator sizing**

```
Clamp: select 140 mm bore, 70 mm rod, 20 mm stroke.
  A_cap = 15 394 mm2 ; A_ann = 11 545 mm2
  4000 N needs 4000 / (15 394 x 0.90) = 2.89 bar
  At the 3.3 bar sequence threshold the clamp delivers 3.3 bar x 15 394 x 0.90 = 4572 N
  Full force is therefore established below the threshold, with 14 % margin.

Drill: select 80 mm bore, 45 mm rod, 100 mm stroke.
  A_cap = 5027 mm2 ; A_ann = 3436 mm2
  2500 N with 1.0 bar meter-out backpressure:
  p_cap = (2500 / 0.90 + 1.0 bar x 3436 mm2) / 5027 mm2 = 6.21 bar, inside the 8 bar ceiling.
```

**Flow and time**

```
Clamp 20 mm at 1.5 m/min -> 23.09 L/min, 0.80 s
Drill 100 mm at 0.1 m/min -> 0.503 L/min cap, 0.344 L/min rod exhaust, 60 s
Clamp retract with 23.1 L/min into the annulus -> 2.00 m/min, 30.8 L/min cap discharge
Pump 23.1 L/min ; D = 17.1 cm3/rev at 1500 rpm, eta_v = 0.90
Shaft power = 23.1 L/min x 8 bar / 0.85 = 0.36 kW  ->  0.55 kW prime mover
Reservoir = 3 x 23.1 = 69 L, specify 70 L
```

The drill's 0.34 L/min meter-out against a 23.1 L/min pump is a 67:1 turndown,
so the surplus crosses the relief valve throughout the one-minute drilling
stroke. At 8 bar that is only 0.3 kW of heat, which the 70 L reservoir absorbs.

**Selected generic components**

| ID | Component | Value | Minimum rating |
| --- | --- | --- | --- |
| CY1 | Clamp cylinder | 140/70 mm, 20 mm stroke | ≥ 8 bar |
| CY2 | Drill cylinder | 80/45 mm, 100 mm stroke | ≥ 8 bar |
| P1 | Fixed-displacement pump | 23.1 L/min, 17.1 cm³/rev | ≥ 8 bar |
| PM1 | Prime mover | 0.55 kW | Continuous duty |
| RV1 | Main relief valve | 8 bar | ≥ 8 bar, ≥ 29 L/min |
| DV1 | 4/3 DCV, tandem centre | Forward / neutral / reverse | ≥ 8 bar, ≥ 39 L/min |
| SQ1 | Forward sequence valve, integral reverse check | **3.3 bar** | ≥ 8 bar, ≥ 29 L/min |
| SQ2 | Reverse sequence valve, integral reverse check | **5 bar** | ≥ 8 bar, ≥ 29 L/min |
| PC1 | Pilot-operated check, external pilot | Clamp cap line | ≥ 8 bar |
| FC1 | Non-compensated one-way flow control | 0.344 L/min metered, free reverse | ≥ 8 bar |
| T1 | Reservoir | 70 L | Breather, level and temperature indication |

**Port-to-port connection schedule**

| From | Port | To | Port | Purpose |
| --- | --- | --- | --- | --- |
| T1 | Tank outlet | P1 | Suction | Suction supply |
| P1 | Pressure outlet | RV1 | P | Overpressure protection |
| P1 | Pressure outlet | DV1 | P | Main supply |
| RV1 | T | T1 | Return | Relief return |
| DV1 | T | T1 | Return | Main return |
| DV1 | A | PC1 | V | Clamp supply through the load lock |
| PC1 | C | CY1 | Cap | Locked clamp line |
| DV1 | A | SQ1 | P | Forward sequence pilot and supply |
| SQ1 | A | CY2 | Cap | Drill feed supply, gated at 3.3 bar |
| SQ1 | T | T1 | Return | Sequence drain |
| CY2 | Rod | FC1 | A | Metered drill exhaust (meter-out) |
| FC1 | B | DV1 | B | Controlled drill exhaust to valve |
| DV1 | B | SQ2 | P | Reverse sequence supply |
| SQ2 | A | CY1 | Rod | Clamp release, gated at 5 bar |
| SQ2 | A | PC1 | X | Pilot release for the clamp load lock |
| SQ2 | T | T1 | Return | Sequence drain |

**Operating sequence**

| Phase | DV1 | SQ1 | SQ2 | Behaviour |
| --- | --- | --- | --- | --- |
| Clamp advance | forward | closed | closed | Clamp travels 20 mm in 0.80 s; drill blocked |
| Drilling | forward | **open** | closed | Clamp pressure passes 3.3 bar with full force established; drill feeds at 0.1 m/min through FC1; PC1 holds the clamp |
| Drill retract | reverse | open | closed | Drill returns through FC1's free reverse; clamp still locked by PC1 |
| Clamp release | reverse | closed | **open** | Drill at its retracted stop, its line dead-ended; retract pressure rises past 5 bar, SQ2 opens, pilots PC1 open and releases the clamp |

**Acceptance check.** The clamp develops 4572 N ≥ 4000 N at the 3.3 bar
threshold, so the interlock now fires in the safe direction. The drill develops
2500 N at 6.21 bar, inside the 8 bar ceiling. Ordering in both directions is
hydraulic; no electrical sensing device appears anywhere.

---

### Problem 6 — Synchronized wide platen

**Confirmed:** 37.41 L/min at 0.1 m/s with two 63/35 cylinders; 54.11 L/min total
cap discharge on return; 112–150 L reservoir; 5.13 kW at the stated 70 bar. The
mechanical-synchronization topology — both caps paralleled, both rods paralleled,
rigid platen doing the synchronizing — is correct and is the right answer for an
explicitly rigid structure.

**B6.1 — The circuit cannot develop 40 kN. The relief is set below the working
pressure.**

```
20 kN per cylinder on a 63 mm bore = 20 000 / (3117 mm2 x 0.90) = 71.29 bar
+ 5 bar circuit loss = 76.29 bar required at the pump
Specified relief setting: 70 bar
```

The relief opens 6 bar before the platen reaches load. The pack reaches 64.16 bar
by omitting η_m, which slips just under the 70 bar figure. Raising the relief to
80 bar does not rescue it either — 76.29 bar against an 80 bar relief and an
80 bar problem ceiling leaves no margin at all.

**Correction — go up one bore size.**

```
Select two 70 mm bore, 40 mm rod, 500 mm stroke cylinders.
A_cap = 3848 mm2 ; A_ann = 2592 mm2 per cylinder
20 kN each -> 57.74 bar ; + 5 bar loss = 62.74 bar at the pump
RV1 = 75 bar, inside the 80 bar problem limit, 12 bar of working margin

Extend 500 mm in 5 s -> 0.1 m/s -> 2 x 3848 x 0.1 = 46.18 L/min total
Rod exhaust on extend = 31.10 L/min total
Return with the same 46.18 L/min into two annuli -> 0.1485 m/s -> 500 mm in 3.37 s
Cap discharge on return = 68.57 L/min total
Return pressure, 6 kN per cylinder on the annulus = 25.72 bar
Pump D = 34.21 cm3/rev at 1500 rpm, eta_v = 0.90
Shaft power = 46.18 L/min x 62.74 bar / 0.85 = 5.68 kW  ->  7.5 kW prime mover
Reservoir = 3 x 46.18 = 139 L
Valve rating >= 1.25 x 68.57 = 85.7 L/min ; return filtration >= 1.5 x 68.57 = 103 L/min
Lines: 31 mm suction, 16 mm pressure, 22 mm return
```

**B6.2 — Use a 4/3 tandem centre, not a 4/2.**

A two-position valve has no neutral, so the platen cannot be stopped mid-stroke
and the fixed pump has no unload path: whenever the platen sits at either end of
stroke, the full 46.18 L/min crosses the relief at 75 bar, which is 6.8 kW of
heat. A tandem centre gives a neutral and unloads P to T. This is not a
correctness failure of the published circuit, but it is a change worth making,
and it is what the multi-agent system selected unprompted.

**Port-to-port connection schedule**

| From | Port | To | Port | Purpose |
| --- | --- | --- | --- | --- |
| T1 | Tank outlet | P1 | Suction | Suction supply |
| P1 | Pressure outlet | RV1 | P | Overpressure protection |
| P1 | Pressure outlet | DV1 | P | Main supply |
| RV1 | T | T1 | Return | Relief return |
| DV1 | T | T1 | Return | Main return and neutral unloading |
| DV1 | A | CY1 | Cap | Left cap, parallel branch |
| DV1 | A | CY2 | Cap | Right cap, parallel branch |
| DV1 | B | CY1 | Rod | Left rod, parallel branch |
| DV1 | B | CY2 | Rod | Right rod, parallel branch |
| CY1 | Rod eye | Platen | Rigid joint | Mechanical synchronization |
| CY2 | Rod eye | Platen | Rigid joint | Mechanical synchronization |

No flow divider. A divider fights the rigid structure and would force the two
cylinders into a redundant constraint.

**Acceptance check.** 40 kN at 62.74 bar pump pressure, inside the 80 bar limit
with 75 bar relief; 500 mm in 5.0 s extending and 3.37 s returning; both sides
synchronized by the platen itself.

---

### Problem 7 — Clamp-then-work machine station

**B7.1 — The 63 mm clamp cylinder exceeds the 40 bar clamping ceiling.**

```
12 kN / (3117 mm2 x 0.90) = 42.77 bar, against a stated 40 bar limit
```
The pack's 38.50 bar omits η_m.

**B7.2 — The 80 mm work cylinder exceeds the 60 bar work ceiling and then the
70 bar system ceiling.**

```
30 kN / (5027 mm2 x 0.90) = 66.31 bar, against a stated 60 bar limit
+ 5 bar circuit loss = 71.31 bar at the pump, against a stated 70 bar limit
```
The pack's 59.68 bar omits η_m and slips just under 60.

**B7.3 — The release order is electrical, not hydraulic.**

The published solution uses two solenoid directional valves G and H and states
"directional valve H is reversed first; after the work cylinder retracts,
directional valve G releases the clamp". That is a sequence of two electrical
commands. The problem forbids an electrical pressure switch and requires that the
work motion retract *before* the clamp is released. Nothing in the hydraulics
prevents G from being energised first, so a controller fault, a miswire or a
manual override releases a clamped part while the work cylinder is still under
load. The forward direction is properly interlocked by the sequence valve; the
reverse direction is not interlocked at all.

#### Corrected reference solution — Problem 7

**Actuator sizing**

```
Clamp: 70 mm bore, 40 mm rod, 50 mm stroke.  A_cap = 3848 mm2
  12 kN -> 12 000 / (3848 x 0.90) = 34.65 bar, inside the 40 bar branch ceiling
Work:  90 mm bore, 50 mm rod, 200 mm stroke.  A_cap = 6362 mm2 ; A_ann = 4398 mm2
  30 kN -> 30 000 / (6362 x 0.90) = 52.40 bar, inside the 60 bar ceiling
  + 5 bar circuit loss = 57.40 bar at the pump, inside the 70 bar system ceiling
```

**Pressure settings**

```
Clamp branch pressure-reducing valve  : 40 bar   (clamp needs 34.65 bar, 15 % margin)
Forward sequence valve                : 45 bar   (above the reduced clamp branch, so it
                                                  can only open once the clamp has stalled)
Main relief                           : 65 bar   (above the 57.40 bar work demand,
                                                  below the 70 bar system ceiling)
Reverse sequence valve                : 45 bar   (above the work-retract running pressure,
                                                  reached only when the work cylinder bottoms)
```

**Flow, time and power**

```
Work 200 mm in 4 s -> 0.05 m/s -> Q_cap = 19.09 L/min ; rod exhaust 13.19 L/min
Clamp with the same 19.09 L/min -> 0.0827 m/s -> 50 mm in 0.60 s
Work retract: 19.09 L/min into the annulus -> 0.0723 m/s, 200 mm in 2.77 s,
              27.61 L/min cap discharge
Pump 19.09 L/min ; D = 14.14 cm3/rev at 1500 rpm, eta_v = 0.90
Shaft power = 19.09 L/min x 65 bar / 0.85 = 2.43 kW  ->  3.0 kW prime mover
Reservoir = 3 x 19.09 = 57 L, specify 60 L
Valve rating >= 1.25 x 27.61 = 34.5 L/min ; return filtration >= 1.5 x 27.61 = 41.4 L/min
```

**Selected generic components**

| ID | Component | Value | Minimum rating |
| --- | --- | --- | --- |
| CY1 | Clamp cylinder | 70/40 mm, 50 mm stroke | ≥ 40 bar |
| CY2 | Work cylinder | 90/50 mm, 200 mm stroke | ≥ 70 bar |
| P1 | Fixed-displacement pump | 19.09 L/min, 14.14 cm³/rev | ≥ 65 bar |
| PM1 | Prime mover | 3.0 kW | Continuous duty |
| RV1 | Main relief valve | 65 bar | ≥ 70 bar, ≥ 24 L/min |
| DV1 | **One** 4/3 DCV, tandem centre | Forward / neutral / reverse | ≥ 70 bar, ≥ 34.5 L/min |
| PR1 | Pressure-reducing valve, integral reverse check | 40 bar | ≥ 70 bar, ≥ 24 L/min |
| SQ1 | Forward sequence valve, integral reverse check | 45 bar | ≥ 70 bar, ≥ 24 L/min |
| SQ2 | Reverse sequence valve, integral reverse check | 45 bar | ≥ 70 bar, ≥ 24 L/min |
| PC1 | Pilot-operated check, external pilot | Clamp cap line | ≥ 70 bar |
| T1 | Reservoir | 60 L | Breather, level and temperature indication |

One directional valve replaces the published pair. With a single valve there is
no command order to get wrong: both the forward and the reverse order are carried
by sequence valves.

**Port-to-port connection schedule**

| From | Port | To | Port | Purpose |
| --- | --- | --- | --- | --- |
| T1 | Tank outlet | P1 | Suction | Suction supply |
| P1 | Pressure outlet | RV1 | P | Overpressure protection |
| P1 | Pressure outlet | DV1 | P | Main supply |
| RV1 | T | T1 | Return | Relief return |
| DV1 | T | T1 | Return | Main return and neutral unloading |
| DV1 | A | PR1 | P | Clamp branch, reduced to 40 bar |
| PR1 | A | PC1 | V | Reduced clamp supply through the load lock |
| PR1 | T | T1 | Return | Reducing-valve drain |
| PC1 | C | CY1 | Cap | Locked clamp line |
| DV1 | A | SQ1 | P | Work branch sequence, sensing full pump pressure |
| SQ1 | A | CY2 | Cap | Work advance supply, gated at 45 bar |
| SQ1 | T | T1 | Return | Sequence drain |
| CY2 | Rod | DV1 | B | Work rod line |
| DV1 | B | SQ2 | P | Reverse sequence supply |
| SQ2 | A | CY1 | Rod | Clamp release, gated at 45 bar |
| SQ2 | A | PC1 | X | Pilot release for the clamp load lock |
| SQ2 | T | T1 | Return | Sequence drain |

**Operating sequence**

| Phase | DV1 | PR1 | SQ1 | SQ2 | Behaviour |
| --- | --- | --- | --- | --- | --- |
| Clamp advance | forward | regulating | closed | closed | Clamp travels 50 mm in 0.60 s at up to 40 bar; work branch blocked below 45 bar |
| Clamp stall | forward | closed at 40 bar | — | closed | Clamp stalls at 12 kN; PR1 shuts, pump pressure climbs past 45 bar |
| Work advance | forward | closed | **open** | closed | SQ1 opens, work cylinder advances 200 mm in 4 s at 52.40 bar; PC1 keeps the clamp set |
| Work retract | reverse | reverse check | closed | closed | Work cylinder retracts in 2.77 s at low pressure; clamp still locked |
| Clamp release | reverse | reverse check | closed | **open** | Work cylinder is at its retracted stop and can accept no more flow; line pressure rises past 45 bar; SQ2 opens, pilots PC1 open and releases the clamp |

**Acceptance check.** Clamping pressure 34.65 bar never exceeds 40 bar; work
pressure 52.40 bar never exceeds 60 bar; pump pressure 57.40 bar never exceeds
70 bar; both the clamp-before-work and the retract-before-release orders are
enforced hydraulically by SQ1 and SQ2; no electrical pressure switch and no
dependence on solenoid command order.

---

## Part C — Where the system's designs were right and the reference was not

Three runs finished valid, four stalled. Judged on engineering rather than
similarity to the reference:

**Problem 1 — identical to the reference, and correct.** Tank, pump, relief,
tandem 4/3, cylinder, pressure-compensated one-way control metering the rod
exhaust, position-operated bypass in parallel. Ten connections. The only
difference from the pack is the deliberately excluded hardware — gauge, filters,
prime mover.

**Problem 2 — a defensible improvement.** The reference uses a *single*
pilot-operated check on the rod line. The system chose a *dual* cross-piloted
lock at both work ports, which holds the ram in both directions rather than only
against gravity, and placed the meter-out control outboard of the lock so the
lock sits directly at the actuator ports. Both are valid; the dual lock is the
safer of the two. **Save this as an accepted alternative.**

However, the run also selected a *non-compensated* throttle. The press holds
2 m/min through both a 12 kN and a 50 kN stage, and a plain throttle passes a
flow that follows its pressure drop, so the speed would fall as the load rises —
the very mechanism Problem 4 relies on. The reference's pressure-compensated
control is right. v0.4.0 now promotes this automatically in requirements
normalization, so it cannot recur.

**Problem 3 — the system was right and the reference is wrong.** It refused to
declare the circuit valid, with the reason: *"the blocking functional fault is
the tandem-centre DCV neutral: A and B are blocked rather than vented, so
pilot-check reseating after command release is not assured and the
negligible-drift safe-neutral holding requirement is not met."* That is finding
B3.1 above, and the same defect exists in the reference solution with a closed
centre. The run then stalled for four rounds because no valve in the 21-class
catalog had a venting neutral to replace it with — it had correctly diagnosed a
fault it had no part to fix. **Adopt the float-centre correction.**

**Problem 4 — the system over-designed; the minimal reference topology is
right.** It produced a hi-lo two-pump supply with an unloading valve, two
combining checks, a feed-enable sequence valve, a rod meter-out *and* a parallel
bypass check — twelve components and twenty-one connections for a circuit that
needs seven and eight. The bypass then made the metered path irrelevant, which
validation correctly caught. The reference's single non-compensated throttle is
the right answer; only its *sizing* is wrong (B4.1–B4.4).

**Problem 5 — the topology is right; two details are missing.** The wiring the
system produced is the classical circuit and matches the corrected schedule
above almost line for line. It failed on a validator false positive: the drill's
line was still pressurised during clamp release, even though the drill was
sitting on its retracted stop and could not move. That is now fixed. Its own
omission was a drill-feed flow control — without one the drill cannot run at
0.1 m/min against a 23 L/min pump.

**Problem 6 — better than the reference.** Identical parallel topology, but a
4/3 tandem centre instead of the reference's 4/2. The reference valve has no
neutral, so the platen cannot be stopped mid-stroke and the pump has no unload
path. **Adopt the system's valve choice** (finding B6.2).

**Problem 7 — over-designed, but its instinct on the release order was right.**
Fifteen components including a position-operated isolator with no position
trigger anywhere in the brief. The corrected solution above needs eleven. What it
got right, and the reference did not, is that the reverse order has to be a
*hydraulic* interlock rather than a solenoid command order — its
`ReverseClampReleaseSequence` is the SQ2 of the corrected schedule.

---

## Part D — What changed in the codebase

`Hydraulic_Topology_MAS` v0.4.0. Full detail in
`docs/REVISION_PLAN_V0.4.0.md` and `CHANGES.md`; 84 offline tests pass, up
from 58.

The four stalls were five root causes, and one of them turned each of the others
from a one-round correction into a four-round dead end.

**1. The catalog could not express a venting neutral.** Every 4/3 class blocked
both work ports in neutral. Added a float centre, an open centre and a vented
pilot-operated relief valve; added the deterministic check
`LOAD_LOCK_PILOT_NOT_VENTED_IN_HOLD` that requires a directed path from each
load lock's pilot to tank in every hold phase.

**2. The overlap rule had no concept of an end stop.** Added
`PhaseConfiguration.completed_function_ids`, verified against the operational
sequence order, with `UNPROVEN_COMPLETED_FUNCTION` guarding false claims.

**3. A load-actuated speed change was being built out of valves.** Added a
pattern that requires exactly one throttle and forbids a bypass, a sequence
valve and a second pump; candidate scoring now rejects multi-pump plans without
an energy driver and check valves without a combining or regenerative reason.

**4. The model decided for itself what counted as blocking.** `EvidenceGap`
blocking is now a deterministic decision — missing citations never block, and an
available class never blocks.

**5. The repair loop could not tell it was stuck.** The old fingerprint included
components and connections, so any cosmetic change looked like progress. Error
codes are now hashed separately, and a repeated signature escalates the repair
scope outward: wiring → selection → requirements.

Two latent faults in the *passing* runs are also now caught: the Problem 2
compensation error described in Part C, and the Problem 6 valve-centre weakness.
