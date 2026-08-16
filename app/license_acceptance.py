from __future__ import annotations

"""Mandatory, per-release acceptance of the SIEKACZ 9000 EULA."""

import hashlib
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.license_text import LICENSE_NAME, full_license_text
from app.version import APP_VERSION
from database.repositories import get_setting, set_setting


_ACCEPTANCE_SETTING = "license_acceptance"


def current_license_revision() -> str:
    """Stable hash makes a changed EULA require acceptance even within one build."""
    return hashlib.sha256(full_license_text().encode("utf-8")).hexdigest()


def acceptance_is_current(record: Any, version: str = APP_VERSION, revision: str | None = None) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("version") == version
        and record.get("revision") == (revision or current_license_revision())
        and bool(record.get("accepted_at"))
    )


class LicenseAcceptanceDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SIEKACZ 9000 - akceptacja licencji")
        self.setModal(True)
        self.resize(820, 680)
        self.setMinimumSize(700, 540)

        title = QLabel("Warunki korzystania z SIEKACZ 9000")
        title.setObjectName("settingsTitle")
        subtitle = QLabel(
            f"Wersja {APP_VERSION} wymaga akceptacji: {LICENSE_NAME}. "
            "Licencja pozwala korzystac z programu bezplatnie, takze komercyjnie, "
            "ale zakazuje odsprzedazy i ingerencji w Program."
        )
        subtitle.setObjectName("summaryLabel")
        subtitle.setWordWrap(True)

        license_view = QTextEdit()
        license_view.setObjectName("premiumInput")
        license_view.setReadOnly(True)
        license_view.setPlainText(full_license_text())

        self._accept = QPushButton("Akceptuje i uruchamiam")
        self._accept.setObjectName("primaryButton")
        self._accept.setCursor(Qt.CursorShape.PointingHandCursor)
        decline = QPushButton("Nie akceptuje")
        decline.setObjectName("smallButton")
        decline.setCursor(Qt.CursorShape.PointingHandCursor)

        self._accept.clicked.connect(self.accept)
        decline.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(decline)
        buttons.addStretch(1)
        buttons.addWidget(self._accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(license_view, 1)
        layout.addLayout(buttons)


def ensure_current_license_accepted(parent: QWidget | None = None) -> bool:
    """Ask once per app version and EULA revision; decline means no app session."""
    revision = current_license_revision()
    if acceptance_is_current(get_setting(_ACCEPTANCE_SETTING), APP_VERSION, revision):
        return True

    dialog = LicenseAcceptanceDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False

    set_setting(
        _ACCEPTANCE_SETTING,
        {
            "version": APP_VERSION,
            "revision": revision,
            "accepted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return True
