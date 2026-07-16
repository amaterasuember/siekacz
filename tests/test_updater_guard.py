from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.updater import expected_installer_name, windows_file_version


def test_release_installer_name_contains_the_release_version() -> None:
    assert expected_installer_name("v3.1.8-beta") == "SIEKACZ9000_Setup_v3.1.8-beta.exe"
    assert expected_installer_name("3.2.0") == "SIEKACZ9000_Setup_v3.2.0.exe"
    print("[OK] installer asset name is tied to the release tag")


def test_windows_version_is_derived_from_semver() -> None:
    assert windows_file_version("3.1.8-beta") == "3.1.8.0"
    assert windows_file_version("v4.2") == "4.2.0.0"
    print("[OK] semantic release version maps to Windows metadata")


if __name__ == "__main__":
    test_release_installer_name_contains_the_release_version()
    test_windows_version_is_derived_from_semver()
    print("UPDATER GUARD TESTS OK")
