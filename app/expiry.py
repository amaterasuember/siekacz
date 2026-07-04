"""
Time-lock dla SIEKACZ 9000.

Od daty LOCK_DATE aplikacja blokuje się przy starcie i wymaga
kodu autoryzacyjnego. PIN przechowywany jest jako hash SHA-256
(nigdy jako tekst jawny).

Żeby zaktualizować datę lub PIN:
  - zmień LOCK_DATE
  - oblicz nowy hash: python -c "import hashlib; print(hashlib.sha256(b'TWOJ_PIN').hexdigest())"
  - wklej wynik do _PIN_HASH
"""
from __future__ import annotations

import hashlib
from datetime import date

# Data zablokowania aplikacji (rok, miesiąc, dzień)
LOCK_DATE = date(2027, 8, 12)

# SHA-256 kodu autoryzacyjnego.  Nigdy nie przechowuj PIN-u wprost.
# Aby wygenerować hash: python -c "import hashlib; print(hashlib.sha256(b'PIN').hexdigest())"
_PIN_HASH = hashlib.sha256(b"1984").hexdigest()


def is_locked() -> bool:
    """True jeśli dzisiejsza data jest >= LOCK_DATE."""
    return date.today() >= LOCK_DATE


def _pin_correct(pin: str) -> bool:
    return hashlib.sha256(pin.strip().encode()).hexdigest() == _PIN_HASH


def check_and_prompt(parent=None) -> bool:  # type: ignore[assignment]
    """
    Wyświetla dialog blokady jeśli aplikacja wygasła.
    Zwraca True jeśli można kontynuować, False jeśli należy wyjść.
    """
    if not is_locked():
        return True

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
    )

    dlg = QDialog(parent)
    dlg.setWindowTitle("SIEKACZ 9000 — Wymagana autoryzacja")
    dlg.setModal(True)
    dlg.setMinimumWidth(400)
    # Blokuj klawisze Esc i przycisk X — użytkownik musi wpisać kod lub zamknąć.
    dlg.setWindowFlags(
        (dlg.windowFlags() | Qt.WindowType.CustomizeWindowHint)
        & ~Qt.WindowType.WindowCloseButtonHint
    )

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(28, 28, 28, 28)
    layout.setSpacing(12)

    title = QLabel("Wersja wygasła")
    title.setStyleSheet(
        "font-size: 18px; font-weight: 700; color: #f1f5f9;"
    )

    info = QLabel(
        "Okres ważności tej wersji SIEKACZ 9000 upłynął "
        f"({LOCK_DATE.strftime('%d.%m.%Y')}).<br><br>"
        "Jeśli posiadasz aktualny kod autoryzacyjny, wpisz go poniżej.<br>"
        "W przeciwnym razie pobierz nową wersję programu."
    )
    info.setWordWrap(True)
    info.setTextFormat(Qt.TextFormat.RichText)
    info.setStyleSheet("color: #94a3b8; font-size: 13px;")

    pin_edit = QLineEdit()
    pin_edit.setEchoMode(QLineEdit.EchoMode.Password)
    pin_edit.setPlaceholderText("Kod autoryzacyjny")
    pin_edit.setMinimumHeight(38)
    pin_edit.setStyleSheet(
        "QLineEdit { padding: 6px 10px; border-radius: 6px;"
        " border: 1px solid #334155; background: #1e293b; color: #f1f5f9; }"
        "QLineEdit:focus { border-color: #3b82f6; }"
    )

    unlock_btn = QPushButton("Odblokuj")
    unlock_btn.setObjectName("primaryButton")
    unlock_btn.setMinimumHeight(36)
    unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    unlock_btn.setDefault(True)

    close_btn = QPushButton("Zamknij program")
    close_btn.setObjectName("smallButton")
    close_btn.setMinimumHeight(36)
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch(1)
    btn_row.addWidget(close_btn)
    btn_row.addWidget(unlock_btn)

    layout.addWidget(title)
    layout.addSpacing(4)
    layout.addWidget(info)
    layout.addSpacing(8)
    layout.addWidget(pin_edit)
    layout.addSpacing(4)
    layout.addLayout(btn_row)

    _result: list[bool] = [False]

    def _try_unlock() -> None:
        if _pin_correct(pin_edit.text()):
            _result[0] = True
            dlg.accept()
        else:
            pin_edit.clear()
            pin_edit.setStyleSheet(
                pin_edit.styleSheet().replace(
                    "border: 1px solid #334155", "border: 1px solid #ef4444"
                )
            )
            pin_edit.setPlaceholderText("Nieprawidłowy kod — spróbuj ponownie")

    def _restore_border() -> None:
        pin_edit.setStyleSheet(
            pin_edit.styleSheet().replace(
                "border: 1px solid #ef4444", "border: 1px solid #334155"
            )
        )

    unlock_btn.clicked.connect(_try_unlock)
    close_btn.clicked.connect(dlg.reject)
    pin_edit.returnPressed.connect(_try_unlock)
    pin_edit.textChanged.connect(_restore_border)

    dlg.exec()
    return _result[0]
