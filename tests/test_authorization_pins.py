from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.expiry import authorization_pin_is_valid


def run() -> None:
    assert authorization_pin_is_valid("2137")
    assert authorization_pin_is_valid(" 2137 ")
    assert not authorization_pin_is_valid("1984")
    assert not authorization_pin_is_valid("wrong")
    print("Authorization PIN checks passed")


if __name__ == "__main__":
    run()
