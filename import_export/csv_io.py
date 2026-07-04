from __future__ import annotations

import csv
from pathlib import Path


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: str | Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        # Write BOM-only so Excel still recognises the file as UTF-8.
        Path(path).write_text("﻿", encoding="utf-8")
        return
    # utf-8-sig writes a UTF-8 BOM at the start of the file, which tells
    # Microsoft Excel (on Windows) to open the file as UTF-8 instead of the
    # system ANSI codepage — without it Polish characters (ł, ó, ę …) are
    # displayed as garbage.
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

