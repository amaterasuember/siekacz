from __future__ import annotations

import json
from io import BytesIO
import sys
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.publisher import PublisherWorker


ROOT = Path(__file__).resolve().parents[1]


def test_publisher_dispatches_version_and_notes_to_native_build_workflow() -> None:
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    worker = PublisherWorker("secret", "4.2.0", "Zmiany testowe")
    with patch("app.publisher.urllib.request.urlopen", side_effect=fake_urlopen):
        worker._dispatch_multiplatform_release()

    assert captured["url"].endswith("/actions/workflows/release-multiplatform.yml/dispatches")
    assert captured["payload"] == {
        "ref": "main",
        "inputs": {"version": "4.2.0", "notes": "Zmiany testowe"},
    }
    print("[OK] publisher delegates release metadata to the native multi-OS workflow")


def test_publisher_explains_when_the_release_workflow_is_not_on_github() -> None:
    worker = PublisherWorker("secret", "4.2.0", "Zmiany testowe")
    not_found = HTTPError(
        "https://api.github.com/repos/example/siekacz/actions/workflows/release-multiplatform.yml/dispatches",
        404,
        "Not Found",
        hdrs=None,
        fp=BytesIO(b'{"message":"Not Found"}'),
    )
    with patch("app.publisher.urllib.request.urlopen", side_effect=not_found):
        try:
            worker._dispatch_multiplatform_release()
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("HTTP 404 powinien dać komunikat o brakującym workflow.")

    assert "release-multiplatform.yml" in message
    assert "main" in message
    assert "Contents" not in message
    print("[OK] publisher distinguishes a missing GitHub workflow from token permissions")


def test_release_workflow_builds_every_supported_asset() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-multiplatform.yml").read_text(encoding="utf-8")
    required_markers = (
        "windows-2022",
        "macos-15-intel",
        "macos-15",
        "ubuntu-24.04",
        "ubuntu-24.04-arm",
        "_windows-x64.exe",
        "_macos-*.dmg",
        "_linux-*.AppImage",
        "VERIFY_WINDOWS_SERVER.ps1",
    )
    for marker in required_markers:
        assert marker in workflow, marker
    assert (ROOT / "scripts" / "build_macos.sh").is_file()
    assert (ROOT / "scripts" / "build_linux.sh").is_file()
    assert (ROOT / "installer_src" / "AppRun").is_file()
    print("[OK] CI defines native Windows Server, Intel/ARM macOS and x64/ARM Linux packages")


def test_cad_viewer_is_bundled_on_every_desktop_platform() -> None:
    for relative in ("scripts/BUILD_EXE.bat", "scripts/build_macos.sh", "scripts/build_linux.sh"):
        build_script = (ROOT / relative).read_text(encoding="utf-8")
        assert "app.cad_viewer" in build_script, relative
        assert "cad.inspection" in build_script, relative
    print("[OK] DXF/STEP/STL viewer is included in Windows, macOS and Linux packages")


if __name__ == "__main__":
    test_publisher_dispatches_version_and_notes_to_native_build_workflow()
    test_publisher_explains_when_the_release_workflow_is_not_on_github()
    test_release_workflow_builds_every_supported_asset()
    test_cad_viewer_is_bundled_on_every_desktop_platform()
    print("MULTIPLATFORM RELEASE TESTS OK")
