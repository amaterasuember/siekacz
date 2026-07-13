from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QTextEdit, QPushButton, QMessageBox, QProgressBar
)

from app.version import APP_VERSION
from database.repositories import get_setting, set_setting
from app.updater import GITHUB_OWNER, GITHUB_REPO

_logger = logging.getLogger(__name__)

class PublisherWorker(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, token: str, version: str, notes: str):
        super().__init__()
        self.token = token
        self.version = version
        self.notes = notes

    def run(self):
        try:
            self.progress.emit("Podbijanie wersji w app/version.py...")
            self._bump_version()

            self.progress.emit("Kompilacja aplikacji i instalatora...")
            self._build_installer()

            self.progress.emit("Tworzenie wydania na GitHubie...")
            upload_url = self._create_release()

            self.progress.emit("Wysyłanie pliku instalatora...")
            self._upload_asset(upload_url)

            self.finished.emit(True, "Wydanie zostało pomyślnie opublikowane!")
        except Exception as e:
            _logger.exception("Błąd publikacji")
            self.finished.emit(False, str(e))

    def _bump_version(self):
        root_dir = Path(__file__).resolve().parents[1]
        version_file = root_dir / "app" / "version.py"
        content = version_file.read_text(encoding="utf-8")
        new_content = re.sub(r'APP_VERSION\s*=\s*".*?"', f'APP_VERSION = "{self.version}"', content)
        version_file.write_text(new_content, encoding="utf-8")

    def _build_installer(self):
        root_dir = Path(__file__).resolve().parents[1]
        bat_file = root_dir / "scripts" / "BUILD_INSTALLER.bat"
        
        proc = subprocess.run(
            [str(bat_file)],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            input="\n" * 10,  # Skip any "pause" commands in batch scripts
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Błąd budowania instalatora (kod {proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    def _create_release(self) -> str:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
        data = {
            "tag_name": f"v{self.version}",
            "name": f"SIEKACZ 9000 v{self.version}",
            "body": self.notes,
            "draft": False,
            "prerelease": False
        }
        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers={
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }, method="POST")

        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["upload_url"].split("{")[0]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise RuntimeError(f"Błąd API GitHuba (Tworzenie Release): {e.code}\n{err_body}")

    def _upload_asset(self, upload_url: str):
        root_dir = Path(__file__).resolve().parents[1]
        installer_path = root_dir / "dist" / "installer" / "SIEKACZ9000_Setup.exe"
        if not installer_path.exists():
            raise RuntimeError("Nie znaleziono pliku instalatora po kompilacji!")

        with open(installer_path, "rb") as f:
            file_data = f.read()

        url = f"{upload_url}?name=SIEKACZ9000_Setup.exe"
        req = urllib.request.Request(url, data=file_data, headers={
            "Authorization": f"token {self.token}",
            "Content-Type": "application/vnd.microsoft.portable-executable",
            "Accept": "application/vnd.github.v3+json"
        }, method="POST")

        try:
            with urllib.request.urlopen(req) as response:
                pass
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise RuntimeError(f"Błąd przesyłania pliku: {e.code}\n{err_body}")


class PublisherDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Opublikuj aktualizację (Wydanie)")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        self.token = get_setting("github_pat", "")
        
        layout = QVBoxLayout(self)
        
        # Version
        ver_layout = QHBoxLayout()
        ver_layout.addWidget(QLabel("Nowa wersja (obecna: " + APP_VERSION + "):"))
        self.version_in = QLineEdit(self._suggest_version())
        self.version_in.setObjectName("premiumInput")
        ver_layout.addWidget(self.version_in)
        layout.addLayout(ver_layout)
        
        # Token
        tok_layout = QHBoxLayout()
        tok_layout.addWidget(QLabel("GitHub PAT:"))
        self.token_in = QLineEdit(self.token)
        self.token_in.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_in.setObjectName("premiumInput")
        tok_layout.addWidget(self.token_in)
        layout.addLayout(tok_layout)
        
        # Notes
        layout.addWidget(QLabel("Notatki do wydania (Changelog):"))
        self.notes_in = QTextEdit()
        self.notes_in.setObjectName("premiumInput")
        layout.addWidget(self.notes_in)
        
        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Anuluj")
        self.btn_cancel.setObjectName("smallButton")
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_publish = QPushButton("Opublikuj Wydanie")
        self.btn_publish.setObjectName("primaryButton")
        self.btn_publish.clicked.connect(self._start_publish)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_publish)
        layout.addLayout(btn_layout)

    def _suggest_version(self):
        parts = APP_VERSION.split('.')
        if len(parts) >= 3:
            try:
                parts[-1] = str(int(parts[-1]) + 1)
                return ".".join(parts)
            except: pass
        return APP_VERSION

    def _start_publish(self):
        token = self.token_in.text().strip()
        version = self.version_in.text().strip()
        notes = self.notes_in.toPlainText().strip()
        
        if not token or not version or not notes:
            QMessageBox.warning(self, "Błąd", "Wypełnij wszystkie pola (Wersja, Token, Changelog).")
            return
            
        set_setting("github_pat", token)
        
        self.btn_publish.setEnabled(False)
        self.progress.show()
        
        self.thread = QThread(self)
        self.worker = PublisherWorker(token, version, notes)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status_lbl.setText)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _on_finished(self, success: bool, message: str):
        self.progress.hide()
        self.btn_publish.setEnabled(True)
        if success:
            QMessageBox.information(self, "Sukces", message)
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", message)
            
    def closeEvent(self, event):
        if hasattr(self, "thread") and self.thread.isRunning():
            QMessageBox.warning(self, "Operacja w toku", "Nie można zamknąć okna, podczas gdy trwa proces publikacji.")
            event.ignore()
        else:
            super().closeEvent(event)
