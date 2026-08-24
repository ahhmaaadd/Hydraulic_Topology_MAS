# Sized and Certified Solutions — all seven problems

Produced by `hydraulic_mas.sizing` v0.5.0 on the seven topologies the
multi-agent system generated, every one of which re-validates clean under v0.4.5.

Verification is **Phase-Resolved Quasi-Static** — each phase is solved as an
algebraic system in the valve states the topology stage already proved, then
bounded over an operating envelope of ±2 points on each efficiency (and ±15 %
load where the brief states it). A verdict of PROVED means the guaranteed
enclosure lies inside the requirement *everywhere in that envelope*, not merely
at nominal.

| Problem | Verdict | Criteria | Actuators | Pump | Relief | Motor | Tank | Valves |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P7-01 | **UNDECIDED** | 6P / 1U / 0R | SlideCylinder 100/56 | 19 cm³/rev (25.6 L/min) | 63.8 bar | 4.0 kW | 80 L | NG6 |
| P7-02 | **PROVED** | 8P / 0U / 0R | PressCylinder 100/56 | 32 cm³/rev (43.2 L/min) | 79.8 bar | 7.5 kW | 160 L | NG10 |
| P7-03 | **PROVED** | 6P / 0U / 0R | Cylinder 50/28 | 8 cm³/rev (10.8 L/min) | 95.3 bar | 2.2 kW | 40 L | NG4 |
| P7-04 | **REFUTED** | 4P / 1U / 1R | SlideCylinder 100/80 | 11 cm³/rev (14.8 L/min) | 20.0 bar | 0.75 kW | 60 L | NG6 |
| P7-05 | **PROVED** | 6P / 0U / 0R | ClampCylinder 100/56<br>DrillCylinder 80/45 | 11 cm³/rev (14.8 L/min) | 8.0 bar | 0.25 kW | 60 L | NG6 |
| P7-06 | **PROVED** | 4P / 0U / 0R | LeftCylinder 63/36<br>RightCylinder 63/36 | 32 cm³/rev (43.2 L/min) | 80.0 bar | 7.5 kW | 160 L | NG10 |
| P7-07 | **UNDECIDED** | 6P / 1U / 0R | ClampCylinder 80/45<br>WorkCylinder 100/56 | 19 cm³/rev (25.6 L/min) | 49.0 bar | 3.0 kW | 80 L | NG6 |

---

## P7-01 — UNDECIDED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| SlideCylinder | 100/56 mm | cap 7854 mm², annulus 5391 mm², ratio 1.46 |
| Pump | 19 cm³/rev at 1500 rpm | 25.65 L/min delivered at η_v 0.90 |
| Relief valve | 63.8 bar | above the worst working pressure over the envelope |
| Prime mover | 4.0 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 80 L | three times delivered flow |
| Directional valves | NG6 | ≥125 % of 37.4 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 28x2 (ID 24 mm) | target velocity 1 m/s |
| Pressure line | 15x1.5 (ID 12 mm) | target velocity 4 m/s |
| Return line | 22x2.5 (ID 17 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| slide_approach_extend | flow_limited | 3.266 m/min | 22.12 bar | 21.58 bar | 0.52 bar | 0.00 L/min |
| slide_cutting_feed_extend | flow_set | 0.750 m/min | 49.55 bar | 49.52 bar | 0.01 bar | 19.76 L/min |
| slide_retract_return | flow_limited | 4.758 m/min | 12.89 bar | 12.00 bar | 1.16 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| slide_approach_extend__velocity | >= 3 m/min | [3.193, 3.338] m/min | PROVED | monotone-corner |
| slide_approach_extend__force | >= 15 kn | 44.9 kn available at the relief setting | PROVED | static-force-balance |
| slide_cutting_feed_extend__velocity_range | adjustable 0.5 .. 1 m/min | [0.75, 0.75] m/min | UNDECIDED | monotone-corner |
| slide_cutting_feed_extend__force | >= 35 kn | 45.1 kn available at the relief setting | PROVED | static-force-balance |
| slide_retract_return__velocity | >= 4.2 m/min | [4.652, 4.864] m/min | PROVED | monotone-corner |
| slide_retract_return__force | >= 5 kn | 30.1 kn available at the relief setting | PROVED | static-force-balance |
| system__pressure_ceiling | <= 70 bar | 49.6 bar worst phase | PROVED | worst-phase-nominal |

**Findings**

- `[V6] THERMAL_CAPACITY_EXCEEDED` — 1.63 kW crosses the relief at the worst phase but a 80 L reservoir sheds roughly 0.80 kW; a cooler is needed

## P7-02 — PROVED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| PressCylinder | 100/56 mm | cap 7854 mm², annulus 5391 mm², ratio 1.46 |
| Pump | 32 cm³/rev at 1500 rpm | 43.20 L/min delivered at η_v 0.90 |
| Relief valve | 79.8 bar | above the worst working pressure over the envelope |
| Prime mover | 7.5 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 160 L | three times delivered flow |
| Directional valves | NG10 | ≥125 % of 62.9 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 38x3 (ID 32 mm) | target velocity 1 m/s |
| Pressure line | 20x2 (ID 16 mm) | target velocity 4 m/s |
| Return line | 28x3 (ID 22 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| ram_rapid_down_approach | flow_limited | 5.500 m/min | 1.33 bar | 0.71 bar | 1.03 bar | 0.00 L/min |
| ram_working_feed_down | flow_set | 2.120 m/min | 17.10 bar | 17.01 bar | 0.04 bar | 26.55 L/min |
| ram_final_press_down | flow_set | 2.120 m/min | 70.86 bar | 70.77 bar | 0.04 bar | 26.55 L/min |
| ram_return_up | flow_limited | 8.013 m/min | 9.00 bar | 8.13 bar | 1.33 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| ram_rapid_down_approach__velocity | >= 5 m/min | [5.378, 5.623] m/min | PROVED | monotone-corner |
| ram_working_feed_down__velocity | >= 2 m/min | [2.12, 2.12] m/min | PROVED | monotone-corner |
| ram_working_feed_down__force | >= 12 kn | 56.4 kn available at the relief setting | PROVED | static-force-balance |
| ram_final_press_down__velocity | >= 2 m/min | [2.12, 2.12] m/min | PROVED | monotone-corner |
| ram_final_press_down__force | >= 50 kn | 56.4 kn available at the relief setting | PROVED | static-force-balance |
| ram_return_up__velocity | >= 7.5 m/min | [7.835, 8.191] m/min | PROVED | monotone-corner |
| ram_return_up__force | >= 3 kn | 37.8 kn available at the relief setting | PROVED | static-force-balance |
| system__pressure_ceiling | <= 100 bar | 70.9 bar worst phase | PROVED | worst-phase-nominal |

**Findings**

- `[V6] THERMAL_CAPACITY_EXCEEDED` — 3.14 kW crosses the relief at the worst phase but a 160 L reservoir sheds roughly 1.60 kW; a cooler is needed

## P7-03 — PROVED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| Cylinder | 50/28 mm | cap 1963 mm², annulus 1348 mm², ratio 1.46 |
| Pump | 8 cm³/rev at 1500 rpm | 10.80 L/min delivered at η_v 0.90 |
| Relief valve | 95.3 bar | above the worst working pressure over the envelope |
| Prime mover | 2.2 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 40 L | three times delivered flow |
| Directional valves | NG4 | ≥125 % of 15.7 L/min peak return |
| Pressure class | 160 bar | 125 % of the relief setting |
| Suction line | 20x2 (ID 16 mm) | target velocity 1 m/s |
| Pressure line | 10x1 (ID 8 mm) | target velocity 4 m/s |
| Return line | 15x2 (ID 11 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| carriage_advance | flow_limited | 5.500 m/min | 86.75 bar | 85.34 bar | 0.67 bar | 0.00 L/min |
| carriage_return | flow_limited | 8.013 m/min | 84.11 bar | 82.69 bar | 3.00 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| carriage_advance__velocity | >= 3.5 m/min | [5.378, 5.623] m/min | PROVED | monotone-corner |
| carriage_advance__force | >= 15 kn | 16.8 kn available at the relief setting | PROVED | static-force-balance |
| carriage_return__velocity | >= 5.5 m/min | [7.835, 8.191] m/min | PROVED | monotone-corner |
| carriage_return__force | >= 9.5 kn | 11 kn available at the relief setting | PROVED | static-force-balance |
| carriage_hold_any_position__force | >= 15 kn | 16.8 kn sustainable | PROVED | static-hold-balance |
| system__pressure_ceiling | <= 100 bar | 86.8 bar worst phase | PROVED | worst-phase-nominal |

## P7-04 — REFUTED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| SlideCylinder | 100/80 mm | cap 7854 mm², annulus 2827 mm², ratio 2.78 |
| Pump | 11 cm³/rev at 1500 rpm | 14.85 L/min delivered at η_v 0.90 |
| Relief valve | 20.0 bar | above the worst working pressure over the envelope |
| Prime mover | 0.75 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 60 L | three times delivered flow |
| Directional valves | NG6 | ≥125 % of 41.2 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 22x2 (ID 18 mm) | target velocity 1 m/s |
| Pressure line | 12x1.5 (ID 9 mm) | target velocity 4 m/s |
| Return line | 22x2 (ID 18 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| slide_rapid_approach | pressure_limited | 1.413 m/min | 20.00 bar | 19.90 bar | 45.45 bar | 3.75 L/min |
| slide_working_feed | pressure_limited | 0.530 m/min | 20.00 bar | 19.99 bar | 6.39 bar | 10.69 L/min |
| slide_full_return | flow_limited | 5.252 m/min | 14.06 bar | 13.76 bar | 1.42 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| slide_rapid_approach__velocity | >= 1.5 m/min | [1.41, 1.416] m/min | REFUTED | monotone-corner |
| slide_rapid_approach__force | >= 2.5 kn | 2.57 kn available at the relief setting | PROVED | static-force-balance |
| slide_working_feed__velocity | >= 0.5 m/min | [0.4819, 0.5723] m/min | UNDECIDED | monotone-corner |
| slide_working_feed__force | >= 12.5 kn | 12.5 kn available at the relief setting | PROVED | static-force-balance |
| slide_full_return__force | >= 2.5 kn | 4.09 kn available at the relief setting | PROVED | static-force-balance |
| system__pressure_ceiling | <= 20 bar | 20 bar worst phase | PROVED | worst-phase-nominal |

**Findings**

- `[V7] RELIEF_MARGIN_THIN` — only 0.00 bar between the worst working pressure and the relief setting; the valve will simmer

**Notes**

- SlideCylinder: rod chosen for a 3.0:1 speed ratio, not for force alone
- machine_slide_linear_motion: a 3.0:1 load-actuated speed ratio at 20.0 bar needs a cap area of 6944..7639 mm2, and no preferred bore lies in that window. The requirement is unreachable at this ceiling for any rod diameter.
- repair stopped: the last change did not move any verdict

**Repairs applied**

- SlideCylinder: rod 70 -> 80 mm (more area ratio for the load-actuated speed change)

## P7-05 — PROVED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| ClampCylinder | 100/56 mm | cap 7854 mm², annulus 5391 mm², ratio 1.46 |
| DrillCylinder | 80/45 mm | cap 5027 mm², annulus 3436 mm², ratio 1.46 |
| Pump | 11 cm³/rev at 1500 rpm | 14.85 L/min delivered at η_v 0.90 |
| Relief valve | 8.0 bar | above the worst working pressure over the envelope |
| Prime mover | 0.25 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 60 L | three times delivered flow |
| Directional valves | NG6 | ≥125 % of 21.7 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 22x2 (ID 18 mm) | target velocity 1 m/s |
| Pressure line | 12x1.5 (ID 9 mm) | target velocity 4 m/s |
| Return line | 16x1.5 (ID 13 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| clamp_extend_to_workpiece | flow_limited | 1.891 m/min | 6.07 bar | 5.78 bar | 0.17 bar | 0.00 L/min |
| drill_feed_stroke | pressure_limited | 0.106 m/min | 8.00 bar | 8.00 bar | 3.62 bar | 14.32 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| clamp_extend_to_workpiece__velocity | >= 1.5 m/min | [1.849, 1.933] m/min | PROVED | monotone-corner |
| clamp_extend_to_workpiece__force | >= 4 kn | 5.57 kn available at the relief setting | PROVED | static-force-balance |
| clamp_hold_during_drilling__force | >= 4 kn | 5.65 kn sustainable | PROVED | static-hold-balance |
| drill_feed_stroke__velocity | >= 0.1 m/min | [0.1033, 0.1085] m/min | PROVED | monotone-corner |
| drill_feed_stroke__force | >= 2.5 kn | 2.5 kn available at the relief setting | PROVED | static-force-balance |
| system__pressure_ceiling | <= 8 bar | 8 bar worst phase | PROVED | worst-phase-nominal |

**Findings**

- `[V7] RELIEF_MARGIN_THIN` — only 0.00 bar between the worst working pressure and the relief setting; the valve will simmer

## P7-06 — PROVED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| LeftCylinder | 63/36 mm | cap 3117 mm², annulus 2099 mm², ratio 1.48 |
| RightCylinder | 63/36 mm | cap 3117 mm², annulus 2099 mm², ratio 1.48 |
| Pump | 32 cm³/rev at 1500 rpm | 43.20 L/min delivered at η_v 0.90 |
| Relief valve | 80.0 bar | above the worst working pressure over the envelope |
| Prime mover | 7.5 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 160 L | three times delivered flow |
| Directional valves | NG10 | ≥125 % of 64.1 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 38x3 (ID 32 mm) | target velocity 1 m/s |
| Pressure line | 20x2 (ID 16 mm) | target velocity 4 m/s |
| Return line | 28x3 (ID 22 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| platen_advance | flow_limited | 6.929 m/min | 71.80 bar | 71.41 bar | 0.18 bar | 0.00 L/min |
| platen_return | flow_limited | 10.289 m/min | 33.42 bar | 33.03 bar | 0.86 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| platen_advance__velocity | >= 6 m/min | [6.775, 7.083] m/min | PROVED | monotone-corner |
| platen_advance__force | >= 40 kn | 44.8 kn available at the relief setting | PROVED | static-force-balance |
| platen_return__force | >= 12 kn | 29.7 kn available at the relief setting | PROVED | static-force-balance |
| system__pressure_ceiling | <= 80 bar | 71.8 bar worst phase | PROVED | worst-phase-nominal |

## P7-07 — UNDECIDED

**Selected components**

| Item | Selection | Basis |
| --- | --- | --- |
| ClampCylinder | 80/45 mm | cap 5027 mm², annulus 3436 mm², ratio 1.46 |
| WorkCylinder | 100/56 mm | cap 7854 mm², annulus 5391 mm², ratio 1.46 |
| Pump | 19 cm³/rev at 1500 rpm | 25.65 L/min delivered at η_v 0.90 |
| Relief valve | 49.0 bar | above the worst working pressure over the envelope |
| Prime mover | 3.0 kW | shaft power at the worst phase, η_o 0.85 |
| Reservoir | 80 L | three times delivered flow |
| Directional valves | NG6 | ≥125 % of 37.5 L/min peak return |
| Pressure class | 100 bar | 125 % of the relief setting |
| Suction line | 28x2 (ID 24 mm) | target velocity 1 m/s |
| Pressure line | 15x1.5 (ID 12 mm) | target velocity 4 m/s |
| Return line | 22x2.5 (ID 17 mm) | target velocity 3 m/s |

**Operating points**

| Phase | Regime | Velocity | Pump | Supply | Exhaust | Over relief |
| --- | --- | --- | --- | --- | --- | --- |
| clamp_extend_apply_force | flow_limited | 5.103 m/min | 40.00 bar | 26.63 bar | 0.16 bar | 0.00 L/min |
| working_advance | flow_limited | 3.266 m/min | 43.72 bar | 42.62 bar | 0.26 bar | 0.00 L/min |

**Certificates**

| Criterion | Required | Achieved (enclosure) | Verdict | Method |
| --- | --- | --- | --- | --- |
| clamp_extend_apply_force__force | >= 12 kn | 18 kn available at the relief setting | PROVED | static-force-balance |
| clamp_hold_during_work__force | >= 12 kn | 18.1 kn sustainable | PROVED | static-hold-balance |
| clamp_actuator__pressure_ceiling | <= 40 bar | 40 bar worst phase | PROVED | worst-phase-nominal |
| working_advance__velocity | 2.7 .. 3.3 m/min | [3.193, 3.338] m/min | UNDECIDED | monotone-corner |
| working_advance__force | >= 30 kn | 34.5 kn available at the relief setting | PROVED | static-force-balance |
| working_actuator__pressure_ceiling | <= 60 bar | 43.7 bar worst phase | PROVED | worst-phase-nominal |
| system__pressure_ceiling | <= 70 bar | 43.7 bar worst phase | PROVED | worst-phase-nominal |
