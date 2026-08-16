from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.updater import (
    current_platform_key,
    expected_installer_name,
    fetch_latest_release,
    launch_downloaded_update,
    windows_file_version,
)


def test_release_installer_name_contains_the_release_version() -> None:
    assert expected_installer_name("v3.1.8-beta", "windows-x64") == "SIEKACZ9000_Setup_v3.1.8-beta_windows-x64.exe"
    assert expected_installer_name("3.2.0", "macos-arm64") == "SIEKACZ9000_v3.2.0_macos-arm64.dmg"
    assert expected_installer_name("3.2.0", "linux-x86_64") == "SIEKACZ9000_v3.2.0_linux-x86_64.AppImage"
    print("[OK] installer asset name is tied to the release tag")


def test_platform_detection_covers_native_release_targets() -> None:
    assert current_platform_key("Windows", "AMD64") == "windows-x64"
    assert current_platform_key("Darwin", "x86_64") == "macos-x64"
    assert current_platform_key("Darwin", "arm64") == "macos-arm64"
    assert current_platform_key("Linux", "x86_64") == "linux-x86_64"
    assert current_platform_key("Linux", "aarch64") == "linux-aarch64"
    print("[OK] updater resolves OS and architecture to native package keys")


def test_windows_version_is_derived_from_semver() -> None:
    assert windows_file_version("3.1.8-beta") == "3.1.8.0"
    assert windows_file_version("v4.2") == "4.2.0.0"
    print("[OK] semantic release version maps to Windows metadata")


def test_release_ignores_an_unexpected_executable_asset() -> None:
    """The updater may only select the installer name tied to the release tag."""
    import json
    from unittest.mock import patch

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "tag_name": "v4.0.0",
                    "assets": [{"name": "unrelated-tool.exe", "browser_download_url": "https://invalid/example.exe"}],
                }
            ).encode("utf-8")

    with patch("app.updater.urllib.request.urlopen", return_value=_Response()):
        info = fetch_latest_release("owner", "repo")
    assert info is not None
    assert not info.asset_url and not info.asset_name
    print("[OK] updater rejects non-versioned executable assets")


def test_release_selects_only_the_current_platform_asset() -> None:
    import json
    from unittest.mock import patch

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "tag_name": "v4.0.0",
                    "assets": [
                        {"name": "SIEKACZ9000_Setup_v4.0.0_windows-x64.exe", "browser_download_url": "https://invalid/windows"},
                        {"name": "SIEKACZ9000_v4.0.0_macos-arm64.dmg", "browser_download_url": "https://invalid/macos"},
                    ],
                }
            ).encode("utf-8")

    with (
        patch("app.updater.current_platform_key", return_value="macos-arm64"),
        patch("app.updater.urllib.request.urlopen", return_value=_Response()),
    ):
        info = fetch_latest_release("owner", "repo")
    assert info is not None
    assert info.platform_key == "macos-arm64"
    assert info.asset_name.endswith("_macos-arm64.dmg")
    assert info.asset_url == "https://invalid/macos"
    print("[OK] updater ignores installers for other operating systems")


def test_native_package_launcher_uses_open_on_macos() -> None:
    from pathlib import Path
    from unittest.mock import patch

    with (
        patch("app.updater.current_platform_key", return_value="macos-arm64"),
        patch("app.updater.subprocess.Popen") as popen,
    ):
        launch_downloaded_update("/tmp/SIEKACZ9000.dmg")
    popen.assert_called_once_with(["open", str(Path("/tmp/SIEKACZ9000.dmg"))], start_new_session=True)
    print("[OK] downloaded macOS package is handed to the native installer shell")


if __name__ == "__main__":
    test_release_installer_name_contains_the_release_version()
    test_platform_detection_covers_native_release_targets()
    test_windows_version_is_derived_from_semver()
    test_release_ignores_an_unexpected_executable_asset()
    test_release_selects_only_the_current_platform_asset()
    test_native_package_launcher_uses_open_on_macos()
    print("UPDATER GUARD TESTS OK")
