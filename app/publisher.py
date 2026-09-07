from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.updater import (
    GENERIC_INSTALLER_NAME,
    GITHUB_OWNER,
    GITHUB_REPO,
    expected_installer_name,
    validate_installer_version,
)
from app.version import APP_VERSION
from database.repositories import delete_setting

_logger = logging.getLogger(__name__)


class PublisherWorker(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, token: str, version: str, notes: str):
        super().__init__()
        self.token = token
        self.version = version
        self.notes = notes
        self._version_file: Path | None = None
        self._original_version_content: str | None = None

    def run(self) -> None:
        try:
            self.progress.emit("Zlecanie natywnych kompilacji Windows, macOS i Linux...")
            self._dispatch_multiplatform_release()
            self.finished.emit(
                True,
                "Publikacja została przekazana do GitHub Actions. "
                "Workflow zbuduje i dołączy paczki dla wszystkich obsługiwanych systemów.",
            )
        except Exception as exc:
            _logger.exception("Błąd publikacji")
            self.finished.emit(False, str(exc))

    def _dispatch_multiplatform_release(self) -> None:
        """Run the native multi-OS release workflow on GitHub-hosted runners."""
        url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/"
            "workflows/release-multiplatform.yml/dispatches"
        )
        payload = {
            "ref": "main",
            "inputs": {"version": self.version, "notes": self.notes},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers("application/json"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request):
                return
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                raise RuntimeError(
                    "Nie znaleziono workflow publikacji na GitHubie (404). "
                    "Najpierw wypchnij na gałąź main plik "
                    ".github/workflows/release-multiplatform.yml, a następnie uruchom publikację ponownie.\n"
                    f"GitHub API: {exc.code}\n{details}"
                ) from exc
            if exc.code in {401, 403}:
                raise RuntimeError(
                    "GitHub odrzucił dostęp do publikacji. Token musi należeć do właściciela repozytorium "
                    "i mieć dostęp do repozytorium oraz uprawnienia Contents: write i Actions: write.\n"
                    f"GitHub API: {exc.code}\n{details}"
                ) from exc
            raise RuntimeError(
                "Nie udało się uruchomić publikacji wieloplatformowej.\n"
                f"GitHub API: {exc.code}\n{details}"
            ) from exc

    def _bump_version(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        version_file = root_dir / "app" / "version.py"
        content = version_file.read_text(encoding="utf-8")
        new_content = re.sub(r'APP_VERSION\s*=\s*".*?"', f'APP_VERSION = "{self.version}"', content)
        if new_content == content and f'APP_VERSION = "{self.version}"' not in content:
            raise RuntimeError("Nie udało się zaktualizować APP_VERSION.")
        self._version_file = version_file
        self._original_version_content = content
        version_file.write_text(new_content, encoding="utf-8")

    def _restore_version_file(self) -> None:
        """Undo a failed publication's temporary source-version update."""
        if self._version_file is None or self._original_version_content is None:
            return
        try:
            self._version_file.write_text(self._original_version_content, encoding="utf-8")
        except OSError:
            _logger.exception("Nie udało się przywrócić app/version.py po błędzie publikacji")

    def _build_installer(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        bat_file = root_dir / "scripts" / "BUILD_INSTALLER.bat"
        expected_path = root_dir / "dist" / "installer" / expected_installer_name(f"v{self.version}")
        build_started = time.time()
        if expected_path.exists():
            expected_path.unlink()
        proc = subprocess.run(
            [str(bat_file)],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            input="\n" * 10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Błąd budowania instalatora (kod {proc.returncode}):\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        if not expected_path.is_file():
            raise RuntimeError(f"Budowanie nie utworzyło instalatora bieżącej wersji: {expected_path.name}")
        if expected_path.stat().st_mtime < build_started:
            raise RuntimeError("Instalator nie został przebudowany w tej sesji publikacji.")
        validate_installer_version(expected_path, self.version)

    def _create_release(self) -> str:
        tag = f"v{self.version}"
        existing_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{tag}"
        try:
            with urllib.request.urlopen(urllib.request.Request(existing_url, headers=self._headers())) as response:
                existing = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError(f"Błąd API GitHuba podczas sprawdzania wydania: {exc.code}") from exc
            existing = None

        payload = {
            "name": f"SIEKACZ 9000 v{self.version}",
            "body": self.notes,
            "draft": False,
            # Existing installations use GitHub's /releases/latest endpoint.
            # Keep public beta builds visible there until update channels are
            # introduced explicitly in the client.
            "prerelease": False,
        }
        if existing:
            release_id = int(existing["id"])
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/{release_id}"
            method = "PATCH"
        else:
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
            payload["tag_name"] = tag
            method = "POST"

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers("application/json"),
            method=method,
        )
        try:
            with urllib.request.urlopen(request) as response:
                release = json.loads(response.read().decode("utf-8"))
                return release["upload_url"].split("{")[0]
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8")
            raise RuntimeError(f"Błąd API GitHuba: {exc.code}\n{details}") from exc

    def _upload_asset(self, upload_url: str) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        asset_name = expected_installer_name(f"v{self.version}")
        installer_path = root_dir / "dist" / "installer" / asset_name
        if not installer_path.is_file():
            raise RuntimeError("Nie znaleziono pliku instalatora po kompilacji.")
        validate_installer_version(installer_path, self.version)
        self._delete_existing_installers()
        request = urllib.request.Request(
            f"{upload_url}?name={asset_name}",
            data=installer_path.read_bytes(),
            headers=self._headers("application/vnd.microsoft.portable-executable"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request):
                pass
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8")
            raise RuntimeError(f"Błąd przesyłania pliku: {exc.code}\n{details}") from exc

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"SIEKACZ9000-Publisher/{self.version}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _delete_existing_installers(self) -> None:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/v{self.version}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=self._headers())) as response:
            release = json.loads(response.read().decode("utf-8"))
        for asset in release.get("assets", []) or []:
            name = str(asset.get("name") or "")
            if (
                name.casefold() != GENERIC_INSTALLER_NAME.casefold()
                and not name.casefold().startswith("siekacz9000_setup_v")
            ):
                continue
            delete_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/assets/{asset['id']}"
            request = urllib.request.Request(delete_url, headers=self._headers(), method="DELETE")
            with urllib.request.urlopen(request):
                pass


class PublisherDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Opublikuj aktualizację")
        self.setModal(True)
        self.setMinimumSize(520, 420)
        # A GitHub PAT grants write access to releases.  Do not keep it in the
        # JSON value column of the local SQLite database.  A developer who wants
        # a prefilled value can set SIEKACZ_GITHUB_PAT in their private shell.
        delete_setting("github_pat")  # remove a legacy plaintext value, if any
        self.token = os.environ.get("SIEKACZ_GITHUB_PAT", "")

        layout = QVBoxLayout(self)
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(f"Nowa wersja (obecna: {APP_VERSION}):"))
        self.version_in = QLineEdit(self._suggest_version())
        self.version_in.setObjectName("premiumInput")
        version_row.addWidget(self.version_in)
        layout.addLayout(version_row)

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("GitHub PAT:"))
        self.token_in = QLineEdit(self.token)
        self.token_in.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_in.setObjectName("premiumInput")
        self.token_in.setToolTip("Wymagane uprawnienia: Contents — write oraz Actions — write")
        token_row.addWidget(self.token_in)
        layout.addLayout(token_row)

        layout.addWidget(QLabel("Notatki do wydania:"))
        self.notes_in = QTextEdit()
        self.notes_in.setObjectName("premiumInput")
        layout.addWidget(self.notes_in)
        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.btn_cancel = QPushButton("Anuluj")
        self.btn_cancel.setObjectName("smallButton")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_publish = QPushButton("Opublikuj wydanie")
        self.btn_publish.setObjectName("primaryButton")
        self.btn_publish.clicked.connect(self._start_publish)
        buttons.addStretch()
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_publish)
        layout.addLayout(buttons)

    def _suggest_version(self) -> str:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", APP_VERSION)
        if match:
            major, minor, patch = (int(value) for value in match.groups())
            return f"{major}.{minor}.{patch + 1}"
        return APP_VERSION

    def _start_publish(self) -> None:
        token = self.token_in.text().strip()
        version = self.version_in.text().strip().lstrip("vV")
        notes = self.notes_in.toPlainText().strip()
        if not token or not version or not notes:
            QMessageBox.warning(self, "Błąd", "Wypełnij wersję, token i changelog.")
            return
        self.btn_publish.setEnabled(False)
        self.progress.show()
        self._publish_thread = QThread(self)
        self.worker = PublisherWorker(token, version, notes)
        self.worker.moveToThread(self._publish_thread)
        self._publish_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status_lbl.setText)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self._publish_thread.quit)
        self._publish_thread.finished.connect(self.worker.deleteLater)
        self._publish_thread.finished.connect(self._publish_thread.deleteLater)
        self._publish_thread.finished.connect(lambda: setattr(self, "_publish_thread", None))
        self._publish_thread.start()

    def _on_finished(self, success: bool, message: str) -> None:
        self.progress.hide()
        self.btn_publish.setEnabled(True)
        if success:
            QMessageBox.information(self, "Sukces", message)
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", message)

    def reject(self) -> None:
        thread = getattr(self, "_publish_thread", None)
        if thread is not None and thread.isRunning():
            QMessageBox.warning(self, "Operacja w toku", "Nie można zamknąć okna podczas publikacji.")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if getattr(self, "_publish_thread", None) is not None and self._publish_thread.isRunning():
            QMessageBox.warning(self, "Operacja w toku", "Nie można zamknąć okna podczas publikacji.")
            event.ignore()
            return
        super().closeEvent(event)
