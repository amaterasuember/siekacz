from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.optimization_progress import _SIMS_MESSAGES
from ui.transport_history_facts import (
    AIRCRAFT_FACTS,
    ALL_FACTS,
    PIRATE_FACTS,
    SHIP_FACTS,
    TRAIN_FACTS,
    WWII_FACTS,
)


def test_fact_deck_has_five_balanced_unique_categories() -> None:
    categories = (TRAIN_FACTS, SHIP_FACTS, AIRCRAFT_FACTS, WWII_FACTS, PIRATE_FACTS)
    assert all(len(category) == 40 for category in categories)
    assert len(ALL_FACTS) == 200
    assert len(set(ALL_FACTS)) == 200
    assert tuple(_SIMS_MESSAGES) == ALL_FACTS
    print("[OK] progress deck contains 200 unique facts in five balanced categories")


if __name__ == "__main__":
    test_fact_deck_has_five_balanced_unique_categories()
    print("PROGRESS FACT TESTS OK")
