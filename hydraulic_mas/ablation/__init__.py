"""The controlled ablation the paper's central claim rests on.

Four arms, one verifier, one contract. See ``arms.py`` for what each condition
withholds and ``metrics.py`` for what is measured beyond the pass rate.
"""

from .arms import ARM_DESCRIPTION, ARMS, ArmResult
from .metrics import (
    catalog_violation_rate,
    error_table,
    prediction_errors,
    requirement_errors,
    verdict_rates,
)
from .random_policy import run_random_arm
from .runner import Suite, load_records, load_suite, run_cell, run_sweep
from .schemas import DirectSizing

__all__ = [
    "ARMS", "ARM_DESCRIPTION", "ArmResult", "DirectSizing", "Suite",
    "catalog_violation_rate", "error_table", "load_records", "load_suite",
    "prediction_errors", "requirement_errors", "run_cell", "run_random_arm", "run_sweep",
    "verdict_rates",
]
