"""Regression tests for the optimizer's CPU-process budget."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.parallel_ensemble import _process_worker_split


def test_process_budget_never_exceeds_logical_cores() -> None:
    for cores in (2, 4, 8, 12, 32):
        with patch("algorithms.parallel_ensemble.os.cpu_count", return_value=cores):
            light, strategy = _process_worker_split(1_000)
        assert light + strategy <= cores
        assert light == 1
        assert strategy == cores - 1
    print("[OK] comparison and production pools stay within the CPU budget")


if __name__ == "__main__":
    test_process_budget_never_exceeds_logical_cores()
    print("PARALLEL ENSEMBLE BUDGET TEST OK")
