"""The sizings published in the reference PDF, as the audit recorded them.

These are the E1 ground truth. Each entry is what the reference pack specifies,
including the parts the audit found wrong, so the verifier can be asked to
rediscover the defects without being told where they are.
"""

from hydraulic_mas.sizing.quantities import Q, annulus_area, area_of_bore


def _cyl(bore: float, rod: float) -> dict:
    return {
        "cap_area_m2": area_of_bore(bore).value,
        "annulus_area_m2": annulus_area(bore, rod).value,
        "bore_mm": bore, "rod_mm": rod,
    }


def _supply(flow_lpm: float, relief_bar: float, motor_kw: float, reservoir_l: float) -> dict:
    return {
        "flow_m3s": Q.of(flow_lpm, "l/min").value,
        "relief_pa": relief_bar * 1e5,
        "motor_kw": motor_kw,
        "reservoir_l": reservoir_l,
        "overall_efficiency": 0.85,
        "speed_rpm": 1500.0,
        "displacement_cm3": flow_lpm * 1000.0 / (1500.0 * 0.9),
    }


# Each: (sizing dict, the defect the audit recorded, the layer expected to catch it)
PDF_SIZINGS = {
    "P7-01": (
        {"SlideCylinder": _cyl(100, 60), "__supply__": _supply(25.0, 65.0, 1.5, 75.0)},
        "prime mover quoted at 1.21 kW; the fixed pump discharges over the relief "
        "throughout the cut, so the shaft duty is about 3.2 kW",
        "V6",
    ),
    "P7-02": (
        {"PressCylinder": _cyl(100, 60), "__supply__": _supply(40.0, 85.0, 3.0, 120.0)},
        "relief margin thin once compensated meter-out backpressure is counted",
        "V7",
    ),
    "P7-03": (
        {"Cylinder": _cyl(63, 40), "__supply__": _supply(12.0, 70.0, 1.5, 40.0)},
        "prime mover quoted at 1.24 kW, which is hydraulic power, not shaft power",
        "V6",
    ),
    "P7-04": (
        {"SlideCylinder": _cyl(100, 50), "__supply__": _supply(11.78, 18.0, 0.37, 35.3)},
        "efficiency omitted; 0.37 kW is hydraulic power; the operating point is "
        "0.4 bar from stall",
        "V6",
    ),
    "P7-05": (
        {"ClampCylinder": _cyl(125, 50), "DrillCylinder": _cyl(63, 35),
         "__supply__": _supply(18.4, 8.0, 0.55, 75.0)},
        "the 63 mm drill cylinder cannot develop 2500 N inside the 8 bar ceiling",
        "V3",
    ),
    "P7-06": (
        {"LeftCylinder": _cyl(63, 35), "RightCylinder": _cyl(63, 35),
         "__supply__": _supply(37.41, 70.0, 5.5, 120.0)},
        "relief set to 70 bar but 20 kN per cylinder needs 71.3 bar; the valve "
        "opens before the load is reached",
        "V7",
    ),
    "P7-07": (
        {"ClampCylinder": _cyl(63, 35), "WorkCylinder": _cyl(80, 45),
         "__supply__": _supply(15.08, 65.0, 1.92, 60.0)},
        "both cylinders exceed their stated branch pressure ceilings once "
        "efficiency is applied",
        "V3",
    ),
}
