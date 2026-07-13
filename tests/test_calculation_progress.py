from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calculation_process import calculation_process_entry
from core.models import OptimizationSettings, Project, SheetPart, SheetStock


def test_progress_packets_follow_real_ensemble_phases() -> None:
    project = Project(
        sheet_stock=[SheetStock("PA6", 18, 1000, 2000, 1)],
        sheet_parts=[SheetPart("A", 300, 400, 2, "PA6", 18)],
        settings=OptimizationSettings(algorithm="auto", multi_core=False, kerf=3),
    )
    receiver, sender = mp.Pipe(duplex=False)
    calculation_process_entry(project, sender)

    packets: list[tuple[str, object]] = []
    while True:
        try:
            packets.append(receiver.recv())
        except (EOFError, BrokenPipeError):
            break

    progress = [payload[0] for kind, payload in packets if kind == "progress"]
    labels = [payload[1] for kind, payload in packets if kind == "progress"]
    assert progress[0] == 0
    assert progress[-1] == 100
    assert all(previous <= current for previous, current in zip(progress, progress[1:]))
    assert any("Zakończono 1/4" in label for label in labels)
    assert packets[-1][0] == "result"


if __name__ == "__main__":
    test_progress_packets_follow_real_ensemble_phases()
    print("test_calculation_progress: OK")
