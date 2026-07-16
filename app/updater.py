from __future__ import annotations

"""Remote update check against GitHub Releases.

Set ``GITHUB_OWNER`` / ``GITHUB_REPO`` below to your repository.  The app polls
the *latest* published release, compares its tag (e.g. ``v2.1.0``) with the
running :data:`app.version.APP_VERSION`, and — when newer — lets the user read
the release notes and download/run the installer asset.

Pure-stdlib (urllib + json); no extra dependencies.  All network work is meant
to run off the UI thread (see :class:`UpdateCheckWorker`).
"""

import json
import logging
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from app.version import APP_VERSION

_logger = logging.getLogger(__name__)

# ── Configure these for your repository ───────────────────────────────────────
GITHUB_OWNER = "amaterasuember"
GITHUB_REPO = "siekacz"
GENERIC_INSTALLER_NAME = "SIEKACZ9000_Setup.exe"
VERSIONED_INSTALLER_PREFIX = "SIEKACZ9000_Setup_"
ASSET_SUFFIXES = (".exe", ".msi", ".zip")

_API_URL = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
_TIMEOUT_S = 8


@dataclass
class UpdateInfo:
    version: str          # normalized, e.g. "2.1.0"
    tag: str              # raw tag, e.g. "v2.1.0"
    name: str             # release title
    notes: str            # markdown changelog body
    html_url: str         # release page
    asset_url: str        # direct installer download (may be "")
    asset_name: str = ""


def _parse_version(text: str) -> tuple[int, ...]:
    """Lenient semver parse: ``"v2.1.0-beta" -> (2, 1, 0)``. Unknown -> (0,)."""
    cleaned = (text or "").strip().lstrip("vV")
    cleaned = cleaned.split("-")[0].split("+")[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _parse_version(remote) > _parse_version(local)


def is_configured() -> bool:
    """False while the placeholder owner/repo are still in place."""
    return GITHUB_OWNER != "your-github-user" and bool(GITHUB_REPO)


def windows_file_version(version: str) -> str:
    """Convert an app version such as 3.1.8-beta to Windows' 3.1.8.0."""
    parts = list(_parse_version(version))
    return ".".join(str(value) for value in (parts + [0, 0, 0, 0])[:4])


def expected_installer_name(tag: str) -> str:
    clean_tag = str(tag or "").strip().lstrip("vV")
    return f"{VERSIONED_INSTALLER_PREFIX}v{clean_tag}.exe"


def installer_product_version(path: str | Path) -> str:
    """Read ProductVersion from a Windows executable without extra packages."""
    installer = Path(path)
    if not installer.is_file():
        raise ValueError(f"Nie znaleziono instalatora: {installer}")
    if not sys.platform.startswith("win"):
        return ""
    env = os.environ.copy()
    env["SIEKACZ_INSTALLER_TO_CHECK"] = str(installer)
    command = (
        "$item = Get-Item -LiteralPath $env:SIEKACZ_INSTALLER_TO_CHECK; "
        "[Console]::Write(($item.VersionInfo.ProductVersion | Out-String).Trim())"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Nie udało się sprawdzić wersji instalatora. {detail}")
    return completed.stdout.strip()


def validate_installer_version(path: str | Path, release_version: str) -> None:
    """Reject stale or incorrectly labelled release installers."""
    if not sys.platform.startswith("win"):
        return
    actual = installer_product_version(path)
    expected = windows_file_version(release_version)
    if _parse_version(actual) != _parse_version(expected):
        raise ValueError(
            "Pobrany instalator ma inną wersję niż wydanie GitHub. "
            f"Wydanie: {release_version}, instalator: {actual or 'brak wersji'}. "
            "Aktualizacja została zatrzymana, żeby nie zainstalować starej wersji."
        )


def fetch_latest_release(owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO) -> UpdateInfo | None:
    """Query GitHub for the latest release. Returns None on any failure."""
    url = _API_URL.format(owner=owner, repo=repo)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"SIEKACZ9000/{APP_VERSION}",
        },
    )
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S, context=context) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # network down, 404, rate-limit, bad JSON, ...
        _logger.info("Update check failed: %s", exc)
        return None

    tag = str(data.get("tag_name") or "")
    if not tag:
        return None

    assets = list(data.get("assets", []) or [])
    expected_name = expected_installer_name(tag)
    selected = next(
        (asset for asset in assets if str(asset.get("name") or "").casefold() == expected_name.casefold()),
        None,
    )
    if selected is None:
        selected = next(
            (asset for asset in assets if str(asset.get("name") or "").casefold() == GENERIC_INSTALLER_NAME.casefold()),
            None,
        )
    if selected is None:
        selected = next(
            (
                asset
                for asset in assets
                if str(asset.get("name") or "").lower().endswith(ASSET_SUFFIXES)
            ),
            None,
        )
    asset_url = str(selected.get("browser_download_url") or "") if selected else ""
    asset_name = str(selected.get("name") or "") if selected else ""

    return UpdateInfo(
        version=".".join(str(n) for n in _parse_version(tag)),
        tag=tag,
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or "Brak opisu zmian."),
        html_url=str(data.get("html_url") or ""),
        asset_url=asset_url,
        asset_name=asset_name,
    )


def download_asset(info: UpdateInfo, progress=None) -> Path:
    """Download the release installer to a temp file and return its path."""
    if not info.asset_url:
        raise ValueError("Ten release nie ma pliku instalatora do pobrania.")
    target = Path(tempfile.gettempdir()) / (info.asset_name or expected_installer_name(info.tag))
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        info.asset_url, headers={"User-Agent": f"SIEKACZ9000/{APP_VERSION}"}
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=60, context=context) as response, open(partial, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
            read += len(chunk)
            if progress and total:
                progress(read / total)
    partial.replace(target)
    try:
        validate_installer_version(target, info.version)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


class UpdateCheckWorker(QObject):
    """Runs :func:`fetch_latest_release` off the UI thread.

    Emits :attr:`update_available` with an :class:`UpdateInfo` only when a strictly
    newer version is published; :attr:`no_update` otherwise (or on error).
    """

    update_available = Signal(object)
    no_update = Signal()

    def run(self) -> None:
        if not is_configured():
            self.no_update.emit()
            return
        info = fetch_latest_release()
        if info is not None and is_newer(info.version):
            self.update_available.emit(info)
        else:
            self.no_update.emit()


def start_background_check(parent: QObject, on_available, on_none=None) -> QThread:
    """Spin up a QThread that checks for updates and calls back on the UI thread.

    The returned thread is owned by ``parent``; keep a reference so it is not
    garbage-collected mid-flight.
    """
    thread = QThread(parent)
    worker = UpdateCheckWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.update_available.connect(on_available)
    if on_none:
        worker.no_update.connect(on_none)
    worker.update_available.connect(thread.quit)
    worker.no_update.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    # Keep refs alive on the parent.
    parent._update_thread = thread  # type: ignore[attr-defined]
    parent._update_worker = worker  # type: ignore[attr-defined]
    thread.start()
    return thread
