"""The controlled ablation the paper's central claim rests on.

Four arms, one verifier, one contract. See ``arms.py`` for what each condition
withholds and ``metrics.py`` for what is measured beyond the pass rate.
"""

from .arms import (
    APPLY_OVERSIZING_CEILING,
    ARM_DESCRIPTION,
    ARMS,
    ArmResult,
    run_direct_verified_arm,
)
from .feedback import render_certificate_feedback
from .metrics import (
    catalog_violation_rate,
    error_table,
    prediction_errors,
    requirement_errors,
    verdict_rates,
)
from .random_policy import run_random_arm
from .runner import Suite, load_records, load_suite, run_cell, run_sweep
from .schemas import DirectSizing, DirectSizingSet

__all__ = [
    "APPLY_OVERSIZING_CEILING", "ARMS", "ARM_DESCRIPTION", "ArmResult",
    "DirectSizing", "DirectSizingSet", "Suite",
    "catalog_violation_rate", "error_table", "load_records", "load_suite",
    "prediction_errors", "render_certificate_feedback", "requirement_errors",
    "run_cell", "run_direct_verified_arm", "run_random_arm", "run_sweep",
    "verdict_rates",
]
