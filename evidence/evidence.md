# Evidence tables

## T1 — What the certificates range over

| Problem | Stated | Answered | Checks | Verdict | P/U/R |
| --- | ---: | ---: | ---: | --- | --- |
| P7-01 | 15 | 8 | 7 | PROVED | 7/0/0 |
| P7-02 | 14 | 8 | 8 | PROVED | 8/0/0 |
| P7-03 | 9 | 6 | 6 | PROVED | 6/0/0 |
| P7-04 | 10 | 6 | 6 | UNDECIDED | 5/1/0 |
| P7-05 | 8 | 6 | 6 | PROVED | 6/0/0 |
| P7-06 | 8 | 4 | 4 | PROVED | 4/0/0 |
| P7-07 | 9 | 6 | 7 | UNDECIDED | 6/1/0 |
| **total** | **73** | **44** (**60%**) | **44** | | |

Interval methods account for 100% of checks: monotone-corner 44

Statements not answered, by reason:

- **19** — geometric_choice_not_an_outcome: fixes a dimension the design chooses; nothing to solve for
- **4** — settled_at_topology_stage: a structural claim already proved by the topology validator
- **2** — non_quantitative: states no numeric target to check against
- **2** — outside_quasi_static_scope: requires transient or leakage behaviour a quasi-static model cannot see
- **2** — restated_by_another_criterion: the same physical fact another criterion already carries

## T4 — Seeded defects

False-positive rate: **0%** over 10 null injections.

| Severity | Injections | Detected | Rate |
| --- | ---: | ---: | ---: |
| subtle | 15 | 15 | 100% |
| gross | 15 | 15 | 100% |

## T5 — Detection against defect magnitude

**relief_low**

| Magnitude | n | Detected |
| ---: | ---: | ---: |
| 0.5% | 5 | 0% |
| 1.0% | 5 | 40% |
| 2.0% | 5 | 60% |
| 3.0% | 5 | 80% |
| 5.0% | 5 | 100% |
| 8.0% | 5 | 100% |
| 12.0% | 5 | 100% |
| 20.0% | 5 | 100% |

**speed_control_shut**

| Magnitude | n | Detected |
| ---: | ---: | ---: |
| 0.5% | 1 | 0% |
| 1.0% | 1 | 0% |
| 2.0% | 1 | 0% |
| 3.0% | 1 | 100% |
| 5.0% | 1 | 100% |
| 8.0% | 1 | 100% |
| 12.0% | 1 | 100% |
| 20.0% | 1 | 100% |

## T6 — Transient cross-check

- phases integrated: 22, all settled: True
- settled velocity against the quasi-static solve: median 0.06%, worst 3.24%
- acceleration occupies a median 5% of the stroke
- never reaching steady state: ['clamp_extend_apply_force']
- mostly transient: ['clamp_extend_to_workpiece', 'platen_advance', 'platen_return']
