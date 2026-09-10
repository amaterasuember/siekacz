"""Portable CAD drawing snapshot stored with the part, independent of source files."""
from __future__ import annotations
import json
from functools import lru_cache
import math

@lru_cache(maxsize=128)
def contours_from_notes(notes: str) -> tuple:
    if "|contours=" not in notes:
        return ()
    try:
        data = json.loads(notes.split("|contours=", 1)[1])
        if not isinstance(data, list) or len(data) > 100000:
            return ()
        return tuple(tuple((float(p[0]), float(p[1])) for p in line) for line in data
                     if len(line) == 2 and all(len(p) == 2 and all(math.isfinite(float(v)) and -0.001 <= float(v) <= 1.001 for v in p) for p in line))
    except (ValueError, TypeError, IndexError, OverflowError):
        return ()
