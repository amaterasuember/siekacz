from __future__ import annotations

import math
import multiprocessing as mp
import shutil
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from dataclasses import replace
from functools import wraps
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSequentialAnimationGroup,
    QSize,
    QThread,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Property,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedLayout,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

from app import project_history
from app.calculation_process import calculation_process_entry
from app.logging_setup import log_path
from app.material_catalog import (
    MaterialCatalogEntry,
    catalog_family_label,
    catalog_from_dicts,
    catalog_revision,
    read_material_catalog,
)
from app.theme import apply_accent_mode, apply_button_cursors, apply_native_title_bar, apply_theme
from app.version import APP_VERSION
from core.models import OptimizationSettings, Project, SheetPart, SheetStock, materials_are_compatible
from core.validation import getValidationErrors, safeNumber, sanitizeProjectState, validatePart, validatePlate
from database import repositories
from core.i18n import tr as _tr  # T5-5: exposed for future locale rollout
from import_export.image_export import export_scene_png
from ui.layout_view import LayoutView
from ui.optimization_progress import OptimizationProgressOverlay
from ui.toggle_switch import ToggleSwitch
from workers.optimizer_worker import optimize_sheet_project

import logging
_logger = logging.getLogger(__name__)

MAX_TOTAL_PARTS = 15_000
PART_LIMIT_OVERRIDE_PIN = "1984"
FIXED_SHEET_PRESETS: tuple[tuple[float, float], ...] = (
    (1000.0, 2000.0),
    (1250.0, 2500.0),
    (2050.0, 3050.0),
    (1500.0, 3000.0),
)

# Stock table layout is intentionally explicit. Board material is derived from
# the active part; FORMAT is a compact per-board picker, not a global selector.
STOCK_MATERIAL_COLUMN = 0
STOCK_THICKNESS_COLUMN = 1
STOCK_HEIGHT_COLUMN = 2
STOCK_WIDTH_COLUMN = 3
STOCK_FORMAT_COLUMN = 4
STOCK_QUANTITY_COLUMN = 5
STOCK_PRIORITY_COLUMN = 6
STOCK_STACK_COLUMN = 7
STOCK_CUT_AXIS_ROLE = int(Qt.ItemDataRole.UserRole) + 17
STOCK_TEMPLATE_ROLE = int(Qt.ItemDataRole.UserRole) + 18
LINK_GLOW_ROLE = int(Qt.ItemDataRole.UserRole) + 19
CELL_ERROR_ROLE = int(Qt.ItemDataRole.UserRole) + 20
PART_ALLOW_ROTATION_ROLE = int(Qt.ItemDataRole.UserRole) + 21
PART_PRIORITY_ROLE = int(Qt.ItemDataRole.UserRole) + 22
PART_LABEL_ROLE = int(Qt.ItemDataRole.UserRole) + 23
PART_NOTES_ROLE = int(Qt.ItemDataRole.UserRole) + 24
STOCK_ALLOW_ROTATION_ROLE = int(Qt.ItemDataRole.UserRole) + 25

PART_MATERIAL_COLUMN = 0
PART_THICKNESS_COLUMN = 1
PART_HEIGHT_COLUMN = 2
PART_WIDTH_COLUMN = 3
PART_QUANTITY_COLUMN = 4


def _format_table_number(value: object) -> str:
    """Render whole millimetre values without the float suffix (20, not 20.0)."""
    if value is None:
        return ""
    number = safeNumber(value)
    return f"{number:g}" if number is not None else str(value)


def _format_piece_count(value: int) -> str:
    """Format a quantity for Polish UI without the English comma separator."""
    return f"{int(value):,}".replace(",", " ")


def _material_badge_spec(material: str) -> tuple[str, str, str]:
    """Return compact label and colors for a material marker in input tables."""
    text = str(material or "").strip()
    if not text:
        return "-", "#273244", "#a8b6c8"

    normalized = text.upper().replace("-", " ")
    tokens = normalized.split()
    if "CZARN" in normalized:
        background, foreground = "#202936", "#f3f7ff"
    elif "ZIELON" in normalized:
        background, foreground = "#188c63", "#effff8"
    elif "NATUR" in normalized:
        background, foreground = "#ffffff", "#111827"
    elif "NIEBIESK" in normalized:
        background, foreground = "#2777c9", "#eff8ff"
    elif "PP" in tokens and "SZAR" in normalized:
        # The supplier calls the board grey, but its physical marker is the
        # warm cream shade used on the shop floor.
        background, foreground = "#eadfbd", "#3b3425"
    else:
        background, foreground = "#4b5f7d", "#f1f6ff"

    if "PE" in normalized and "1000" in normalized:
        return "PE1000", background, foreground
    if "PE" in normalized and "300" in normalized:
        return "PE300", background, foreground
    if "PA6G" in normalized or "PA6 G" in normalized:
        return "PA6G", background, foreground
    if "PA6" in normalized:
        return "PA6", background, foreground
    if "POM" in normalized and "C" in tokens:
        return "POM-C", background, foreground
    if "POM" in normalized and "H" in tokens:
        return "POM-H", background, foreground
    if "POM" in normalized:
        return "POM", background, foreground
    if normalized.startswith("PE"):
        return "PE", background, foreground
    return text.split()[0][:10].upper(), background, foreground


def _stack_icon(color: str = "#b8cdf0") -> QIcon:
    """Create a small stack-of-sheets icon without relying on a font glyph."""
    pixmap = QPixmap(18, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.25)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for x, y in ((5, 1), (3, 4), (1, 7)):
        painter.drawRoundedRect(QRectF(x, y, 12, 7), 1.4, 1.4)
    painter.end()
    return QIcon(pixmap)


def _eye_icon(color: str = "#b8cdf0") -> QIcon:
    """Compact preview icon for the in-app PDF report."""
    pixmap = QPixmap(18, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.35)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(1.0, 2.0, 16.0, 10.0))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(7.0, 4.0, 4.0, 4.0))
    painter.end()
    return QIcon(pixmap)


def _upload_icon(color: str = "#b8cdf0") -> QIcon:
    """Draw a compact upload-to-tray glyph for the DXF import action."""
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(10, 3), QPointF(10, 12))
    painter.drawLine(QPointF(6.2, 7), QPointF(10, 3))
    painter.drawLine(QPointF(13.8, 7), QPointF(10, 3))
    painter.drawLine(QPointF(4, 12), QPointF(4, 16.5))
    painter.drawLine(QPointF(4, 16.5), QPointF(16, 16.5))
    painter.drawLine(QPointF(16, 16.5), QPointF(16, 12))
    painter.end()
    return QIcon(pixmap)


def _gear_icon(color: str = "#b8cdf0") -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    center = QPointF(10, 10)
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        inner = QPointF(center.x() + math.cos(radians) * 6.0, center.y() + math.sin(radians) * 6.0)
        outer = QPointF(center.x() + math.cos(radians) * 8.0, center.y() + math.sin(radians) * 8.0)
        painter.drawLine(inner, outer)
    painter.drawEllipse(QRectF(4.0, 4.0, 12.0, 12.0))
    painter.drawEllipse(QRectF(8.0, 8.0, 4.0, 4.0))
    painter.end()
    return QIcon(pixmap)


def _polish_sheet_count(count: int, missing: bool = False) -> str:
    """Return the correct Polish plural form for board counts in notifications."""
    value = max(0, int(count))
    last_two = value % 100
    last = value % 10
    few = last in (2, 3, 4) and not 12 <= last_two <= 14
    if missing:
        noun = "brakująca płyta" if value == 1 else "brakujące płyty" if few else "brakujących płyt"
    else:
        noun = "płyta" if value == 1 else "płyty" if few else "płyt"
    return f"{value} {noun}"


class _WheelHScrollFilter(QObject):
    """Converts vertical wheel events on the nav chips to horizontal scroll."""

    def __init__(self, scroll_area: "QScrollArea") -> None:
        super().__init__(scroll_area)
        self._scroll = scroll_area

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            if delta:
                bar = self._scroll.horizontalScrollBar()
                step = int(delta / 120 * 80)
                bar.setValue(bar.value() - step)
                event.accept()
                return True
        return False


class _OpenMaterialPopupFilter(QObject):
    """Open an editable material combo on click while keeping its hint empty."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo
        self._placeholder = combo.lineEdit().placeholderText()

    def _restore_placeholder(self) -> None:
        self._combo.setCurrentIndex(-1)
        self._combo.lineEdit().clear()
        self._combo.lineEdit().setPlaceholderText(self._placeholder)
        self._combo.lineEdit().setFocus()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            # Keep the native click alive so the editor receives focus on the
            # first press.  Swallowing it makes the popup look open while the
            # user cannot type until a second click.
            keep_placeholder = self._combo.currentIndex() < 0
            self._combo.showPopup()
            if keep_placeholder:
                QTimer.singleShot(0, self._restore_placeholder)
            else:
                QTimer.singleShot(0, self._combo.lineEdit().setFocus)
            return False
        return False


class _CanvasOverlayFilter(QObject):
    """Event filter that repositions the zoom widget in the top-right corner of the canvas.

    The warning banner lives in the right_layout ABOVE the canvas (not inside it),
    so it never overlaps any scene content.  Only the zoom widget is managed here
    as a floating overlay directly inside the canvas widget.
    """

    def __init__(
        self,
        canvas: QWidget,
        zoom: QWidget,
    ) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._zoom = zoom

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._canvas and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        w = self._canvas.width()
        h = self._canvas.height()
        # Vertical pill in bottom-right corner — keeps top-right free for the
        # per-sheet cut-animation panel that lives inside the scene.
        zw = self._zoom.width()
        zh = self._zoom.height()
        self._zoom.setGeometry(w - zw - 14, h - zh - 14, zw, zh)
        self._zoom.raise_()


def _resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


def _number(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _int_number(text: str, default: int = 0) -> int:
    try:
        return int(float(str(text).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _strict_number(text: str) -> float:
    value = float(str(text).strip().replace(",", "."))
    if not math.isfinite(value):
        raise ValueError
    return value


def _strict_int(text: str) -> int:
    value = _strict_number(text)
    if not value.is_integer():
        raise ValueError
    return int(value)


def _display_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def safe_ui_action(message: str) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                text = f"{message}\n{exc}" if str(exc) else message
                if hasattr(self, "warning"):
                    self.warning.setText(message)
                    self.warning.show()
                if hasattr(self, "_is_exporting"):
                    self._is_exporting = False
                if hasattr(self, "png_action"):
                    self.png_action.setEnabled(not getattr(self, "_is_calculating", False))
                if hasattr(self, "_is_calculating"):
                    self._set_calculating(False)
                QMessageBox.warning(self, "SIEKACZ 9000", text)
                self.statusBar().showMessage(message)
                return None

        return wrapper

    return decorator


class ProjectSaveDialog(QDialog):
    def __init__(self, parent: QWidget, name: str = "", client_name: str = "", notes: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Zapisz projekt")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.save_as_new = False

        self.name = QLineEdit(name)
        self.name.setObjectName("premiumInput")
        self.name.setPlaceholderText("Np. Rozkrój - ABC-Technik")
        self.client_name = QLineEdit(client_name)
        self.client_name.setObjectName("premiumInput")
        self.client_name.setPlaceholderText("Nazwa klienta")
        self.notes = QTextEdit(notes)
        self.notes.setObjectName("premiumInput")
        self.notes.setPlaceholderText("Notatki do projektu")
        self.notes.setMinimumHeight(96)

        title = QLabel("Dane projektu")
        title.setObjectName("settingsTitle")
        hint = QLabel("Projekt zapisuje parametry, formatki i ostatni wynik rozkroju.")
        hint.setObjectName("summaryLabel")
        hint.setWordWrap(True)

        save = QPushButton("Zapisz projekt")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.accept)
        save_new = QPushButton("Zapisz jako nowy projekt")
        save_new.setObjectName("smallButton")
        save_new.clicked.connect(self._accept_as_new)
        cancel = QPushButton("Anuluj")
        cancel.setObjectName("smallButton")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(save)
        buttons.addWidget(save_new)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Nazwa projektu"))
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Nazwa klienta"))
        layout.addWidget(self.client_name)
        layout.addWidget(QLabel("Notatki"))
        layout.addWidget(self.notes)
        layout.addLayout(buttons)

    def _accept_as_new(self) -> None:
        self.save_as_new = True
        self.accept()


class ClientNameDialog(QDialog):
    def __init__(self, parent: QWidget, client_name: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Dane eksportu PNG")
        self.setModal(True)
        self.setMinimumWidth(380)

        self.client_name = QLineEdit(client_name)
        self.client_name.setObjectName("premiumInput")
        self.client_name.setPlaceholderText("Nazwa klienta (opcjonalnie)")
        self.export_mode = QComboBox()
        self.export_mode.setObjectName("premiumInput")
        self.export_mode.addItem("Oszczędny - białe formatki i czarne kontury", "economy")
        self.export_mode.addItem("Kolorowy - jak w podglądzie aplikacji", "color")

        title = QLabel("Eksport PNG")
        title.setObjectName("settingsTitle")
        hint = QLabel("Nazwa klienta i data trafią do nagłówka eksportowanego obrazu.")
        hint.setObjectName("summaryLabel")
        hint.setWordWrap(True)

        ok_button = QPushButton("OK")
        ok_button.setObjectName("primaryButton")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Anuluj")
        cancel_button.setObjectName("smallButton")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(ok_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Nazwa klienta"))
        layout.addWidget(self.client_name)
        layout.addWidget(QLabel("Tryb eksportu"))
        layout.addWidget(self.export_mode)
        layout.addLayout(buttons)


class CalculationWorker(QObject):
    """Runs optimization in a separate OS process so Cancel can kill it."""

    finished = Signal(object, object)
    progress = Signal(int, str)

    def __init__(self, project: "Project | list[Project]") -> None:
        super().__init__()
        self.project = project
        self._cancelled = False
        self._process: mp.Process | None = None
        self._parent_conn = None

    def cancel(self) -> None:
        """Immediately terminate the calculation process and its pool children."""
        self._cancelled = True
        self._terminate_process_tree()

    def is_cancelled(self) -> bool:
        return self._cancelled

    def _terminate_process_tree(self) -> None:
        process = self._process
        if process is None:
            return
        pid = process.pid
        if pid is None:
            return
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        try:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        except Exception:
            pass

    def run(self) -> None:
        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=calculation_process_entry,
            args=(self.project, child_conn),
            name="SIEKACZ calculation",
        )
        self._parent_conn = parent_conn
        self._process = process
        try:
            process.start()
            child_conn.close()

            while True:
                if self._cancelled:
                    self._terminate_process_tree()
                    self.finished.emit(None, None)
                    return

                if parent_conn.poll(0.05):
                    kind, payload = parent_conn.recv()
                    if kind == "progress":
                        try:
                            percent, label = payload
                            self.progress.emit(int(percent), str(label))
                        except (TypeError, ValueError):
                            pass
                        continue
                    process.join(timeout=0.5)
                    if self._cancelled:
                        self.finished.emit(None, None)
                        return
                    if kind == "result":
                        self.finished.emit(payload, None)
                    else:
                        self.finished.emit(None, payload)
                    return

                if not process.is_alive():
                    exit_code = process.exitcode
                    process.join(timeout=0.5)
                    if self._cancelled:
                        self.finished.emit(None, None)
                    elif exit_code == 0:
                        self.finished.emit(None, "Proces obliczen zakonczyl sie bez wyniku.")
                    else:
                        self.finished.emit(None, f"Proces obliczen przerwany (kod {exit_code}).")
                    return
        except Exception as exc:
            if self._cancelled:
                self.finished.emit(None, None)
            else:
                _logger.exception("CalculationWorker.run() failed")
                self.finished.emit(None, f"Nie udalo sie uruchomic procesu obliczen: {exc}")
        finally:
            try:
                parent_conn.close()
            except Exception:
                pass
            try:
                child_conn.close()
            except Exception:
                pass
            if self._cancelled:
                self._terminate_process_tree()


class OrderGroupWidget(QWidget):
    """A self-contained extra board group for multi-board orders.

    Each group has its own board type (material + thickness), its own stock
    table and its own formatki table.  Formatki from one group are only cut
    from that group's boards — groups are optimized in complete isolation and
    only the *quote* and the preview are aggregated afterwards.
    """

    remove_requested = Signal(object)

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("orderGroupCard")
        self._index = index

        self._header = QLabel(f"Grupa {index} — inna płyta")
        self._header.setObjectName("sectionTitle")
        header = self._header

        remove_btn = QPushButton("Usuń grupę")
        remove_btn.setObjectName("smallButton")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch(1)
        header_row.addWidget(remove_btn)

        self.material = QLineEdit()
        self.material.setObjectName("premiumInput")
        self.material.setPlaceholderText("Materiał / typ płyty (np. Egger U702)")
        self.thickness = QSpinBox()
        self.thickness.setObjectName("premiumInput")
        self.thickness.setRange(1, 200)
        self.thickness.setValue(18)
        self.thickness.setSuffix(" mm")
        self.thickness.setMaximumWidth(110)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        meta_row.addWidget(self.material, 1)
        meta_row.addWidget(self.thickness)

        self.stock_table = self._make_table(["Szer. (mm)", "Wys. (mm)", "Ilość"])
        self.parts_table = self._make_table(["Szer. (mm)", "Dł. (mm)", "Ilość"])
        self._add_row(self.stock_table)
        self._add_row(self.parts_table)

        stock_btns = self._table_buttons(self.stock_table, "płytę")
        parts_btns = self._table_buttons(self.parts_table, "formatkę")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header_row)
        layout.addLayout(meta_row)
        layout.addWidget(QLabel("Płyty tej grupy"))
        layout.addLayout(stock_btns)
        layout.addWidget(self.stock_table)
        layout.addWidget(QLabel("Formatki tej grupy"))
        layout.addLayout(parts_btns)
        layout.addWidget(self.parts_table)

    # ── construction helpers ────────────────────────────────────────────────
    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMaximumHeight(150)
        return table

    def _table_buttons(self, table: QTableWidget, noun: str) -> QHBoxLayout:
        add = QPushButton(f"Dodaj {noun}")
        add.setObjectName("smallButton")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        add.clicked.connect(lambda: self._add_row(table))
        remove = QPushButton("Usuń wiersz")
        remove.setObjectName("smallButton")
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        remove.clicked.connect(lambda: self._remove_last_row(table))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        return row

    @staticmethod
    def _add_row(table: QTableWidget) -> None:
        r = table.rowCount()
        table.insertRow(r)
        for c in range(table.columnCount()):
            table.setItem(r, c, QTableWidgetItem(""))

    @staticmethod
    def _remove_last_row(table: QTableWidget) -> None:
        if table.rowCount() > 1:
            table.removeRow(table.rowCount() - 1)

    @staticmethod
    def _cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def _row_blank(self, table: QTableWidget, row: int) -> bool:
        return not any(self._cell(table, row, c) for c in range(table.columnCount()))

    def set_index(self, index: int) -> None:
        self._index = index
        self._header.setText(f"Grupa {index} — inna płyta")

    # ── persistence ─────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        def rows(table: QTableWidget) -> list[list[str]]:
            out: list[list[str]] = []
            for r in range(table.rowCount()):
                row = [self._cell(table, r, c) for c in range(table.columnCount())]
                if any(row):
                    out.append(row)
            return out
        return {
            "material": self.material.text(),
            "thickness": int(self.thickness.value()),
            "stock": rows(self.stock_table),
            "parts": rows(self.parts_table),
        }

    def load_dict(self, data: dict) -> None:
        self.material.setText(str(data.get("material", "") or ""))
        try:
            self.thickness.setValue(int(data.get("thickness", 18)))
        except (TypeError, ValueError):
            pass
        self._load_rows(self.stock_table, data.get("stock", []))
        self._load_rows(self.parts_table, data.get("parts", []))

    @staticmethod
    def _load_rows(table: QTableWidget, rows: object) -> None:
        table.setRowCount(0)
        for row in (rows or []):
            r = table.rowCount()
            table.insertRow(r)
            for c in range(table.columnCount()):
                val = str(row[c]) if isinstance(row, (list, tuple)) and c < len(row) else ""
                table.setItem(r, c, QTableWidgetItem(val))
        if table.rowCount() == 0:
            OrderGroupWidget._add_row(table)

    # ── data extraction ─────────────────────────────────────────────────────
    def material_name(self) -> str:
        return self.material.text().strip() or f"Grupa {self._index}"

    def is_empty(self) -> bool:
        stock_blank = all(self._row_blank(self.stock_table, r) for r in range(self.stock_table.rowCount()))
        parts_blank = all(self._row_blank(self.parts_table, r) for r in range(self.parts_table.rowCount()))
        return stock_blank and parts_blank

    def collect_stock(self, allow_rotation: bool) -> list[SheetStock]:
        material = self.material_name()
        thickness = int(self.thickness.value())
        stocks: list[SheetStock] = []
        for row in range(self.stock_table.rowCount()):
            if self._row_blank(self.stock_table, row):
                continue
            stock, errors = validatePlate(
                {
                    "material": material, "thickness": thickness,
                    "width": self._cell(self.stock_table, row, 0),
                    "height": self._cell(self.stock_table, row, 1),
                    "quantity": self._cell(self.stock_table, row, 2),
                    "price": 0, "allow_rotation": allow_rotation,
                    "min_offcut_width": 0, "min_offcut_height": 0, "source": "stock",
                },
                row + 1,
            )
            if errors:
                raise ValueError(f"Grupa „{material}” — płyty:\n" + "\n".join(errors))
            if stock:
                stocks.append(stock)
        return stocks

    def collect_parts(self, allow_rotation: bool) -> list[SheetPart]:
        material = self.material_name()
        thickness = int(self.thickness.value())
        parts: list[SheetPart] = []
        for row in range(self.parts_table.rowCount()):
            if self._row_blank(self.parts_table, row):
                continue
            part, errors = validatePart(
                {
                    "name": "", "width": self._cell(self.parts_table, row, 0),
                    "height": self._cell(self.parts_table, row, 1),
                    "quantity": self._cell(self.parts_table, row, 2),
                    "material": material, "thickness": thickness,
                    "allow_rotation": allow_rotation, "label": "",
                },
                row + 1,
            )
            if errors:
                raise ValueError(f"Grupa „{material}” — formatki:\n" + "\n".join(errors))
            if part is not None:
                parts.append(part)
        return parts


class HoverLiftFilter(QObject):
    """Gives the widgets it watches a subtle drop-shadow "lift" on hover.

    A single instance can be shared across many widgets (e.g. all sheet-nav
    chips).  The drop-shadow effect is created lazily on the first hover so it
    never collides with a fade-in opacity effect that may still be running when
    the widget is first shown (a QWidget can hold only one graphics effect).
    """

    def __init__(self, parent: QObject | None = None, hover_blur: float = 16.0,
                 hover_dy: float = 3.0, duration: int = 150) -> None:
        super().__init__(parent)
        self._hover_blur = float(hover_blur)
        self._hover_dy = float(hover_dy)
        self._duration = int(duration)

    def watch(self, widget: QWidget) -> None:
        widget.installEventFilter(self)

    def _ensure_effect(self, widget: QWidget):
        effect = getattr(widget, "_lift_effect", None)
        # The stored effect may have been torn down (RuntimeError on access) or
        # replaced by None after a fade-in cleanup; recreate it when needed.
        try:
            if effect is not None and widget.graphicsEffect() is effect:
                return effect
        except RuntimeError:
            return None
        from PySide6.QtGui import QColor
        try:
            effect = QGraphicsDropShadowEffect(widget)
            effect.setBlurRadius(0.0)
            effect.setOffset(0.0, 0.0)
            effect.setColor(QColor(0, 0, 0, 130))
            widget.setGraphicsEffect(effect)
        except RuntimeError:
            return None
        widget._lift_effect = effect  # type: ignore[attr-defined]
        return effect

    def _animate(self, widget: QWidget, blur: float, dy: float) -> None:
        effect = self._ensure_effect(widget)
        if effect is None:
            return
        try:
            blur_anim = QPropertyAnimation(effect, b"blurRadius", widget)
            blur_anim.setDuration(self._duration)
            blur_anim.setEndValue(float(blur))
            blur_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            off_anim = QPropertyAnimation(effect, b"offset", widget)
            off_anim.setDuration(self._duration)
            off_anim.setEndValue(QPointF(0.0, float(dy)))
            off_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        except RuntimeError:
            return
        # Keep references so the animations aren't garbage-collected mid-flight.
        widget._lift_anims = (blur_anim, off_anim)  # type: ignore[attr-defined]
        blur_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        off_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.Enter:
            self._animate(obj, self._hover_blur, self._hover_dy)
        elif event_type == QEvent.Type.Leave:
            self._animate(obj, 0.0, 0.0)
        return False


class ThemeToggleSwitch(QWidget):
    """Animated sliding sun/moon switch for light/dark mode.

    Emits :attr:`toggled` with ``True`` for light mode, ``False`` for dark.
    The knob slides and the track colour cross-fades between night and day.
    """

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._light = False
        self._pos = 0.0  # 0.0 = dark (left), 1.0 = light (right)
        self.setFixedSize(60, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Przełącz tryb jasny / ciemny")
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_knob_pos(self) -> float:
        return self._pos

    def _set_knob_pos(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def is_light(self) -> bool:
        return self._light

    def setChecked(self, light: bool, animate: bool = False) -> None:
        light = bool(light)
        self._light = light
        target = 1.0 if light else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pos = target
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._light, animate=True)
            self.toggled.emit(self._light)
        super().mousePressEvent(event)

    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        t = self._pos

        track = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        track_color = self._mix(QColor("#243044"), QColor("#fbbf24"), t)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, h / 2.0, h / 2.0)

        margin = 3.0
        diameter = h - 2.0 * margin
        knob_x = margin + (w - 2.0 * margin - diameter) * t
        knob_rect = QRectF(knob_x, margin, diameter, diameter)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob_rect)

        glyph = "☀" if t > 0.5 else "☽"  # ☀ sun / ☽ moon
        painter.setPen(self._mix(QColor("#334155"), QColor("#b45309"), t))
        font = QFont("Segoe UI Symbol")
        font.setPointSizeF(diameter * 0.52)
        painter.setFont(font)
        painter.drawText(knob_rect, Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("O programie")
        self.setModal(True)
        self.setMinimumWidth(430)

        title = QLabel("SIEKACZ 9000")
        title.setObjectName("settingsTitle")
        subtitle = QLabel(f"Made by Kewin\nWersja {APP_VERSION}")
        subtitle.setObjectName("summaryLabel")
        subtitle.setWordWrap(True)
        details = QLabel(
            "Natywna aplikacja desktopowa do rozkroju płyt.\n"
            f"Log błędów: {log_path()}"
        )
        details.setObjectName("summaryLabel")
        details.setWordWrap(True)

        close = QPushButton("Zamknij")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(details)
        layout.addWidget(close)


class LicenseDialog(QDialog):
    """Read-only view of the SIEKACZ 9000 EULA available from Settings."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        from app.license_text import LICENSE_NAME, LICENSE_URL, full_license_text

        self.setWindowTitle("Licencja")
        self.setModal(True)
        self.setMinimumSize(560, 520)

        title = QLabel(f"Licencja — {LICENSE_NAME}")
        title.setObjectName("settingsTitle")

        text = QTextEdit()
        text.setObjectName("premiumInput")
        text.setReadOnly(True)
        text.setPlainText(full_license_text())

        online = QPushButton("Otwórz pełny tekst online")
        online.setObjectName("smallButton")
        online.setCursor(Qt.CursorShape.PointingHandCursor)
        online.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LICENSE_URL)))
        close = QPushButton("Zamknij")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(online)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(text, 1)
        layout.addLayout(buttons)


class TutorialDialog(QDialog):
    """Guided tour drawn directly over the real application controls."""

    STEPS: tuple[tuple[str, str], ...] = (
        ("1. Nowy rozkrój", "<b>Nowy rozkrój</b> czyści bieżące dane i zaczyna nowe zlecenie. Zapisane projekty pozostają bezpieczne."),
        ("2. Materiał i grubość", "Kliknij znacznik materiału w pierwszej kolumnie. Zobaczysz tylko materiały i grubości mające cenę w aktualnym XLSX."),
        ("3. Wpisywanie formatek", "Wprowadź wysokość, szerokość i ilość. <b>Enter</b> lub <b>Tab</b> prowadzi przez pola i tworzy następny wiersz."),
        ("4. Podgląd CAD", "Przycisk <b>CAD</b> otwiera pliki DXF, STEP/STP i STL. Kliknij krawędź, aby zmierzyć bok; przeciągaj model, aby go obracać, albo zmierz odległość między dwoma punktami."),
        ("5. Dostępne płyty", "Materiał i grubość formatki automatycznie dodają zgodną płytę. Format 1000 × 2000 mm jest domyślny, jeśli ma cenę. Zielona kropka priorytetu każe zużyć wskazany format jako pierwszy. Opcja <b>Inteligentny dobór formatów</b> sama porównuje wszystkie wycenione rozmiary i ich mieszanki."),
        ("6. Kierunek długich cięć", "Kliknij szarą kropkę przy wybranym boku płyty. Niebieska kropka wymusza cięcie wzdłuż tego boku; drugie kliknięcie wraca do automatu."),
        ("7. Sztapel", "Tutaj wybierasz liczbę identycznych płyt ciętych jednocześnie. Sztapel nie może przekraczać dostępnej liczby arkuszy."),
        ("8. Obliczanie", "Uruchom rozkrój przyciskiem lub skrótem <b>Ctrl+Enter</b>. Program najpierw sprawdzi, czy każda formatka mieści się na zgodnej płycie."),
        ("9. Ustawienia algorytmu", "Ustaw rzaz, tolerancję, obrót, wielordzeniowość i tryb Comfort/Sport. Rzaz powinien odpowiadać rzeczywistej pile."),
        ("10. Projekty", "Tutaj otwierasz, kopiujesz, usuwasz i eksportujesz zapisane projekty. Zapis obejmuje również sztapel i kierunek cięcia."),
        ("11. Zapis i eksport", "Zapisz projekt albo wybierz <b>Wyślij</b>, aby przygotować PDF, DXF, wydruk lub e-mail. Ikona oka otwiera podgląd raportu z układem, metrykami i wyceną."),
        ("12. Cennik materiałów", "W <b>Ustawieniach</b> wczytasz cennik XLSX. Zmiany materiałów, kolorów, grubości, formatów i cen są rozpoznawane automatycznie."),
    )
    SPOTLIGHTS: tuple[str, ...] = (
        "new_cut",
        "part_material",
        "parts_table",
        "cad_inspection",
        "stock_table",
        "cut_axis",
        "stack",
        "calculate",
        "settings",
        "projects",
        "save_send",
        "settings",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Samouczek — SIEKACZ 9000")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self._step_index = 0
        self._completed = False
        self._pulse_value = 0.0
        self._target_rect = QRect()

        self._bubble = QFrame(self)
        self._bubble.setObjectName("tutorialBubble")
        self._bubble.setFixedWidth(430)
        self._bubble.setStyleSheet(
            """
            QFrame#tutorialBubble {
                background: #0d1a2c;
                border: 1px solid #4f8fe8;
                border-radius: 14px;
            }
            QLabel#tutorialStepTitle {
                color: #ffffff;
                font-size: 15px;
                font-weight: 800;
                background: transparent;
                border: 0;
            }
            QLabel#tutorialStepBody {
                color: #d7e6f8;
                font-size: 12px;
                background: transparent;
                border: 0;
            }
            QLabel#tutorialProgress {
                color: #8da7c7;
                font-size: 11px;
                background: transparent;
                border: 0;
            }
            """
        )
        self._title = QLabel()
        self._title.setObjectName("tutorialStepTitle")
        self._body = QLabel()
        self._body.setObjectName("tutorialStepBody")
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setWordWrap(True)
        self._progress = QLabel()
        self._progress.setObjectName("tutorialProgress")
        self._back = QPushButton("Wstecz")
        self._back.setObjectName("smallButton")
        self._next = QPushButton("Dalej")
        self._next.setObjectName("primaryButton")
        close = QPushButton("Zamknij")
        close.setObjectName("smallButton")
        self._back.clicked.connect(lambda: self._change_page(-1))
        self._next.clicked.connect(self._next_step)
        close.clicked.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(self._progress)
        footer.addStretch(1)
        footer.addWidget(self._back)
        footer.addWidget(self._next)
        footer.addWidget(close)

        bubble_layout = QVBoxLayout(self._bubble)
        bubble_layout.setContentsMargins(20, 18, 20, 16)
        bubble_layout.setSpacing(11)
        bubble_layout.addWidget(self._title)
        bubble_layout.addWidget(self._body)
        bubble_layout.addSpacing(4)
        bubble_layout.addLayout(footer)

        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(1050)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.valueChanged.connect(self._set_pulse_value)
        self._bubble_fade: QPropertyAnimation | None = None
        self._refresh_navigation()

    def showEvent(self, event) -> None:  # noqa: N802
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.frameGeometry())
        self._refresh_navigation()
        self._pulse.start()
        super().showEvent(event)

    def done(self, result: int) -> None:
        self._pulse.stop()
        super().done(result)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._next_step()
            return
        if event.key() == Qt.Key.Key_Left:
            self._change_page(-1)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(2, 7, 16, 220))
        target = self._target_rect
        if target.isValid():
            radius = max(6.0, min(11.0, target.height() / 3.0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.setBrush(Qt.BrushStyle.SolidPattern)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(target), radius, radius)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            glow = QColor(74, 149, 255, int(105 + 100 * self._pulse_value))
            painter.setPen(QPen(glow, 3.0 + 2.0 * self._pulse_value))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(target), radius, radius)

            # Animated click cue makes the selected control immediately obvious.
            cue_center = QPointF(target.left() + min(22.0, target.width() / 2), target.center().y())
            cue_radius = 5.0 + 9.0 * self._pulse_value
            cue = QColor(96, 165, 250, int(220 * (1.0 - self._pulse_value)))
            painter.setPen(QPen(cue, 2.0))
            painter.drawEllipse(cue_center, cue_radius, cue_radius)
            painter.setBrush(QColor("#3b82f6"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(cue_center, 4.0, 4.0)
        painter.end()

    def _set_pulse_value(self, value: object) -> None:
        self._pulse_value = float(value)
        self.update()

    @staticmethod
    def _global_widget_rect(widget: QWidget | None) -> QRect:
        if widget is None or not widget.isVisible():
            return QRect()
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, widget.size())

    @staticmethod
    def _global_cell_rect(table: QTableWidget, row: int, column: int) -> QRect:
        if row < 0 or row >= table.rowCount():
            return TutorialDialog._global_widget_rect(table)
        rect = table.visualRect(table.model().index(row, column))
        if not rect.isValid():
            return TutorialDialog._global_widget_rect(table)
        return QRect(table.viewport().mapToGlobal(rect.topLeft()), rect.size())

    def _spotlight_global_rect(self, key: str) -> QRect:
        window = self.parentWidget()
        if window is None:
            return QRect()
        if key == "new_cut":
            return self._global_widget_rect(getattr(window, "new_cut_button", None))
        if key == "part_material":
            table = getattr(window, "parts", None)
            if table is not None:
                badge = table.cellWidget(0, PART_MATERIAL_COLUMN)
                return self._global_widget_rect(badge) if badge is not None else self._global_cell_rect(table, 0, PART_MATERIAL_COLUMN)
        if key == "parts_table":
            return self._global_widget_rect(getattr(window, "parts", None))
        if key == "cad_inspection":
            return self._global_widget_rect(getattr(window, "cad_inspection_button", None))
        if key == "stock_table":
            return self._global_widget_rect(getattr(window, "stock_table", None))
        if key == "cut_axis":
            table = getattr(window, "stock_table", None)
            if table is not None:
                # Point at one complete dimension cell.  A union of both cells
                # produced a rectangular spotlight that did not match either
                # selectable field and could not follow their rounded corners.
                return self._global_cell_rect(table, 0, STOCK_HEIGHT_COLUMN).adjusted(2, 2, -2, -2)
        if key == "stack":
            table = getattr(window, "stock_table", None)
            if table is not None:
                control = table.cellWidget(0, STOCK_STACK_COLUMN)
                return self._global_widget_rect(control) if control is not None else self._global_cell_rect(table, 0, STOCK_STACK_COLUMN)
        if key == "calculate":
            return self._global_widget_rect(getattr(window, "calculate_button", None))
        if key == "settings":
            return self._global_widget_rect(getattr(window, "_algo_btn", None))
        if key == "projects":
            return self._global_widget_rect(getattr(window, "history_tab_button", None))
        if key == "save_send":
            save_rect = self._global_widget_rect(getattr(window, "save_project_button", None))
            send_rect = self._global_widget_rect(getattr(window, "send_button", None))
            return save_rect.united(send_rect) if save_rect.isValid() else send_rect
        if key == "send":
            return self._global_widget_rect(getattr(window, "send_button", None))
        return self._global_widget_rect(window.centralWidget())

    def _position_bubble(self) -> None:
        self._bubble.adjustSize()
        bubble_size = self._bubble.sizeHint().expandedTo(QSize(430, 190))
        self._bubble.resize(430, bubble_size.height())
        target = self._target_rect
        margin = 22
        gap = 22
        bounds = self.rect().adjusted(margin, margin, -margin, -margin)
        candidates = (
            QPoint(target.right() + gap, target.center().y() - self._bubble.height() // 2),
            QPoint(target.left() - gap - self._bubble.width(), target.center().y() - self._bubble.height() // 2),
            QPoint(target.center().x() - self._bubble.width() // 2, target.bottom() + gap),
            QPoint(target.center().x() - self._bubble.width() // 2, target.top() - gap - self._bubble.height()),
        )
        position = candidates[-1]
        padded_target = target.adjusted(-12, -12, 12, 12)
        for candidate in candidates:
            clamped = QPoint(
                max(bounds.left(), min(candidate.x(), bounds.right() - self._bubble.width())),
                max(bounds.top(), min(candidate.y(), bounds.bottom() - self._bubble.height())),
            )
            rect = QRect(clamped, self._bubble.size())
            if not rect.intersects(padded_target):
                position = clamped
                break
        x = max(bounds.left(), min(position.x(), bounds.right() - self._bubble.width()))
        y = max(bounds.top(), min(position.y(), bounds.bottom() - self._bubble.height()))
        self._bubble.move(x, y)
        self._bubble.raise_()

    def _fade_bubble_in(self) -> None:
        effect = QGraphicsOpacityEffect(self._bubble)
        self._bubble.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(220)
        animation.setStartValue(0.15)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self._bubble.setGraphicsEffect(None))
        self._bubble_fade = animation
        animation.start()

    def _next_step(self) -> None:
        if self._step_index >= len(self.STEPS) - 1:
            self._completed = True
            self.accept()
            return
        self._change_page(1)

    @property
    def completed(self) -> bool:
        return self._completed

    def _change_page(self, offset: int) -> None:
        self._step_index = max(0, min(len(self.STEPS) - 1, self._step_index + offset))
        self._refresh_navigation()

    def _refresh_navigation(self) -> None:
        title, body = self.STEPS[self._step_index]
        self._title.setText(title)
        self._body.setText(body)
        self._progress.setText(f"Krok {self._step_index + 1} z {len(self.STEPS)}")
        self._back.setEnabled(self._step_index > 0)
        self._next.setText("Dalej" if self._step_index < len(self.STEPS) - 1 else "Zakończ")
        global_rect = self._spotlight_global_rect(self.SPOTLIGHTS[self._step_index])
        if global_rect.isValid():
            local_top_left = self.mapFromGlobal(global_rect.topLeft())
            self._target_rect = QRect(local_top_left, global_rect.size())
        else:
            self._target_rect = self.rect().adjusted(80, 80, -80, -80)
        self._position_bubble()
        if self.isVisible():
            self._fade_bubble_in()
        self.update()


class SettingsDialog(QDialog):
    """General settings / about: license, support and update check."""

    check_updates_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        from app.license_text import BUY_ME_COFFEE_URL, LICENSE_NAME

        self.setWindowTitle("Pomoc")
        self.setModal(True)
        self.setMinimumWidth(460)

        title = QLabel("Pomoc i informacje")
        title.setObjectName("settingsTitle")
        sub = QLabel(f"SIEKACZ 9000 • wersja {APP_VERSION} • Made by Kewin")
        sub.setObjectName("summaryLabel")
        sub.setWordWrap(True)

        license_btn = QPushButton(f"Licencja oprogramowania  ({LICENSE_NAME})")
        license_btn.setObjectName("smallButton")
        license_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        license_btn.setToolTip("Pokaż warunki licencji programu")
        license_btn.clicked.connect(lambda: LicenseDialog(self).exec())

        coffee_btn = QPushButton("Buy Me a Coffee — wesprzyj autora")
        coffee_btn.setObjectName("smallButton")
        coffee_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        coffee_btn.setToolTip("Otwórz stronę wsparcia w przeglądarce")
        coffee_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUY_ME_COFFEE_URL)))

        update_btn = QPushButton("Sprawdź aktualizacje")
        update_btn.setObjectName("smallButton")
        update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        update_btn.clicked.connect(self.check_updates_requested.emit)

        close = QPushButton("Zamknij")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(6)
        layout.addWidget(license_btn)
        layout.addWidget(coffee_btn)
        layout.addWidget(update_btn)
        layout.addStretch(1)
        layout.addWidget(close)


class SendDialog(QDialog):
    """Choose how to send the cutting report: e-mail, PDF or printer."""

    def __init__(self, parent: QWidget, has_result: bool, last_action: str = "email", last_printer: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Wyślij rozkrój")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            "QRadioButton { spacing: 8px; }"
            "QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px;"
            "  border: 2px solid #475569; background: transparent; }"
            "QRadioButton::indicator:checked { border: 2px solid #3b82f6;"
            "  background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,"
            "  stop:0 #e2e8f0, stop:0.36 #e2e8f0, stop:0.37 #3b82f6, stop:1 #3b82f6); }"
        )

        title = QLabel("Wyślij rozkrój")
        title.setObjectName("settingsTitle")
        hint = QLabel("Wybierz sposób przekazania aktualnego wyniku rozkroju.")
        hint.setObjectName("summaryLabel")
        hint.setWordWrap(True)

        self.opt_email = QRadioButton("Wyślij e-mailem (wbudowany klient SMTP)")
        self.opt_pdf = QRadioButton("Zapisz do pliku PDF")
        self.opt_dxf = QRadioButton("Zapisz do pliku DXF")
        self.opt_print = QRadioButton("Wyślij do drukarki (wydruk)")
        action_buttons = {
            "email": self.opt_email,
            "pdf": self.opt_pdf,
            "dxf": self.opt_dxf,
            "print": self.opt_print,
        }
        action_buttons.get(last_action, self.opt_email).setChecked(True)

        self.email_config_btn = QPushButton("Konfiguracja poczty (SMTP)...")
        self.email_config_btn.setObjectName("smallButton")
        self.email_config_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.email_config_btn.clicked.connect(self._open_smtp_config)
        self.opt_email.toggled.connect(self.email_config_btn.setVisible)
        self.email_config_btn.setVisible(self.opt_email.isChecked())

        from PySide6.QtPrintSupport import QPrinterInfo
        from PySide6.QtWidgets import QComboBox
        self.printer_combo = QComboBox()
        self.printer_combo.setObjectName("premiumInput")
        for p in QPrinterInfo.availablePrinterNames():
            self.printer_combo.addItem(p)
        default_printer = QPrinterInfo.defaultPrinterName()
        if last_printer and self.printer_combo.findText(last_printer) >= 0:
            self.printer_combo.setCurrentText(last_printer)
        elif default_printer:
            self.printer_combo.setCurrentText(default_printer)
        self.printer_combo.setVisible(self.opt_print.isChecked())
        self.opt_print.toggled.connect(self.printer_combo.setVisible)
        group = QButtonGroup(self)
        for btn in (self.opt_email, self.opt_pdf, self.opt_dxf, self.opt_print):
            group.addButton(btn)

        self.save_project_check = QCheckBox("Zapisz też projekt w historii")
        self.skip_summary_check = QCheckBox("Pomiń stronę tytułową z podsumowaniem")
        self.skip_summary_check.setChecked(True)

        send_btn = QPushButton("Wyślij")
        send_btn.setObjectName("primaryButton")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Anuluj")
        cancel_btn.setObjectName("smallButton")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        if not has_result:
            hint.setText("Brak wyniku rozkroju — najpierw kliknij „Oblicz rozkrój”.")
            send_btn.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self.save_project_check)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(send_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(4)
        layout.addWidget(self.opt_email)
        layout.addWidget(self.email_config_btn)
        layout.addWidget(self.opt_pdf)
        layout.addWidget(self.opt_dxf)
        layout.addWidget(self.opt_print)
        layout.addWidget(self.printer_combo)
        layout.addWidget(self.skip_summary_check)
        layout.addSpacing(8)
        layout.addLayout(button_row)

    def selected_action(self) -> str:
        if self.opt_pdf.isChecked():
            return "pdf"
        if self.opt_dxf.isChecked():
            return "dxf"
        if self.opt_print.isChecked():
            return "print"
        return "email"

    def selected_printer(self) -> str:
        return self.printer_combo.currentText()

    def _open_smtp_config(self, *args) -> None:
        dialog = SmtpConfigDialog(self)
        dialog.exec()

    def should_save_project(self) -> bool:
        return self.save_project_check.isChecked()

    def should_skip_summary(self) -> bool:
        return self.skip_summary_check.isChecked()


class PannablePdfView(QPdfView):
    """A PDF view with cursor-anchored zoom and direct left-button panning."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pan_origin = QPoint()
        self._is_panning = False
        self.setPageMode(QPdfView.PageMode.MultiPage)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def zoom_at(self, position: QPoint, factor: float) -> None:
        """Change zoom while keeping the document point below *position* fixed."""
        current = max(0.2, float(self.zoomFactor() or 1.0))
        target = max(0.2, min(6.0, current * factor))
        if abs(target - current) < 1e-9:
            return
        horizontal = self.horizontalScrollBar()
        vertical = self.verticalScrollBar()
        old_horizontal = horizontal.value()
        old_vertical = vertical.value()
        scale = target / current
        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(target)

        def restore_anchor() -> None:
            horizontal.setValue(round((old_horizontal + position.x()) * scale - position.x()))
            vertical.setValue(round((old_vertical + position.y()) * scale - position.y()))

        # QPdfView updates its scroll ranges after the zoom event returns.
        QTimer.singleShot(0, restore_anchor)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.zoom_at(event.position().toPoint(), 1.18 if delta > 0 else 1 / 1.18)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_panning = True
            self._pan_origin = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._is_panning:
            position = event.position().toPoint()
            delta = position - self._pan_origin
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_origin = position
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._is_panning and event.button() == Qt.MouseButton.LeftButton:
            self._is_panning = False
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PdfPreviewDialog(QDialog):
    """A lightweight in-app preview for the transient cutting report."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Podgląd raportu PDF")
        self.setModal(True)
        self.resize(1080, 780)
        self._document = QPdfDocument(self)
        self._document.load(str(path))
        self._view = PannablePdfView(self)
        self._view.setDocument(self._document)
        self._view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self._view.setToolTip("Ctrl + rolka: przybliżenie pod kursorem. Przeciągnij lewym przyciskiem, aby przesunąć dokument.")
        self._view.pageNavigator().currentPageChanged.connect(self._refresh_page_label)

        previous = QToolButton()
        previous.setObjectName("headerIconButton")
        previous.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        previous.setToolTip("Poprzednia strona")
        previous.clicked.connect(lambda: self._change_page(-1))
        next_page = QToolButton()
        next_page.setObjectName("headerIconButton")
        next_page.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        next_page.setToolTip("Następna strona")
        next_page.clicked.connect(lambda: self._change_page(1))
        zoom_out = QToolButton()
        zoom_out.setObjectName("headerIconButton")
        zoom_out.setText("−")
        zoom_out.setToolTip("Oddal")
        zoom_out.clicked.connect(lambda: self._change_zoom(1 / 1.2))
        zoom_in = QToolButton()
        zoom_in.setObjectName("headerIconButton")
        zoom_in.setText("+")
        zoom_in.setToolTip("Przybliż")
        zoom_in.clicked.connect(lambda: self._change_zoom(1.2))
        fit = QToolButton()
        fit.setObjectName("headerIconButton")
        fit.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        fit.setToolTip("Dopasuj strony do widoku")
        fit.clicked.connect(lambda: self._view.setZoomMode(QPdfView.ZoomMode.FitInView))
        self._page_label = QLabel()
        self._page_label.setObjectName("summaryLabel")
        self._refresh_page_label()
        interaction_hint = QLabel("Ctrl + rolka — zoom pod kursorem · przeciągnij — przesuń")
        interaction_hint.setObjectName("summaryLabel")

        close = QPushButton("Zamknij")
        close.setObjectName("smallButton")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        toolbar = QHBoxLayout()
        toolbar.addWidget(previous)
        toolbar.addWidget(next_page)
        toolbar.addWidget(self._page_label)
        toolbar.addWidget(interaction_hint)
        toolbar.addStretch(1)
        toolbar.addWidget(zoom_out)
        toolbar.addWidget(fit)
        toolbar.addWidget(zoom_in)
        layout.addLayout(toolbar)
        layout.addWidget(self._view, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _refresh_page_label(self, page: int | None = None) -> None:
        current = self._view.pageNavigator().currentPage() if page is None else page
        count = self._document.pageCount()
        self._page_label.setText(f"Strona {max(0, current) + 1} / {max(1, count)}")

    def _change_page(self, offset: int) -> None:
        count = self._document.pageCount()
        if count <= 0:
            return
        current = self._view.pageNavigator().currentPage()
        target = max(0, min(count - 1, current + offset))
        self._view.pageNavigator().jump(target, QPointF(), self._view.zoomFactor())

    def _change_zoom(self, factor: float) -> None:
        self._view.zoom_at(self._view.viewport().rect().center(), factor)

class SmtpConfigDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Konfiguracja SMTP")
        self.setModal(True)
        self.setMinimumWidth(380)

        from database.repositories import get_setting
        from PySide6.QtWidgets import QFormLayout
        self.config = get_setting("smtp_config", {})
        if not isinstance(self.config, dict):
            self.config = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.host_in = QLineEdit(self.config.get("host", "smtp.gmail.com"))
        self.host_in.setObjectName("premiumInput")
        form.addRow("Serwer SMTP:", self.host_in)

        self.port_in = QLineEdit(str(self.config.get("port", 465)))
        self.port_in.setObjectName("premiumInput")
        form.addRow("Port:", self.port_in)

        self.user_in = QLineEdit(self.config.get("user", ""))
        self.user_in.setObjectName("premiumInput")
        form.addRow("E-mail (użytkownik):", self.user_in)

        self.pass_in = QLineEdit(self.config.get("password", ""))
        self.pass_in.setObjectName("premiumInput")
        self.pass_in.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Hasło (app password):", self.pass_in)

        self.ssl_check = QCheckBox("Wymagaj SSL/TLS")
        self.ssl_check.setChecked(self.config.get("ssl", True))
        form.addRow("Zabezpieczenia:", self.ssl_check)

        self.to_in = QLineEdit(self.config.get("to", ""))
        self.to_in.setObjectName("premiumInput")
        form.addRow("Domyślny odbiorca:", self.to_in)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Zapisz")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Anuluj")
        cancel_btn.setObjectName("smallButton")
        cancel_btn.clicked.connect(self.reject)
        
        btn_box.addStretch()
        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _save(self) -> None:
        from database.repositories import set_setting
        try:
            port = int(self.port_in.text() or 465)
        except ValueError:
            QMessageBox.warning(self, "Błąd", "Port musi być liczbą.")
            return
            
        config = {
            "host": self.host_in.text(),
            "port": port,
            "user": self.user_in.text(),
            "password": self.pass_in.text(),
            "ssl": self.ssl_check.isChecked(),
            "to": self.to_in.text()
        }
        set_setting("smtp_config", config)
        self.accept()
class UpdateDialog(QDialog):
    """Shows release notes for an available update and offers to install it."""

    def __init__(self, parent: QWidget, info) -> None:
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("Dostępna aktualizacja")
        self.setModal(True)
        self.setMinimumSize(520, 460)

        title = QLabel(f"Nowa wersja: {info.tag}")
        title.setObjectName("settingsTitle")
        sub = QLabel(f"Masz wersję {APP_VERSION}. Dostępna jest {info.version}.")
        sub.setObjectName("summaryLabel")
        sub.setWordWrap(True)

        notes_label = QLabel("Opis zmian:")
        notes_label.setObjectName("summaryLabel")
        notes = QTextEdit()
        notes.setObjectName("premiumInput")
        notes.setReadOnly(True)
        notes.setPlainText(info.notes or "Brak opisu zmian.")

        self.install_btn = QPushButton("Zainstaluj teraz")
        self.install_btn.setObjectName("primaryButton")
        self.install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_btn.clicked.connect(self.accept)
        later_btn = QPushButton("Później")
        later_btn.setObjectName("smallButton")
        later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        later_btn.clicked.connect(self.reject)
        page_btn = QPushButton("Otwórz stronę release")
        page_btn.setObjectName("smallButton")
        page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info.html_url)))
        if not getattr(info, "asset_url", ""):
            self.install_btn.setText("Pobierz ze strony")

        buttons = QHBoxLayout()
        buttons.addWidget(page_btn)
        buttons.addStretch(1)
        buttons.addWidget(later_btn)
        buttons.addWidget(self.install_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addWidget(notes_label)
        layout.addWidget(notes, 1)
        layout.addLayout(buttons)


class AlgorithmSettingsDialog(QDialog):
    """Popup dialog for algorithm variants — mode, scoring priority, rotation, engine."""

    # Default state that matches the hardcoded values used before this dialog existed.
    DEFAULTS: dict = {
        "optimization_mode": "comfort",
        "mode": "minimize_waste",
        "cutting_mode": "hybrid",
        "allow_rotation_parts": True,
        "allow_rotation_stock": True,
        "multi_core": True,
        "kerf": 5.0,
        "kerf_tolerance": 0.5,
        "saw_feed_m_per_min": 12.0,
        "animation_mode": "economy",
    }

    # (label, tolerance_mm) — None means "manual entry"
    _TOL_PRESETS: list = [
        ("Dokładna (+0,5mm)", 0.5),
        ("Średniodokładna (+1,2mm)", 1.2),
        ("Zgrubna (+3mm)", 3.0),
        ("Bardzo zgrubna (+6mm)", 6.0),
        ("Ręczna", None),
    ]

    def __init__(self, parent: QWidget, settings: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ustawienia algorytmu")
        self.setModal(True)
        self.setMinimumSize(780, 540)
        self.tutorial_requested = False

        self._out: dict = dict(settings)   # will be overwritten on accept

        # ── Explicit radio-button indicator styles ───────────────────────────
        # The dark app theme removes the default radio indicator dot; restore it.
        self.setStyleSheet(
            "QRadioButton { spacing: 8px; }"
            "QRadioButton::indicator {"
            "  width: 16px; height: 16px;"
            "  border-radius: 8px;"
            "  border: 2px solid #475569;"
            "  background: transparent;"
            "}"
            "QRadioButton::indicator:hover {"
            "  border-color: #3b82f6;"
            "}"
            "QRadioButton::indicator:checked {"
            "  border: 2px solid #3b82f6;"
            "  background: qradialgradient("
            "    cx:0.5, cy:0.5, radius:0.5,"
            "    fx:0.5, fy:0.5,"
            "    stop:0    #e2e8f0,"
            "    stop:0.36 #e2e8f0,"
            "    stop:0.37 #3b82f6,"
            "    stop:1    #3b82f6"
            "  );"
            "}"
            "QPushButton#settingsNavTab {"
            "  color: #cbd5e1;"
            "  background: transparent;"
            "  border: 1px solid transparent;"
            "  border-radius: 6px;"
            "  padding: 9px 10px;"
            "  text-align: left;"
            "  font-weight: 600;"
            "}"
            "QPushButton#settingsNavTab:hover { background: rgba(59,130,246,0.10); }"
            "QPushButton#settingsNavTab:checked {"
            "  color: #eff6ff;"
            "  background: rgba(37,99,235,0.24);"
            "  border-color: rgba(96,165,250,0.45);"
            "}"
        )

        # ── Header ──────────────────────────────────────────────────────────
        title = QLabel("Algorytm rozkroju")
        title.setObjectName("settingsTitle")
        hint = QLabel("Ustawienia aktywne przy kolejnym obliczeniu rozkroju.")
        hint.setObjectName("summaryLabel")
        hint.setWordWrap(True)

        # ── Section 1: Charakter pracy ───────────────────────────────────────
        self._comfort_radio = QRadioButton("Komfort - stabilne pasy, czytelna kolejność cięcia")
        self._sport_radio   = QRadioButton("Sport - agresywna minimalizacja zużytego materiału")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._comfort_radio)
        mode_group.addButton(self._sport_radio)
        if settings.get("optimization_mode", "comfort") == "sport":
            self._sport_radio.setChecked(True)
        else:
            self._comfort_radio.setChecked(True)

        # ── Section 2: Priorytet scoringu ────────────────────────────────────
        self._mode_waste   = QRadioButton("Minimalizuj odpad (zalecany)")
        self._mode_sheets  = QRadioButton("Jak najmniej płyt")
        self._mode_cutlen  = QRadioButton("Najkrótsza trasa cięcia")
        prio_group = QButtonGroup(self)
        for btn in (self._mode_waste, self._mode_sheets, self._mode_cutlen):
            prio_group.addButton(btn)
        _prio_map = {
            "minimize_waste": self._mode_waste,
            "minimize_number_of_sheets": self._mode_sheets,
            "minimize_cut_length": self._mode_cutlen,
        }
        _prio_map.get(settings.get("mode", "minimize_waste"), self._mode_waste).setChecked(True)

        # ── Section 3: Rotacja ───────────────────────────────────────────────
        self._rotate_parts = QCheckBox("Rotacja formatek — elementy mogą być obracane o 90°")
        self._rotate_stock = QCheckBox("Rotacja arkuszy — próbuj oba kierunki stosu płyt")
        self._rotate_parts.setChecked(bool(settings.get("allow_rotation_parts", True)))
        self._rotate_stock.setChecked(bool(settings.get("allow_rotation_stock", True)))

        # ── Section: Kerf i tolerancja ──────────────────────────────────────
        self._kerf_spin = QDoubleSpinBox()
        self._kerf_spin.setRange(0.0, 50.0)
        self._kerf_spin.setDecimals(1)
        self._kerf_spin.setSuffix(" mm")
        self._kerf_spin.setValue(float(settings.get("kerf", 5.0)))
        self._kerf_spin.setToolTip(
            "Szerokość rzazu — materiał usuwany przez piłę podczas każdego cięcia.\n"
            "Typowe wartości: piła formatowa 3–5 mm, laser 0,1–0,5 mm."
        )

        self._tol_combo = QComboBox()
        for _lbl, _ in self._TOL_PRESETS:
            self._tol_combo.addItem(_lbl)

        self._tol_manual = QDoubleSpinBox()
        self._tol_manual.setRange(0.0, 10.0)
        self._tol_manual.setDecimals(2)
        self._tol_manual.setSuffix(" mm")
        self._tol_manual.setEnabled(False)
        self._tol_manual.setToolTip(
            "Ręczna korekta tolerancji kerfu.\n"
            "Zostanie dodana do podanego kerfu jako margines bezpieczeństwa."
        )

        # restore saved tolerance preset / manual value
        _saved_tol = float(settings.get("kerf_tolerance", 0.2))
        _preset_found = False
        for _pi, (_, _pv) in enumerate(self._TOL_PRESETS[:-1]):
            if _pv is not None and abs(_pv - _saved_tol) < 0.001:
                self._tol_combo.setCurrentIndex(_pi)
                _preset_found = True
                break
        if not _preset_found:
            self._tol_combo.setCurrentIndex(len(self._TOL_PRESETS) - 1)
            self._tol_manual.setValue(_saved_tol)
            self._tol_manual.setEnabled(True)

        self._eff_kerf_lbl = QLabel()
        self._eff_kerf_lbl.setObjectName("summaryLabel")
        self._eff_kerf_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._refresh_eff_kerf()

        self._tol_combo.currentIndexChanged.connect(self._on_tol_preset_changed)
        self._kerf_spin.valueChanged.connect(self._refresh_eff_kerf)
        self._tol_manual.valueChanged.connect(self._refresh_eff_kerf)

        kerf_row = QHBoxLayout()
        kerf_row.setSpacing(8)
        kerf_row.addWidget(QLabel("Kerf (rzaz)"))
        kerf_row.addStretch(1)
        kerf_row.addWidget(self._kerf_spin)

        tol_row = QHBoxLayout()
        tol_row.setSpacing(8)
        tol_row.addWidget(QLabel("Tolerancja"))
        tol_row.addStretch(1)
        tol_row.addWidget(self._tol_combo)

        man_row = QHBoxLayout()
        man_row.setSpacing(8)
        man_row.addWidget(QLabel("Wartość ręczna"))
        man_row.addStretch(1)
        man_row.addWidget(self._tol_manual)

        # ── Section: Wydajność (multi-core) ──────────────────────────────────
        import os as _os
        _cores = _os.cpu_count() or 2
        self._multi_core = QCheckBox(
            f"Tryb best-of na wielu rdzeniach — porównaj algorytmy równolegle ({_cores} rdzeni CPU)"
        )
        self._multi_core.setToolTip(
            "Uruchamia kilka algorytmów rozkroju jednocześnie na osobnych rdzeniach\n"
            "procesora i wybiera najlepszy wynik. Może dać lepszy rozkrój kosztem\n"
            "nieco dłuższego startu obliczeń."
        )
        self._multi_core.setChecked(bool(settings.get("multi_core", True)))

        self._feed_spin = QDoubleSpinBox()
        self._feed_spin.setRange(1.0, 80.0)
        self._feed_spin.setDecimals(1)
        self._feed_spin.setSingleStep(0.5)
        self._feed_spin.setSuffix(" m/min")
        self._feed_spin.setValue(float(settings.get("saw_feed_m_per_min", 12.0)))
        self._feed_spin.setToolTip(
            "Rzeczywista prędkość posuwu podczas cięcia. Wpływa na czas i koszt robocizny w wycenie."
        )

        feed_row = QHBoxLayout()
        feed_row.setSpacing(8)
        feed_row.addWidget(QLabel("Posuw piły"))
        feed_row.addStretch(1)
        feed_row.addWidget(self._feed_spin)

        self._animation_quality = QRadioButton("Jakość — animacja samuraja")
        self._animation_economy = QRadioButton("Oszczędny — kosmiczna animacja")
        animation_group = QButtonGroup(self)
        for animation_button in (
            self._animation_quality,
            self._animation_economy,
        ):
            animation_group.addButton(animation_button)
        animation_mode = str(settings.get("animation_mode", "economy"))
        {
            "economy": self._animation_economy,
            "quality": self._animation_quality,
        }.get(animation_mode, self._animation_economy).setChecked(True)

        # ── Section 4: Silnik cięcia ─────────────────────────────────────────
        self._engine_hybrid     = QRadioButton("Hybrydowy produkcyjny (zalecany)")
        self._engine_strip      = QRadioButton("Pasy")
        self._engine_guillotine = QRadioButton("Gilotyna (uproszczony)")
        self._engine_free       = QRadioButton("Free nesting — podgląd, bez gwarancji gilotynowości")
        engine_group = QButtonGroup(self)
        for btn in (self._engine_hybrid, self._engine_strip,
                    self._engine_guillotine, self._engine_free):
            engine_group.addButton(btn)
        _engine_map = {
            "hybrid":     self._engine_hybrid,
            "strip":      self._engine_strip,
            "guillotine": self._engine_guillotine,
            "free":       self._engine_free,
        }
        _engine_map.get(settings.get("cutting_mode", "hybrid"),
                        self._engine_hybrid).setChecked(True)

        # ── Buttons ──────────────────────────────────────────────────────────
        apply_btn = QPushButton("Zastosuj i zamknij")
        apply_btn.setObjectName("primaryButton")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(self._apply_and_close)
        cancel_btn = QPushButton("Anuluj")
        cancel_btn.setObjectName("smallButton")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)

        # ── Compact left navigation with focused setting pages ───────────────
        self._settings_pages = QStackedWidget()
        nav = QWidget()
        nav.setObjectName("settingsNavigation")
        nav.setFixedWidth(164)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 12, 0)
        nav_layout.setSpacing(6)
        self._settings_nav_buttons: list[QPushButton] = []

        def _page() -> tuple[QWidget, QVBoxLayout]:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 4, 4, 4)
            page_layout.setSpacing(8)
            return page, page_layout

        def _add_page(label: str, page: QWidget) -> None:
            index = self._settings_pages.addWidget(page)
            button = QPushButton(label)
            button.setObjectName("settingsNavTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, target=index: self._set_settings_page(target))
            nav_layout.addWidget(button)
            self._settings_nav_buttons.append(button)

        cut_page, cut_layout = _page()
        cut_layout.addWidget(self._section_header("Charakter pracy"))
        cut_layout.addWidget(self._comfort_radio)
        cut_layout.addWidget(self._sport_radio)
        cut_layout.addSpacing(8)
        cut_layout.addWidget(self._section_header("Priorytet"))
        cut_layout.addWidget(self._mode_waste)
        cut_layout.addWidget(self._mode_sheets)
        cut_layout.addWidget(self._mode_cutlen)
        cut_layout.addStretch(1)
        _add_page("Rozkrój", cut_page)

        tech_page, tech_layout = _page()
        tech_layout.addWidget(self._section_header("Rotacja"))
        tech_layout.addWidget(self._rotate_parts)
        tech_layout.addWidget(self._rotate_stock)
        tech_layout.addSpacing(8)
        tech_layout.addWidget(self._section_header("Kerf i tolerancja"))
        tech_layout.addLayout(kerf_row)
        tech_layout.addLayout(tol_row)
        tech_layout.addLayout(man_row)
        tech_layout.addWidget(self._eff_kerf_lbl)
        tech_layout.addSpacing(10)
        tech_layout.addWidget(self._section_header("Parametry piły"))
        tech_layout.addLayout(feed_row)
        feed_hint = QLabel("Posuw jest używany w metrykach cięcia i w koszcie robocizny.")
        feed_hint.setObjectName("summaryLabel")
        feed_hint.setWordWrap(True)
        tech_layout.addWidget(feed_hint)
        tech_layout.addStretch(1)
        _add_page("Technologia", tech_page)

        performance_page, performance_layout = _page()
        performance_layout.addWidget(self._section_header("Moc obliczeniowa"))
        performance_layout.addWidget(self._multi_core)
        performance_layout.addStretch(1)
        _add_page("Wydajność", performance_page)

        display_page, display_layout = _page()
        display_layout.addWidget(self._section_header("Animacja obliczania"))
        display_layout.addWidget(self._animation_quality)
        display_layout.addWidget(self._animation_economy)
        animation_hint = QLabel(
            "Tryb jakości pokazuje animację samuraja. Tryb oszczędny używa lekkiej "
            "animacji generowanej przez program i nie odtwarza filmu."
        )
        animation_hint.setObjectName("summaryLabel")
        animation_hint.setWordWrap(True)
        display_layout.addWidget(animation_hint)
        display_layout.addStretch(1)
        _add_page("Wygląd", display_page)

        catalog_page, catalog_layout = _page()
        catalog_layout.addWidget(self._section_header("Cennik materiałów"))
        catalog_hint = QLabel("Wczytaj aktualny cennik XLSX. Lista materiałów i dostępne grubości odświeżą się od razu.")
        catalog_hint.setObjectName("summaryLabel")
        catalog_hint.setText(
            "Wczytaj arkusz XLSX dostawcy. Nowy arkusz oznacza nową grupę materiałową; "
            "program pokaże tylko przecięcia grubości i formatu z ceną za m²."
        )
        catalog_hint.setWordWrap(True)
        catalog_layout.addWidget(catalog_hint)
        self._catalog_import_button = QPushButton("Wczytaj cennik XLSX")
        self._catalog_import_button.setObjectName("primaryButton")
        self._catalog_import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_catalog = getattr(parent, "_import_material_catalog", None)
        if callable(import_catalog):
            self._catalog_import_button.clicked.connect(import_catalog)
        else:
            self._catalog_import_button.setEnabled(False)
        catalog_layout.addWidget(self._catalog_import_button, 0, Qt.AlignmentFlag.AlignLeft)
        catalog_layout.addStretch(1)
        _add_page("Cennik", catalog_page)

        engine_page, engine_layout = _page()
        engine_layout.addWidget(self._section_header("Silnik cięcia"))
        engine_layout.addWidget(self._engine_hybrid)
        engine_layout.addWidget(self._engine_strip)
        engine_layout.addWidget(self._engine_guillotine)
        engine_layout.addWidget(self._engine_free)
        engine_layout.addStretch(1)
        _add_page("Silnik", engine_page)
        nav_layout.addStretch(1)
        self._tutorial_button = QPushButton("Samouczek")
        self._tutorial_button.setObjectName("settingsNavTab")
        self._tutorial_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tutorial_button.setToolTip("Przejdź samouczek obsługi programu jeszcze raz")
        self._tutorial_button.clicked.connect(self._request_tutorial)
        nav_layout.addWidget(self._tutorial_button)
        license_button = QPushButton("Licencja")
        license_button.setObjectName("settingsNavTab")
        license_button.setCursor(Qt.CursorShape.PointingHandCursor)
        license_button.setToolTip("Przeczytaj warunki licencji oprogramowania")
        license_button.clicked.connect(lambda: LicenseDialog(self).exec())
        nav_layout.addWidget(license_button)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(nav)
        body.addWidget(self._settings_pages, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(body, 1)
        layout.addLayout(btn_row)
        self._set_settings_page(0)

    def _request_tutorial(self) -> None:
        self.tutorial_requested = True
        self.reject()

    @staticmethod
    def _section_header(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: #64748b;"
            "font-size: 10px;"
            "font-weight: 700;"
            "letter-spacing: 1px;"
            "padding-top: 8px;"
            "padding-bottom: 2px;"
            "border-top: 1px solid rgba(255,255,255,0.07);"
        )
        return lbl

    def _apply_and_close(self) -> None:
        self._out["optimization_mode"] = "sport" if self._sport_radio.isChecked() else "comfort"

        if self._mode_sheets.isChecked():
            self._out["mode"] = "minimize_number_of_sheets"
        elif self._mode_cutlen.isChecked():
            self._out["mode"] = "minimize_cut_length"
        else:
            self._out["mode"] = "minimize_waste"

        self._out["allow_rotation_parts"] = self._rotate_parts.isChecked()
        self._out["allow_rotation_stock"] = self._rotate_stock.isChecked()
        self._out["multi_core"] = self._multi_core.isChecked()
        self._out["kerf"] = self._kerf_spin.value()
        self._out["kerf_tolerance"] = self._current_tolerance()
        self._out["saw_feed_m_per_min"] = self._feed_spin.value()
        if self._animation_quality.isChecked():
            self._out["animation_mode"] = "quality"
        else:
            self._out["animation_mode"] = "economy"

        if self._engine_strip.isChecked():
            self._out["cutting_mode"] = "strip"
        elif self._engine_guillotine.isChecked():
            self._out["cutting_mode"] = "guillotine"
        elif self._engine_free.isChecked():
            self._out["cutting_mode"] = "free"
        else:
            self._out["cutting_mode"] = "hybrid"

        self.accept()

    def _set_settings_page(self, index: int) -> None:
        self._settings_pages.setCurrentIndex(index)
        for current, button in enumerate(self._settings_nav_buttons):
            button.setChecked(current == index)

    def _current_tolerance(self) -> float:
        idx = self._tol_combo.currentIndex()
        preset_val = self._TOL_PRESETS[idx][1]
        return self._tol_manual.value() if preset_val is None else float(preset_val)

    def _refresh_eff_kerf(self) -> None:
        eff = self._kerf_spin.value() + self._current_tolerance()
        tol = self._current_tolerance()
        sign = f"+{tol:.2f}" if tol >= 0 else f"{tol:.2f}"
        self._eff_kerf_lbl.setText(
            f"Efektywny rzaz: <b>{eff:.2f} mm</b>"
            f"  <span style='color:#64748b;font-size:11px;'>(kerf {self._kerf_spin.value():.1f} {sign} tol.)</span>"
        )

    def _on_tol_preset_changed(self, idx: int) -> None:
        is_manual = self._TOL_PRESETS[idx][1] is None
        self._tol_manual.setEnabled(is_manual)
        self._refresh_eff_kerf()

    def result_settings(self) -> dict:
        return dict(self._out)


class CutInfoDialog(QDialog):
    """Per-format cutting metrics (area, waste, cuts, time) plus an optional
    live cost estimate driven by three rates (zł/m², zł/mb, zł/h)."""

    _COLS = [
        "Formatka / materiał",
        "Szt.",
        "Netto [m²]",
        "Odpad prod. [m²]",
        "Resztki [m²]",
        "Rozlicz. [m²]",
        "Cięcia",
        "Dł. cięcia [mb]",
        "Czas",
        "Koszt / szt.",
        "Koszt",
    ]

    def __init__(self, parent: QWidget, result, feed_m_per_min: float | None = None) -> None:
        super().__init__(parent)
        from algorithms.cut_metrics import compute_cut_summary

        self.setWindowTitle("Metryki cięcia i wycena")
        self.setModal(True)
        screen = parent.screen() if parent is not None and parent.screen() is not None else QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRectF(0, 0, 1440, 820)
        initial_width = min(1600, max(1280, int(available.width()) - 64))
        initial_height = min(820, max(620, int(available.height()) - 80))
        self.setMinimumSize(1200, 620)
        self.resize(initial_width, initial_height)

        self._summary = compute_cut_summary(result, feed_m_per_min)

        # ── Header: whole-job numbers ────────────────────────────────────────
        s = self._summary
        util = (s.total_area_m2 / s.total_board_area_m2 * 100.0) if s.total_board_area_m2 else 0.0
        title = QLabel("Metryki cięcia i wycena zlecenia")
        title.setObjectName("settingsTitle")

        from algorithms.layout_scoring import format_cut_time
        self._format_cut_time = format_cut_time

        head = QLabel(
            f"<b>{s.total_pieces}</b> formatek &nbsp;·&nbsp; "
            f"<b>{s.total_cuts}</b> cięć &nbsp;·&nbsp; "
            f"piła <b>{s.total_saw_m:.1f} mb</b> &nbsp;·&nbsp; "
            f"czas <b>{format_cut_time(s.total_time_s)}</b> przy posuwie <b>{float(feed_m_per_min or getattr(result, 'saw_feed_m_per_min', 12.0)):g} m/min</b><br>"
            f"materiał rozliczany <b>{s.total_board_area_m2:.3f} m²</b> "
            f"(netto {s.total_area_m2:.3f} m², odpad produkcyjny {s.total_waste_m2:.3f} m², "
            f"resztki magazynowe {s.total_reusable_m2:.3f} m²) &nbsp;·&nbsp; "
            f"wykorzystanie <b>{util:.1f}%</b>"
        )
        head.setObjectName("summaryLabel")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)

        # ── Rates ────────────────────────────────────────────────────────────
        rates_header = AlgorithmSettingsDialog._section_header("Stawki do wyceny")

        def _rate_spin(value: float) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 100000.0)
            sp.setDecimals(2)
            sp.setMaximumWidth(200)
            sp.setValue(value)
            sp.valueChanged.connect(self._recompute)
            return sp

        materials = sorted(
            {
                (fmt.material, float(getattr(fmt, "thickness", 0.0) or 0.0))
                for fmt in s.formats
                if fmt.material and fmt.material != "standard"
            },
            key=lambda item: (item[0].casefold(), item[1]),
        )
        if not materials:
            materials = [("Płyta", 0.0)]

        self._rate_m2_dict = {}
        self._rate_m2_labels: dict[str, str] = {}
        catalog_rates: dict[str, tuple[float, float]] = {}
        for fmt in s.formats:
            rate = float(getattr(fmt, "catalog_price_m2", 0.0) or 0.0)
            if rate <= 0:
                continue
            key = f"{fmt.material}|{float(getattr(fmt, 'thickness', 0.0) or 0.0):g}"
            total, area = catalog_rates.get(key, (0.0, 0.0))
            catalog_rates[key] = (total + rate * fmt.gross_m2, area + fmt.gross_m2)
        for mat, thickness in materials:
            price_key = f"{mat}|{thickness:g}"
            setting_key = f"price_per_m2_{mat}_{thickness:g}"
            catalog_total, catalog_area = catalog_rates.get(price_key, (0.0, 0.0))
            catalog_rate = catalog_total / catalog_area if catalog_area > 0 else 0.0
            val = catalog_rate if catalog_rate > 0 else float(repositories.get_setting(
                setting_key,
                repositories.get_setting(f"price_per_m2_{mat}", repositories.get_setting("price_per_m2", 0.0)),
            ))
            sp = _rate_spin(val)
            sp.setSuffix(" zł/m²")
            if catalog_rate > 0:
                sp.setReadOnly(True)
                sp.setToolTip("Cena pochodzi z dokładnie użytej płyty w katalogu XLSX.")
            self._rate_m2_dict[price_key] = sp
            self._rate_m2_labels[price_key] = f"{mat} · {thickness:g} mm" if thickness > 0 else mat

        self._rate_saw = _rate_spin(float(repositories.get_setting("price_per_piece", 0.0)))
        self._rate_saw.setSuffix(" zł/szt.")
        self._rate_hour = _rate_spin(float(repositories.get_setting("price_per_hour", 0.0)))
        self._rate_hour.setSuffix(" zł/h")

        rates_panel = QWidget()
        rates_panel.setObjectName("sectionCard")
        rates_list = QVBoxLayout(rates_panel)
        rates_list.setContentsMargins(10, 8, 10, 8)
        rates_list.setSpacing(6)
        for price_key, sp in self._rate_m2_dict.items():
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            label_text = self._rate_m2_labels[price_key]
            label = QLabel(label_text)
            label.setToolTip(label_text)
            label.setWordWrap(False)
            row.addWidget(label, 1)
            row.addWidget(sp)
            rates_list.addWidget(row_widget)

        service_row_widget = QWidget()
        service_row = QHBoxLayout(service_row_widget)
        service_row.setContentsMargins(0, 0, 0, 0)
        service_row.addWidget(QLabel("Cięcie / szt."))
        service_row.addWidget(self._rate_saw)
        service_row.addSpacing(16)
        service_row.addWidget(QLabel("Robocizna"))
        service_row.addWidget(self._rate_hour)
        service_row.addStretch(1)

        rates_scroll = QScrollArea()
        rates_scroll.setObjectName("controlScroll")
        rates_scroll.setWidgetResizable(True)
        rates_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rates_scroll.setFixedHeight(min(148, max(56, 24 + len(materials) * 36)))
        rates_scroll.setWidget(rates_panel)

        # ── Table ────────────────────────────────────────────────────────────
        self._table = QTableWidget(len(s.formats) + 1, len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hdr = self._table.horizontalHeader()
        # The former fixed widths squeezed the middle measurements while the
        # final cost column absorbed all spare space.  Keep the description
        # readable, then distribute the remaining columns evenly.
        self._table.setColumnWidth(0, 250)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        # The last (cost) column has a value and currency.  A 100 px minimum
        # avoids clipping it in the default-size metrics window while the
        # remaining space is still divided evenly between numeric columns.
        hdr.setMinimumSectionSize(100)
        for c in range(1, len(self._COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
            self._table.horizontalHeaderItem(c).setToolTip(self._COLS[c])
        self._table.horizontalHeaderItem(0).setToolTip(self._COLS[0])

        # ── Time-model explanation ───────────────────────────────────────────
        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("primaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(head)
        layout.addWidget(rates_header)
        layout.addWidget(rates_scroll)
        layout.addWidget(service_row_widget)
        layout.addWidget(self._table, 1)
        layout.addLayout(btn_row)

        self._recompute()

    @staticmethod
    def _cell(text: str, align_right: bool = True) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _recompute(self) -> None:
        from algorithms.pricing import PricingRates, compute_pricing

        per_m2_dict = {price_key: sp.value() for price_key, sp in self._rate_m2_dict.items()}
        rates = PricingRates(
            per_m2=per_m2_dict,
            per_piece=self._rate_saw.value(),
            per_hour=self._rate_hour.value(),
        )
        for price_key, val in per_m2_dict.items():
            material, thickness = price_key.rsplit("|", 1)
            repositories.set_setting(f"price_per_m2_{material}_{thickness}", val)
        repositories.set_setting("price_per_piece", rates.per_piece)
        repositories.set_setting("price_per_hour", rates.per_hour)

        pricing = compute_pricing(self._summary, rates)
        show_cost = not rates.is_zero

        for row, (fmt, cost) in enumerate(zip(self._summary.formats, pricing.formats)):
            cost_text = f"{cost.total:.2f} zł" if show_cost else "—"
            unit_cost = (cost.total / fmt.pieces) if fmt.pieces else 0.0
            unit_cost_text = f"{unit_cost:.2f} zł" if show_cost else "—"
            label = f"{fmt.width:g}×{fmt.height:g} mm"
            if fmt.name and fmt.name != "—":
                label = f"{fmt.name} · {label}"
            if fmt.material and fmt.material != "standard":
                label = f"{label}  [{fmt.material}]"
            values = [
                (label, False),
                (str(fmt.pieces), True),
                (f"{fmt.area_m2:.3f}", True),
                (f"{fmt.waste_m2:.3f}", True),
                (f"{fmt.reusable_m2:.3f}", True),
                (f"{fmt.gross_m2:.3f}", True),
                (str(fmt.cuts), True),
                (f"{fmt.saw_m:.2f}", True),
                (self._format_cut_time(fmt.time_s), True),
                (unit_cost_text, True),
                (cost_text, True),
            ]
            for col, (text, right) in enumerate(values):
                self._table.setItem(row, col, self._cell(text, right))

        # ── Totals row ───────────────────────────────────────────────────────
        s = self._summary
        total_row = len(s.formats)
        total_cost = f"{pricing.total:.2f} zł" if show_cost else "—"
        total_unit_cost = (pricing.total / s.total_pieces) if s.total_pieces else 0.0
        total_unit_cost_text = f"{total_unit_cost:.2f} zł" if show_cost else "—"
        totals = [
            ("RAZEM", False),
            (str(s.total_pieces), True),
            (f"{s.total_area_m2:.3f}", True),
            (f"{s.total_waste_m2:.3f}", True),
            (f"{s.total_reusable_m2:.3f}", True),
            (f"{s.total_board_area_m2:.3f}", True),
            (str(s.total_cuts), True),
            (f"{s.total_saw_m:.2f}", True),
            (self._format_cut_time(s.total_time_s), True),
            (total_unit_cost_text, True),
            (total_cost, True),
        ]
        bold = QFont()
        bold.setBold(True)
        for col, (text, right) in enumerate(totals):
            item = self._cell(text, right)
            item.setFont(bold)
            self._table.setItem(total_row, col, item)


def _make_envelope_icon(size: int = 16) -> "QIcon":
    """Draw a simple mail-envelope icon using QPainter (no external files needed)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#cbd5e1"), 1.3)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # envelope body
    p.drawRoundedRect(QRectF(1, 3, size - 2, size - 6), 1.0, 1.0)
    # flap V lines
    p.drawLine(QPointF(1, 3), QPointF(size / 2, size / 2 + 1))
    p.drawLine(QPointF(size / 2, size / 2 + 1), QPointF(size - 1, 3))
    p.end()
    from PySide6.QtGui import QIcon as _QIcon
    return _QIcon(pm)


_HEADER_DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})


def _normalize_header(value: object) -> str:
    return str(value or "").strip().lower().translate(_HEADER_DIACRITIC_MAP)


def _find_header_key(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {_normalize_header(h): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    for alias in aliases:
        for norm, original in normalized.items():
            if alias and norm.startswith(alias):
                return original
    return None


def _parse_numeric(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", ".").replace(" ", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Nieprawidłowa liczba: {value!r}") from exc


def parse_parts_file(path: Path) -> list[tuple[float, float, int]]:
    """Parse a dropped CSV or XLSX file into a list of (width, height, qty) tuples.

    Detects headers in Polish (Szerokość/Długość/Wysokość/Ilość) and English
    (width/length/height/quantity/qty); falls back to positional order when no
    recognized headers are found. Raises ValueError on bad rows.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        from import_export.csv_io import read_csv
        rows = read_csv(path)
    elif suffix == ".xlsx":
        from import_export.xlsx_io import read_xlsx
        rows = read_xlsx(path)
    else:
        raise ValueError(f"Nieobsługiwany format pliku: {path.name}")

    if not rows:
        return []

    headers = [str(k) for k in rows[0].keys()]
    width_key = _find_header_key(headers, ("szerokosc", "width", "szer", "w"))
    height_key = _find_header_key(
        headers, ("dlugosc", "wysokosc", "length", "height", "dl", "wys", "l", "h")
    )
    qty_key = _find_header_key(headers, ("ilosc", "quantity", "qty", "szt", "q"))

    parsed: list[tuple[float, float, int]] = []
    for index, row in enumerate(rows, start=1):
        if width_key and height_key and qty_key:
            w_raw, h_raw, q_raw = row.get(width_key), row.get(height_key), row.get(qty_key)
        else:
            values = [row.get(h) for h in headers]
            if len(values) < 3:
                raise ValueError(
                    f"Wiersz {index}: za mało kolumn — wymagane 3 (szerokość, długość, ilość)."
                )
            w_raw, h_raw, q_raw = values[0], values[1], values[2]

        width = _parse_numeric(w_raw)
        height = _parse_numeric(h_raw)
        qty = int(round(_parse_numeric(q_raw)))

        if width <= 0 or height <= 0 or qty <= 0:
            raise ValueError(
                f"Wiersz {index}: nieprawidłowe wartości (szer={w_raw}, dł={h_raw}, il={q_raw})."
            )
        parsed.append((width, height, qty))
    return parsed


class PartsTableWidget(QTableWidget):
    """QTableWidget that emits files_dropped when CSV/XLSX files are dragged onto it."""

    files_dropped = Signal(list)
    paste_requested = Signal(str)

    _ACCEPTED_SUFFIXES = (".csv", ".xlsx")

    def __init__(self, rows: int = 0, columns: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self.setAcceptDrops(True)

    def _extract_local_paths(self, event) -> list[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if local and local.lower().endswith(self._ACCEPTED_SUFFIXES):
                paths.append(Path(local))
        return paths

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_local_paths(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_local_paths(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = self._extract_local_paths(event)
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return
        super().dropEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_requested.emit(QApplication.clipboard().text())
            event.accept()
            return
        super().keyPressEvent(event)


class StockTableWidget(QTableWidget):
    """Stock grid with a click target for the cut-direction dot."""

    cut_axis_clicked = Signal(int, int)
    paste_requested = Signal(str)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        index = self.indexAt(event.position().toPoint())
        if (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and index.column() in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN)
        ):
            rect = self.visualRect(index)
            if event.position().x() <= rect.left() + 22:
                self.cut_axis_clicked.emit(index.row(), index.column())
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_requested.emit(QApplication.clipboard().text())
            event.accept()
            return
        super().keyPressEvent(event)


class PartsTableDelegate(QStyledItemDelegate):
    quantity_enter_pressed = Signal(int)
    cell_navigation_requested = Signal(int, int, bool)


    def setModelData(self, editor, model, index):
        from PySide6.QtWidgets import QComboBox
        if isinstance(editor, QComboBox) and index.column() == PART_THICKNESS_COLUMN:
            text = editor.currentText()
            if text == "Własna...":
                # Do not write "Własna..." to the model! Keep previous data or clear it.
                return
            model.setData(index, text)
            return
        super().setModelData(editor, model, index)

    def createEditor(self, parent: QWidget, option, index):
        if index.column() == PART_THICKNESS_COLUMN:
            table = self.parent()
            material_item = table.item(index.row(), PART_MATERIAL_COLUMN)
            material = material_item.text().strip() if material_item else ""
            main_window = table.window()
            
            thicknesses = []
            catalog_thicknesses = []
            if material and hasattr(main_window, "_catalog_entries_for"):
                entries = main_window._catalog_entries_for(material)
                catalog_thicknesses = [entry.thickness for entry in entries if entry.thickness > 0]
                thicknesses = catalog_thicknesses[:]

            # A selected supplier material is authoritative: do not leak an
            # old/manual thickness from another row into its priced list.
            # Reusing row values remains useful only for a material absent
            # from the uploaded catalogue.
            if material and not catalog_thicknesses:
                for r in range(table.rowCount()):
                    mat_item = table.item(r, PART_MATERIAL_COLUMN)
                    thk_item = table.item(r, PART_THICKNESS_COLUMN)
                    if mat_item and mat_item.text().strip().casefold() == material.casefold():
                        if thk_item:
                            try:
                                val = float(thk_item.text().replace(",", "."))
                                if val > 0:
                                    thicknesses.append(val)
                            except ValueError:
                                pass
                                
            thicknesses = sorted(list(set(thicknesses)))
                
            from PySide6.QtWidgets import QComboBox
            editor = QComboBox(parent)
            self._style_opaque_editor(editor)
            # The text field is editable from the first opening.  Previously
            # selecting "Własna..." changed this flag only after Qt had already
            # committed and closed the delegate, forcing the user to open the
            # cell a second time.
            editor.setEditable(True)
            
            line_edit = editor.lineEdit()
            if line_edit:
                line_edit.setFrame(False)
                line_edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                line_edit.setProperty("tableRow", index.row())
                line_edit.setProperty("tableColumn", index.column())
                line_edit.installEventFilter(self)
            
            editor.setMinimumHeight(28)
            editor.view().setMinimumWidth(120)
            editor.setProperty("tableRow", index.row())
            editor.setProperty("tableColumn", index.column())
            
            if thicknesses:
                editor.addItems([f"{t:g}" for t in thicknesses])
                
            if not catalog_thicknesses:
                editor.addItem("Własna...")
            
            def on_currentIndexChanged(idx):
                if editor.currentText() == "Własna...":
                    # A direct prompt is reliable even when Qt closes a combo
                    # delegate immediately after selecting an item.
                    def choose_value() -> None:
                        initial = _number(index.data(), 1.0)
                        value, accepted = QInputDialog.getDouble(
                            table.window(),
                            "Własna grubość",
                            "Grubość [mm]:",
                            max(0.01, initial),
                            0.01,
                            1000.0,
                            3,
                        )
                        if accepted:
                            window = table.window()
                            if hasattr(window, "_set_part_material_thickness"):
                                window._set_part_material_thickness(row=index.row(), material=material, thickness=value)
                            else:  # pragma: no cover - standalone delegate use
                                model.setData(index, f"{value:g}")

                    QTimer.singleShot(0, choose_value)
                    
            editor.currentIndexChanged.connect(on_currentIndexChanged)
            
            return editor
            
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            self._style_opaque_editor(editor)
            editor.setFrame(False)
            editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            editor.setMinimumHeight(28)
            editor.setProperty("tableRow", index.row())
            editor.setProperty("tableColumn", index.column())
            editor.installEventFilter(self)
        return editor

    @staticmethod
    def _style_opaque_editor(editor: QWidget) -> None:
        """Keep an active cell editor visually separate from the cell beneath it."""
        editor.setObjectName("tableCellEditor")
        editor.setAutoFillBackground(True)
        editor.setStyleSheet(
            """
            QLineEdit#tableCellEditor, QComboBox#tableCellEditor {
                background: #0b1524;
                color: #eef6ff;
                border: 1px solid #3d82d8;
                border-radius: 5px;
                padding: 0 6px;
            }
            QComboBox#tableCellEditor::drop-down { border: 0; width: 18px; }
            QComboBox#tableCellEditor QAbstractItemView {
                background: #0b1524;
                color: #eef6ff;
                selection-background-color: #1c4f8c;
            }
            """
        )

    def updateEditorGeometry(self, editor: QWidget, option, index) -> None:
        editor.setGeometry(option.rect)

    def eventFilter(self, editor: QWidget, event) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(editor, QLineEdit)
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        ):
            from PySide6.QtWidgets import QComboBox
            real_editor = editor.parent() if isinstance(editor.parent(), QComboBox) else editor
            row = int(real_editor.property("tableRow") or 0)
            col = int(real_editor.property("tableColumn") or -1)
            if col not in (PART_THICKNESS_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN, PART_QUANTITY_COLUMN):
                return super().eventFilter(editor, event)
            backwards = event.key() == Qt.Key.Key_Backtab or bool(
                event.key() == Qt.Key.Key_Tab
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            self.commitData.emit(real_editor)
            self.closeEditor.emit(real_editor, QAbstractItemDelegate.EndEditHint.NoHint)
            QTimer.singleShot(
                0,
                lambda row=row, col=col, backwards=backwards: self.cell_navigation_requested.emit(
                    row,
                    col,
                    backwards,
                ),
            )
            return True
        return super().eventFilter(editor, event)


class StockTableDelegate(QStyledItemDelegate):
    cell_navigation_requested = Signal(int, int, bool)

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() not in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN):
            return
        active = bool(index.data(STOCK_CUT_AXIS_ROLE))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#3b82f6" if active else "#73839a")
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(color if active else QColor("#263449"))
        center = QPointF(option.rect.left() + 11.0, option.rect.center().y())
        painter.drawEllipse(center, 4.0, 4.0)
        painter.restore()

    def createEditor(self, parent: QWidget, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setObjectName("tableCellEditor")
            editor.setAutoFillBackground(True)
            editor.setStyleSheet(
                """
                QLineEdit#tableCellEditor {
                    background: #0b1524;
                    color: #eef6ff;
                    border: 1px solid #3d82d8;
                    border-radius: 5px;
                    padding: 0 6px;
                }
                """
            )
            editor.setFrame(False)
            editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            editor.setMinimumHeight(28)
            editor.setProperty("tableRow", index.row())
            editor.setProperty("tableColumn", index.column())
        return editor

    def updateEditorGeometry(self, editor: QWidget, option, index) -> None:
        editor.setGeometry(option.rect)

    def eventFilter(self, editor: QWidget, event) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(editor, QLineEdit)
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        ):
            row = int(editor.property("tableRow") or 0)
            col = int(editor.property("tableColumn") or -1)
            if col not in (0, 1, 2, 3):
                return super().eventFilter(editor, event)
            backwards = event.key() == Qt.Key.Key_Backtab or bool(
                event.key() == Qt.Key.Key_Tab
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            QTimer.singleShot(
                0,
                lambda row=row, col=col, backwards=backwards: self.cell_navigation_requested.emit(
                    row,
                    col,
                    backwards,
                ),
            )
            return True
        return super().eventFilter(editor, event)


class _ButtonClickEffect(QObject):
    """Event filter that plays a quick opacity-dip flash when a QPushButton is pressed.

    Install on individual buttons:
        btn.installEventFilter(self._click_effect)

    Skips buttons that already have a graphics effect (e.g. pulse animation).
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: ANN001
        if (
            isinstance(obj, QPushButton)
            and event.type() == QEvent.Type.MouseButtonPress
            and obj.graphicsEffect() is None   # don't clash with pulse / shadow
        ):
            self._flash(obj)
        return False  # always let the event propagate

    @staticmethod
    def _flash(button: QPushButton) -> None:
        try:
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(1.0)
            button.setGraphicsEffect(effect)
        except RuntimeError:
            return  # button was deleted between event and now
        anim = QPropertyAnimation(effect, b"opacity", button)
        anim.setDuration(260)
        anim.setKeyValueAt(0.00, 1.0)
        anim.setKeyValueAt(0.35, 0.42)   # quick dip
        anim.setKeyValueAt(1.00, 1.0)    # recover
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            # Button may have been destroyed (page switch, window close)
            # before the animation finished — both setGraphicsEffect and
            # checking isWidgetType on a deleted C++ object raise RuntimeError.
            try:
                button.setGraphicsEffect(None)
            except RuntimeError:
                pass

        anim.finished.connect(_cleanup)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class SimpleCutWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SIEKACZ 9000")
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1600, 940)
        self.setMinimumSize(1280, 760)
        self.last_result = None
        self.project_window = None
        self._pending_update = None
        self._update_thread: QThread | None = None
        self._update_worker = None
        self._update_manual = False
        self._pending_project: Project | None = None
        self._order_groups: list[OrderGroupWidget] = []
        self._calculation_thread: QThread | None = None
        self._calculation_worker: CalculationWorker | None = None
        self._is_calculating = False
        self._is_exporting = False
        self._last_smart_stock_mode = False
        self._tutorial_completed = bool(repositories.get_setting("tutorial_completed", False))
        # The administrator PIN only grants a per-window override.  It is not
        # persisted in projects or settings, so reopening the program restores
        # the normal 15,000-piece safety limit.
        self._part_limit_override_authorized = False
        self._notification_tray: QSystemTrayIcon | None = None
        if sys.platform.startswith("win") and QSystemTrayIcon.isSystemTrayAvailable():
            self._notification_tray = QSystemTrayIcon(self.windowIcon(), self)
            self._notification_tray.setToolTip("SIEKACZ 9000")
            self._notification_tray.show()
        self.current_project_id: str | None = None
        self.current_project_name = ""
        self.current_client_name = ""
        self.current_project_notes = ""
        self.current_project_created_at = ""
        self._algo_settings: dict = dict(AlgorithmSettingsDialog.DEFAULTS)
        # Seed kerf from persisted DB value so it survives app restarts.
        self._algo_settings["kerf"] = float(repositories.get_setting("default_kerf", 5.0))
        self._algo_settings["saw_feed_m_per_min"] = float(repositories.get_setting("saw_feed_m_per_min", 12.0))
        self._algo_settings["animation_mode"] = str(repositories.get_setting("calculation_animation_mode", "economy"))
        self.toolbar_buttons: list[QPushButton] = []
        self.part_buttons: list[QPushButton] = []
        self.stock_buttons: list[QPushButton] = []
        self.expanded_history_ids: set[str] = set()

        self.sheet_width = self._double_input(2000, 0, 100000)
        self.sheet_width.setToolTip("Szerokość płyty bazowej (mm).\nNp. 2000 dla płyty 2000×1000.")
        self.sheet_height = self._double_input(1000, 0, 100000)
        self.sheet_height.setToolTip("Wysokość (krótszy bok) płyty bazowej (mm).\nNp. 1000 dla płyty 2000×1000.")
        self.sheet_qty = QSpinBox()
        self.sheet_qty.setRange(0, 10000)
        self.sheet_qty.setValue(1)
        self.sheet_qty.setSpecialValueText("")
        self.sheet_qty.setObjectName("premiumInput")
        self.sheet_qty.setMinimumHeight(34)
        self.sheet_qty.setToolTip("Dostępna liczba płyt tego formatu.")

        if QApplication.instance():
            apply_accent_mode(QApplication.instance(), "comfort")

        self.sheet_allowance = self._double_input(float(repositories.get_setting("sheet_allowance", 0.0)), 0, 200)
        self.sheet_allowance.setSuffix(" mm")
        self.sheet_allowance.setToolTip(
            "Naddatek technologiczny płyty (mm) — doliczany do nominalnych wymiarów płyty.\n"
            "Pozwala uwzględnić szlif krawędziowy lub inne obróbki brzegów."
        )
        min_offcut = float(repositories.get_setting("min_reusable_offcut_size", 200.0))
        if min_offcut < 10.0: min_offcut = 200.0
        self.min_reusable_offcut = self._double_input(min_offcut, 0, 2000)
        self.min_reusable_offcut.setSuffix(" mm")
        self.min_reusable_offcut.setToolTip(
            "Minimalny rozmiar boku użytecznego odpadu (mm).\n"
            "Odpady mniejsze od tego wymiaru są traktowane jako złom i nie trafiają do listy resztek."
        )

        self.theme = QComboBox()
        self.theme.setObjectName("premiumInput")
        self.theme.addItem("Ciemny", "dark")
        self.theme.addItem("Jasny", "light")
        self._set_theme_combo(repositories.get_setting("theme", "dark"))

        self.display_orientation = QComboBox()
        self.display_orientation.setObjectName("premiumInput")
        self.display_orientation.addItem("Poziomo (wysokość płyty wzdłuż ekranu)", "horizontal")
        self.display_orientation.addItem("Pionowo", "vertical")
        self.display_orientation.addItem("Auto", "auto")
        self._set_display_orientation_combo(repositories.get_setting("display_orientation", "horizontal"))

        self.recent_sheet_formats = QComboBox()
        self.recent_sheet_formats.setObjectName("premiumInput")
        self.recent_sheet_formats.addItem("Wybierz ostatni format", "")
        self._refresh_recent_sheet_formats()
        self.recent_sheet_formats.activated.connect(self._apply_recent_sheet_format)

        self._last_thickness = 18.0
        self._material_catalog: list[MaterialCatalogEntry] = []
        # Excel often saves by replacing the workbook.  Watching the file and
        # its folder catches both regular and atomic saves; the timer waits for
        # a complete workbook before it is parsed again.
        self._catalog_watcher = QFileSystemWatcher(self)
        self._catalog_refresh_timer = QTimer(self)
        self._catalog_refresh_timer.setSingleShot(True)
        self._catalog_refresh_timer.setInterval(900)
        self._catalog_refresh_timer.timeout.connect(self._refresh_uploaded_material_catalog_if_changed)
        self._catalog_watcher.fileChanged.connect(self._schedule_material_catalog_refresh)
        self._catalog_watcher.directoryChanged.connect(self._schedule_material_catalog_refresh)
        self.material_selector = QComboBox()
        self.material_selector.setObjectName("premiumInput")
        self.material_selector.setEditable(True)
        self.material_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.material_selector.setMinimumWidth(0)
        self.material_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.material_selector.view().setMinimumWidth(430)
        self.material_selector.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.material_selector.view().setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.material_selector.setToolTip("Materiał płyty. Lista pochodzi z zaimportowanego cennika XLSX.")
        self.material_selector.lineEdit().setPlaceholderText("Wybierz lub wpisz materiał")
        self.material_selector.lineEdit().setClearButtonEnabled(True)
        self._material_popup_filter = _OpenMaterialPopupFilter(self.material_selector)
        self.material_selector.lineEdit().installEventFilter(self._material_popup_filter)
        self._material_completer = QCompleter(self.material_selector.model(), self)
        self._material_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._material_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._material_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._material_completer.activated[str].connect(self._select_catalog_material_from_completion)
        self.material_selector.setCompleter(self._material_completer)
        self.material_selector.lineEdit().editingFinished.connect(self._commit_typed_catalog_material)
        self.catalog_thickness_selector = QComboBox()
        self.catalog_thickness_selector.setObjectName("premiumInput")
        self.catalog_thickness_selector.setEditable(True)
        self.catalog_thickness_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.catalog_thickness_selector.setMinimumWidth(0)
        self.catalog_thickness_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.catalog_thickness_selector.setToolTip("Grubości dostępne dla wybranego materiału.")
        self.catalog_thickness_selector.lineEdit().setPlaceholderText("Wybierz lub wpisz grubość")
        self._thickness_popup_filter = _OpenMaterialPopupFilter(self.catalog_thickness_selector)
        self.catalog_thickness_selector.lineEdit().installEventFilter(self._thickness_popup_filter)
        self.catalog_thickness_selector.lineEdit().editingFinished.connect(self._apply_catalog_selection)
        self._thickness_completer = QCompleter(self.catalog_thickness_selector.model(), self)
        self._thickness_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._thickness_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._thickness_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.catalog_thickness_selector.setCompleter(self._thickness_completer)
        self.material_selector.currentIndexChanged.connect(self._refresh_catalog_thicknesses)
        self.catalog_thickness_selector.currentIndexChanged.connect(self._apply_catalog_selection)
        self._load_material_catalog()

        self.stock_table = StockTableWidget(0, 8)
        self.stock_table.setObjectName("partsTable")
        self.stock_table.setHorizontalHeaderLabels(["MATERIAŁ", "GRUBOŚĆ", "WYSOKOŚĆ", "SZEROKOŚĆ", "FORMAT", "ILOŚĆ", "", ""])
        for column, hint in enumerate(("Materiał przypisany z formatki", "Grubość [mm]", "Wysokość [mm]", "Szerokość [mm]", "Szybki format dostępny dla materiału", "Ilość sztuk", "Oznacz płytę jako priorytetową", "Sztapel płyt")):
            self.stock_table.horizontalHeaderItem(column).setToolTip(hint)
        self.stock_table.horizontalHeaderItem(STOCK_STACK_COLUMN).setIcon(_stack_icon())
        self.stock_table.horizontalHeaderItem(STOCK_STACK_COLUMN).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stock_table.setAlternatingRowColors(True)
        self.stock_table.verticalHeader().setVisible(False)
        self.stock_table.setShowGrid(False)
        self.stock_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.stock_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.stock_delegate = StockTableDelegate(self.stock_table)
        self.stock_delegate.cell_navigation_requested.connect(self._handle_stock_nav_key)
        self.stock_table.setItemDelegate(self.stock_delegate)
        self.stock_table.cut_axis_clicked.connect(self._toggle_stock_cut_axis)
        self.stock_table.paste_requested.connect(self._paste_stock_rows)
        self.stock_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.SelectedClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.stock_table.setMinimumHeight(126)
        self.stock_table.verticalHeader().setDefaultSectionSize(34)
        self.stock_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # A dense table is easier to scan when every field has the same visual
        # rhythm.  Do not stretch the material field: it made the remaining
        # columns unreadably narrow.
        stock_column_widths = {
            # Qt uses logical pixels.  At Windows 125% DPI the previous total
            # was visibly too narrow and left unused table space on the right.
            STOCK_MATERIAL_COLUMN: 96,
            STOCK_THICKNESS_COLUMN: 78,
            STOCK_HEIGHT_COLUMN: 86,
            STOCK_WIDTH_COLUMN: 90,
            STOCK_FORMAT_COLUMN: 76,
            # Keep a real safety margin for the table frame and scroll-bar:
            # equal-to-viewport width still causes a horizontal bar in Qt.
            STOCK_QUANTITY_COLUMN: 52,
            STOCK_PRIORITY_COLUMN: 28,
            STOCK_STACK_COLUMN: 64,
        }
        for column in range(self.stock_table.columnCount()):
            self.stock_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.stock_table.setColumnWidth(column, stock_column_widths[column])
        # All descriptive columns keep their readable fixed widths.  The last,
        # icon-based stack column absorbs only the genuinely unused remainder,
        # so the row reaches the right border without squeezing any heading.
        self.stock_table.horizontalHeader().setSectionResizeMode(
            STOCK_STACK_COLUMN, QHeaderView.ResizeMode.Stretch
        )
        self.stock_table.setIconSize(QSize(14, 14))

        initial_material = self._selected_material_name(self._last_thickness)
        initial_format = self._default_catalog_format(initial_material, self._last_thickness)
        self._add_stock_row({
            "material": initial_material,
            "thickness": self._last_thickness,
            "width": initial_format.width if initial_format is not None else 1000,
            "height": initial_format.height if initial_format is not None else 2000,
            "quantity": 1,
            "template": True,
        })

        self.parts = PartsTableWidget(0, 5)
        self.parts.setObjectName("partsTable")
        self.parts.setHorizontalHeaderLabels(["MATERIAŁ", "GRUBOŚĆ", "WYSOKOŚĆ", "SZEROKOŚĆ", "ILOŚĆ"])
        for column, hint in enumerate(("Najpierw wybierz materiał", "Grubość [mm]", "Wysokość [mm]", "Szerokość [mm]", "Ilość sztuk")):
            self.parts.horizontalHeaderItem(column).setToolTip(hint)
        self.parts.setToolTip("")
        self.parts.files_dropped.connect(self._on_parts_files_dropped)
        self.parts.paste_requested.connect(self._paste_part_rows)
        self.parts.setAlternatingRowColors(True)
        self.parts.verticalHeader().setVisible(False)
        self.parts.setShowGrid(False)
        self.parts.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.parts.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.parts.setMinimumHeight(178)
        self.parts.setWordWrap(False)
        self.parts_delegate = PartsTableDelegate(self.parts)
        self.parts_delegate.quantity_enter_pressed.connect(self._add_or_focus_next_part_row)
        self.parts_delegate.cell_navigation_requested.connect(self._handle_part_nav_key)
        self.parts.setItemDelegate(self.parts_delegate)
        self.parts.cellClicked.connect(self._edit_part_cell_on_click)
        self.parts.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.SelectedClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.parts.verticalHeader().setDefaultSectionSize(36)
        self.parts.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Parts are the primary data-entry grid: five equal columns always
        # fill the whole available viewport, without a horizontal scrollbar.
        for column in range(self.parts.columnCount()):
            self.parts.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)


        self.warning = QLabel("")
        self.warning.setObjectName("warningBanner")
        self.warning.setWordWrap(True)
        self.warning.hide()

        self.layout_view = LayoutView()
        self.layout_view.set_theme(self.current_theme())
        self.layout_view.set_display_orientation(self.current_display_orientation())

        self._parts_undo_stack: list[tuple] = []
        self._parts_redo_stack: list[tuple] = []
        self._parts_undo_suspended = False
        self._parts_current_snapshot: tuple | None = None
        self._build_toolbar()
        self._build_layout()
        self._load_example_rows()
        self._parts_current_snapshot = self._snapshot_parts()
        self.stock_table.itemChanged.connect(self._on_stock_item_changed)
        self.parts.itemChanged.connect(self._on_parts_item_changed)
        self._refresh_history_view()
        self.layout_view.show_result(None)
        self.theme.currentIndexChanged.connect(self.apply_selected_theme)
        self.display_orientation.currentIndexChanged.connect(self.apply_display_orientation)
        self.statusBar().showMessage("Gotowe do obliczeń | Jednostki: mm")
        apply_button_cursors(self)
        self._install_click_effects()
        self.statusBar().showMessage(f"Gotowe do obliczeń | SIEKACZ 9000 v{APP_VERSION} | Jednostki: mm")
        # Check for updates shortly after the window is up (no-op until the
        # GitHub repo is configured in app/updater.py).
        QTimer.singleShot(1800, self._start_update_check)
        _app = QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._stop_update_thread)

        self._setup_publisher_dot()

    def _setup_publisher_dot(self):
        self.publisher_dot = QPushButton("•")
        self.publisher_dot.setFlat(True)
        self.publisher_dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.publisher_dot.setStyleSheet("color: #94a3b8; background: transparent; border: none; font-size: 16px; font-weight: bold; padding: 0 4px;")
        self.publisher_dot.clicked.connect(self._open_publisher_pin)
        self.statusBar().addPermanentWidget(self.publisher_dot)

    def _open_publisher_pin(self, *args):
        from PySide6.QtWidgets import QInputDialog
        pin, ok = QInputDialog.getText(self, "Zabezpieczenie", "Podaj kod PIN aby odblokować narzędzie publikacji:", QLineEdit.EchoMode.Password)
        if ok and pin == "1984":
            from app.publisher import PublisherDialog
            dlg = PublisherDialog(self)
            dlg.exec()
        elif ok:
            QMessageBox.warning(self, "Błąd", "Nieprawidłowy kod PIN.")

    def _double_input(self, value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(2)
        widget.setValue(value)
        if minimum == 0:
            widget.setSpecialValueText("")
        widget.setObjectName("premiumInput")
        widget.setMinimumHeight(34)
        return widget

    def _icon(self, name: str):
        pixmap = getattr(QStyle.StandardPixmap, name)
        return self.style().standardIcon(pixmap)

    def _build_toolbar(self) -> None:
        self.calc_action = QAction(self._icon("SP_MediaPlay"), "Oblicz", self)
        self.calc_action.setShortcuts([QKeySequence("Ctrl+Return"), QKeySequence("F5")])
        self.add_action = QAction(self._icon("SP_FileDialogNewFolder"), "Dodaj formatkę", self)
        self.remove_action = QAction(self._icon("SP_TrashIcon"), "Usuń", self)
        self.save_project_action = QAction(self._icon("SP_DialogApplyButton"), "Zapisz projekt", self)
        self.save_project_action.setShortcut(QKeySequence.StandardKey.Save)
        self.png_action = QAction(self._icon("SP_DialogSaveButton"), "Zapisz PNG", self)
        self.png_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.new_cut_action = QAction("Nowy rozkrój", self)
        self.new_cut_action.setShortcut(QKeySequence.StandardKey.New)

        self.calc_action.triggered.connect(self.calculate)
        self.add_action.triggered.connect(self.add_part_row)
        self.remove_action.triggered.connect(self.remove_selected_rows)
        self.save_project_action.triggered.connect(self.save_project)
        self.png_action.triggered.connect(self.export_png)
        self.new_cut_action.triggered.connect(self.new_cut)

        for action in (
            self.calc_action,
            self.add_action,
            self.remove_action,
            self.save_project_action,
            self.png_action,
            self.new_cut_action,
        ):
            self.addAction(action)

        self._install_parts_table_shortcuts()

    def _install_parts_table_shortcuts(self) -> None:
        insert_sc = QShortcut(QKeySequence(Qt.Key.Key_Insert), self.parts)
        insert_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        insert_sc.activated.connect(self.add_part_row)

        delete_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.parts)
        delete_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_sc.activated.connect(self.remove_selected_rows)

        undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self.parts)
        undo_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        undo_sc.activated.connect(self._undo_parts)

        redo_sc = QShortcut(QKeySequence.StandardKey.Redo, self.parts)
        redo_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        redo_sc.activated.connect(self._redo_parts)

        redo_alt = QShortcut(QKeySequence("Ctrl+Y"), self.parts)
        redo_alt.setContext(Qt.ShortcutContext.WidgetShortcut)
        redo_alt.activated.connect(self._redo_parts)

    def _install_click_effects(self) -> None:
        """Attach the opacity-dip flash to nav and toolbar buttons."""
        self._click_effect = _ButtonClickEffect(self)
        buttons: list[QPushButton] = list(self.toolbar_buttons)
        for attr in ("cut_tab_button", "history_tab_button", "back_to_cut_button"):
            btn = getattr(self, attr, None)
            if isinstance(btn, QPushButton):
                buttons.append(btn)
        for btn in buttons:
            btn.installEventFilter(self._click_effect)

    def _toolbar_button(self, action: QAction, primary: bool = False) -> QPushButton:
        button = QPushButton(action.text())
        button.setIcon(action.icon())
        button.setIconSize(QSize(16, 16))
        button.setObjectName("toolbarPrimary" if primary else "toolbarButton")
        button.clicked.connect(lambda checked=False, item=action: item.trigger())
        self.toolbar_buttons.append(button)
        return button

    def _build_layout(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")

        self.stack = QStackedWidget()
        self.stack.setObjectName("mainStack")
        self.stack.addWidget(self._cut_tab())
        self.stack.addWidget(self._history_tab())

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        main_column = QWidget()
        main_column.setObjectName("mainColumn")
        main_layout = QVBoxLayout(main_column)
        main_layout.setContentsMargins(12, 12, 18, 10)
        main_layout.setSpacing(12)
        main_layout.addWidget(self._top_bar())
        main_layout.addWidget(self.stack, 1)

        root_layout.addWidget(main_column, 1)
        self.setCentralWidget(root)


    def _side_bar(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("sideRail")
        rail.setFixedWidth(206)

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(14, 30, 14, 14)
        layout.setSpacing(8)

        logo = QLabel()
        logo.setObjectName("sideLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(_resource_path("assets/app_icon.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(46, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("S")

        title = QLabel("SIEKACZ 9000")
        title.setObjectName("sideTitle")
        title.setMinimumWidth(112)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("sideVersion")

        brand = QWidget()
        brand.setObjectName("sideBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(4, 0, 0, 26)
        brand_layout.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        text_box.addWidget(title)
        text_box.addWidget(version)
        brand_layout.addWidget(logo)
        brand_layout.addLayout(text_box, 1)
        layout.addWidget(brand)
        layout.addSpacing(14)

        self.cut_tab_button = QPushButton("Rozkrój")
        self.cut_tab_button.setObjectName("sideNavButton")
        self.cut_tab_button.setCheckable(True)
        self.cut_tab_button.setChecked(True)
        self.cut_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cut_tab_button.clicked.connect(lambda: self._select_page(0))
        self.side_cut_button = self.cut_tab_button

        self.history_tab_button = QPushButton("Projekty")
        self.history_tab_button.setObjectName("sideNavButton")
        self.history_tab_button.setCheckable(True)
        self.history_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_tab_button.clicked.connect(lambda: self._select_page(1))
        self.side_projects_button = self.history_tab_button

        materials_btn = QPushButton("Materiały")
        materials_btn.setObjectName("sideNavButton")
        materials_btn.setEnabled(False)

        self.back_to_cut_button = QPushButton("Powrót")
        self.back_to_cut_button.setObjectName("sideNavButton")
        self.back_to_cut_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_to_cut_button.clicked.connect(lambda: self._select_page(0))
        self.back_to_cut_button.hide()

        for button in (self.cut_tab_button, self.history_tab_button, materials_btn, self.back_to_cut_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)

        layout.addStretch(1)

        made = QLabel("Made by Kewin")
        made.setObjectName("sideMuted")
        layout.addWidget(made)

        return rail

    def _top_bar(self) -> QWidget:
        header = QWidget()
        header.setObjectName("appHeader")

        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        brand = QWidget()
        brand.setObjectName("topbarBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 10, 0)
        brand_layout.setSpacing(10)

        logo = QLabel()
        logo.setObjectName("topbarLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(_resource_path("assets/app_icon.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("S")

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        title = QLabel("SIEKACZ 9000")
        title.setObjectName("topbarTitle")
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("topbarVersion")
        title_box.addWidget(title)
        title_box.addWidget(version)
        brand_layout.addWidget(logo)
        brand_layout.addLayout(title_box)
        layout.addWidget(brand)

        self.cut_tab_button = QPushButton("Rozkrój")
        self.cut_tab_button.setObjectName("topbarNavButton")
        self.cut_tab_button.setCheckable(True)
        self.cut_tab_button.setChecked(True)
        self.cut_tab_button.clicked.connect(lambda: self._select_page(0))

        self.history_tab_button = QPushButton("Projekty")
        self.history_tab_button.setObjectName("topbarNavButton")
        self.history_tab_button.setCheckable(True)
        self.history_tab_button.clicked.connect(lambda: self._select_page(1))

        self.tutorial_button = QPushButton("SAMOUCZEK")
        self.tutorial_button.setObjectName("topbarTutorial")
        self.tutorial_button.setToolTip("Otwórz pełny samouczek obsługi programu")
        self.tutorial_button.clicked.connect(self.open_tutorial)
        self.tutorial_button.setVisible(not self._tutorial_completed)

        for button in (self.cut_tab_button, self.history_tab_button, self.tutorial_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(button)

        layout.addStretch(1)

        self.new_cut_button = QPushButton("Nowy rozkrój")
        self.new_cut_button.setObjectName("topbarPrimary")
        self.new_cut_button.setToolTip("Wyczyść dane i rozpocznij nowy rozkrój")
        self.new_cut_button.clicked.connect(self.new_cut)

        save_btn = QPushButton("Zapisz projekt")
        self.save_project_button = save_btn
        save_btn.setObjectName("topbarButton")
        save_btn.setToolTip("Zapisz aktualny projekt")
        save_btn.clicked.connect(self.save_project)

        self.send_button = QPushButton("Wyślij")
        self.send_button.setObjectName("topbarButton")
        self.send_button.setToolTip("Przygotuj wysyłkę projektu")
        self.send_button.clicked.connect(self.open_send_dialog)

        self._algo_btn = QPushButton("Ustawienia")
        self._algo_btn.setObjectName("topbarModeButton")
        self._algo_btn.setToolTip("Ustawienia algorytmu rozkroju")
        self._algo_btn.clicked.connect(self._open_algo_settings)

        self.update_button = QPushButton("Aktualizacja")
        self.update_button.setObjectName("topbarPrimary")
        self.update_button.setToolTip("Pokaż dostępną aktualizację")
        self.update_button.clicked.connect(self._show_pending_update)
        self.update_button.hide()

        for button in (
            self.new_cut_button,
            save_btn,
            self.send_button,
            self._algo_btn,
            self.update_button,
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.toolbar_buttons.append(button)
            layout.addWidget(button)

        self._update_algo_btn()
        return header

    def open_tutorial(self) -> None:
        dialog = TutorialDialog(self)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and dialog.completed:
            self._tutorial_completed = True
            repositories.set_setting("tutorial_completed", True)
            self.tutorial_button.hide()
            self.statusBar().showMessage(
                "Samouczek ukończony. Możesz uruchomić go ponownie w Ustawieniach.",
                6000,
            )

    @safe_ui_action("Nie udało się przełączyć widoku.")
    def _select_page(self, index: int) -> None:
        previous = self.stack.currentIndex()
        if previous == index:
            return
        old_widget = self.stack.currentWidget()

        # ── 1. Capture the old page as a pixmap overlay for cross-fade ────────
        # The overlay sits on top of the stack while the new page fades in under
        # it. Defensive: skip the overlay entirely if grab() fails for any
        # reason (some Qt versions crash on grab during teardown / on widgets
        # nested in QGraphicsView).
        overlay: QLabel | None = None
        try:
            if (
                old_widget is not None
                and old_widget.isVisible()
                and old_widget.width() > 8
                and old_widget.height() > 8
                and self.stack.width() > 8
                and self.stack.height() > 8
            ):
                pixmap = old_widget.grab()
                if pixmap is not None and not pixmap.isNull():
                    overlay = QLabel(self.stack)
                    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    overlay.setPixmap(pixmap)
                    overlay.resize(self.stack.size())
                    overlay.show()
                    overlay.raise_()
        except Exception:
            overlay = None

        # ── 2. Switch page immediately (state is correct for tests & logic) ───
        self.stack.setCurrentIndex(index)
        self.cut_tab_button.setChecked(index == 0)
        self.history_tab_button.setChecked(index == 1)
        if hasattr(self, "back_to_cut_button"):
            self.back_to_cut_button.setVisible(index != 0)
        if index == 1:
            self._refresh_history_view()

        # ── 3. Fade in the new page from transparent ──────────────────────────
        self._fade_in_widget(self.stack.currentWidget(), duration=240, start_opacity=0.0)

        # ── 4. Dissolve the overlay so the new page is revealed smoothly ──────
        if overlay is not None:
            # Use a guarded deleter — if the window is destroyed before the
            # animation finishes, deleteLater on a dangling widget would crash.
            def _safe_delete(ref=overlay) -> None:
                try:
                    ref.deleteLater()
                except RuntimeError:
                    pass
            self._fade_out_widget(overlay, duration=220, callback=_safe_delete)
            # Slide the outgoing snapshot gently upward as it dissolves, so the
            # transition reads as "slide up + cross-fade" rather than a plain
            # fade.  The overlay is absolutely positioned (not in a layout), so
            # animating its position is safe.
            try:
                slide = QPropertyAnimation(overlay, b"pos", overlay)
                slide.setDuration(220)
                slide.setStartValue(overlay.pos())
                slide.setEndValue(overlay.pos() + QPoint(0, -22))
                slide.setEasingCurve(QEasingCurve.Type.InCubic)
                slide.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            except RuntimeError:
                pass

    @safe_ui_action("Nie udało się przygotować nowego rozkroju.")
    def new_cut(self) -> None:
        if self._is_calculating:
            self.statusBar().showMessage("Trwa liczenie. Poczekaj na zakończenie przed rozpoczęciem nowego rozkroju.")
            return
        self.current_project_id = None
        self.current_project_name = ""
        self.current_client_name = ""
        self.current_project_notes = ""
        self.current_project_created_at = ""
        self.last_result = None
        self._pending_project = None
        if hasattr(self, "_order_groups"):
            self._clear_order_groups()
        self.sheet_width.setValue(0)
        self.sheet_height.setValue(0)
        self.sheet_qty.setValue(0)
        self._algo_settings["kerf"] = float(repositories.get_setting("default_kerf", 5.0))
        self._algo_settings["saw_feed_m_per_min"] = float(repositories.get_setting("saw_feed_m_per_min", 12.0))
        self._algo_settings["animation_mode"] = str(repositories.get_setting("calculation_animation_mode", "economy"))
        self.sheet_allowance.setValue(float(repositories.get_setting("sheet_allowance", 0.0)))
        min_offcut = float(repositories.get_setting("min_reusable_offcut_size", 200.0))
        if min_offcut < 10.0: min_offcut = 200.0
        self.min_reusable_offcut.setValue(min_offcut)
        self.stock_table.setRowCount(0)
        self._add_stock_row({"width": "", "height": "", "quantity": ""})
        if hasattr(self, "smart_stock_checkbox"):
            self.smart_stock_checkbox.setChecked(False)
        self.parts.clearSelection()
        self.parts.setRowCount(0)
        self._reset_parts_undo_history()
        self.warning.clear()
        self.warning.hide()
        self.layout_view.show_result(None)
        if hasattr(self, "preview_pdf_button"):
            self.preview_pdf_button.setEnabled(False)
        # Hide zoom controls on start / new-cut screen (no preview to zoom into)
        if hasattr(self, "_zoom_widget"):
            self._zoom_widget.hide()
        if hasattr(self, "_sheet_nav_widget"):
            self._sheet_nav_widget.hide()
        self._select_page(0)
        self.statusBar().showMessage("Nowy rozkrój | Uzupełnij parametry i formatki")


    def _preview_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("previewHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 16, 24, 12)
        layout.setSpacing(14)

        self.input_panel_toggle = QToolButton()
        self.input_panel_toggle.setObjectName("headerIconButton")
        self.input_panel_toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowLeft))
        self.input_panel_toggle.setToolTip("Schowaj panel wprowadzania danych")
        self.input_panel_toggle.clicked.connect(self._toggle_input_panel)
        layout.addWidget(self.input_panel_toggle)

        title = QLabel("Podgląd rozkroju")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.preview_pdf_button = QToolButton()
        self.preview_pdf_button.setIcon(_eye_icon())
        self.preview_pdf_button.setToolTip("Otwórz tymczasowy podgląd raportu PDF")
        self.preview_pdf_button.setEnabled(False)
        self.preview_pdf_button.setObjectName("headerIconButton")
        self.preview_pdf_button.clicked.connect(self.open_temporary_pdf_preview)
        layout.addWidget(self.preview_pdf_button)
        layout.addStretch(1)

        metrics = QWidget()
        metrics.setObjectName("previewMetrics")
        metrics.setMinimumWidth(460)
        metrics.setMaximumWidth(600)
        metrics.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        metrics_layout = QHBoxLayout(metrics)
        metrics_layout.setContentsMargins(18, 8, 18, 8)
        metrics_layout.setSpacing(18)

        self.preview_util_value = QLabel("--")
        self.preview_sheet_value = QLabel("--")
        self.preview_part_value = QLabel("--")
        self.preview_waste_value = QLabel("--")
        for label, value in (
            ("Wykorzystanie materiału", self.preview_util_value),
            ("Ilość płyt", self.preview_sheet_value),
            ("Ilość części", self.preview_part_value),
            ("Odpady", self.preview_waste_value),
        ):
            box = QWidget()
            box.setObjectName("previewMetricBox")
            box.setMinimumWidth(92)
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(1)
            caption = QLabel(label)
            caption.setObjectName("previewMetricLabel")
            caption.setWordWrap(True)
            caption.setMinimumWidth(0)
            value.setObjectName("previewMetricValue")
            box_layout.addWidget(caption)
            box_layout.addWidget(value)
            metrics_layout.addWidget(box)
        layout.addWidget(metrics)
        return header

    def _cut_tab(self) -> QWidget:
        left = QWidget()
        left.setObjectName("controlPanel")
        left.setMinimumWidth(650)
        left.setMaximumWidth(670)

        layout = QVBoxLayout(left)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        plate_section = self._plate_parameters_section()
        parts_section = self._parts_section()
        self._apply_card_shadow(plate_section)
        self._apply_card_shadow(parts_section)
        layout.addWidget(plate_section)
        layout.addWidget(parts_section, 1)

        # ── Extra board groups (multi-board orders) ──────────────────────────
        self._groups_container = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_container)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(10)
        layout.addWidget(self._groups_container)

        layout.addWidget(self._calculate_button())

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        scroll.setMinimumWidth(0)
        scroll.setMaximumWidth(700)

        right = QWidget()
        right.setObjectName("workspacePanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._preview_header_widget = self._preview_header()
        right_layout.addWidget(self._preview_header_widget)

        canvas = QWidget()
        canvas.setObjectName("canvasStack")
        canvas_layout = QStackedLayout(canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        canvas_layout.addWidget(self.layout_view)
        self.optimization_progress_overlay = OptimizationProgressOverlay()
        canvas_layout.addWidget(self.optimization_progress_overlay)
        self.optimization_progress_overlay.hide()
        self._progress_cancel_connected = False
        canvas_layout.setCurrentWidget(self.layout_view)

        # ── Warning banner ─────────────────────────────────────────────────────
        # Style comes from unified theme (QLabel#warningBanner). No inline QSS
        # so the design system controls colours, border, radius, and padding.
        # Qt collapses hidden widgets in QVBoxLayout by default, so a hidden
        # banner takes 0 px height.
        self.warning.setMaximumHeight(64)
        self.warning.hide()
        right_layout.addWidget(self.warning)   # ← ABOVE nav+canvas, collapses when hidden

        # ── T1-3: sheet-navigator chips — fixed strip above canvas ───────────
        self._sheet_nav_widget = self._sheet_nav_bar()
        self._sheet_nav_widget.hide()           # hidden until results arrive
        right_layout.addWidget(self._sheet_nav_widget)   # fixed, collapses when hidden

        # ── Zoom buttons: compact floating overlay inside canvas ──────────────
        self._zoom_widget = self._zoom_controls()
        self._zoom_widget.setParent(canvas)
        # Soft drop-shadow so the floating panel reads against any sheet colour
        self._apply_card_shadow(self._zoom_widget, blur=18, y_offset=4, alpha=110)
        self._zoom_widget.hide()  # hidden until a result is shown

        self._canvas_root = canvas

        # Event filter repositions only the zoom widget on canvas resize
        self._canvas_overlay_filter = _CanvasOverlayFilter(canvas, self._zoom_widget)
        canvas.installEventFilter(self._canvas_overlay_filter)

        self._apply_card_shadow(canvas, blur=28, y_offset=8, alpha=60)
        right_layout.addWidget(canvas, 1)

        splitter = QSplitter()
        splitter.setObjectName("mainSplitter")
        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setCollapsible(0, True)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([675, 885])
        self._main_splitter = splitter
        self._input_scroll = scroll
        self._input_panel_width = 675

        tab = QWidget()
        tab.setObjectName("workspaceRoot")
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(splitter)
        return tab

    def _restore_input_panel_width(self) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if splitter is None or splitter.width() <= 0:
            return
        width = min(675, max(640, splitter.width() // 2))
        splitter.setSizes([width, max(1, splitter.width() - width)])

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not getattr(self, "_input_panel_initialized", False):
            self._input_panel_initialized = True
            QTimer.singleShot(0, self._restore_input_panel_width)

    def _toggle_input_panel(self) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        total = max(sum(sizes), splitter.width())
        collapsed = not sizes or sizes[0] <= 12
        if collapsed:
            width = max(640, int(getattr(self, "_input_panel_width", 675)))
            target = [width, max(1, total - width)]
            self.input_panel_toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowLeft))
            self.input_panel_toggle.setToolTip("Schowaj panel wprowadzania danych")
        else:
            self._input_panel_width = sizes[0]
            target = [0, total]
            self.input_panel_toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
            self.input_panel_toggle.setToolTip("Pokaż panel wprowadzania danych")
        animation = QVariantAnimation(self)
        animation.setDuration(220)
        animation.setStartValue((sizes or [0, total])[0])
        animation.setEndValue(target[0])
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(lambda value: splitter.setSizes([int(value), max(1, total - int(value))]))
        animation.finished.connect(animation.deleteLater)
        self._input_panel_animation = animation
        animation.start()

    def _zoom_controls(self) -> QWidget:
        controls = QWidget()
        controls.setObjectName("zoomControls")
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        _BTN = 44  # button side in px

        info_btn = QPushButton("i")
        info_btn.setObjectName("zoomButton")
        info_btn.setToolTip("Metryki cięcia i wycena zlecenia")
        info_btn.setFixedSize(_BTN, _BTN)
        info_btn.clicked.connect(self.open_cut_info_dialog)

        zoom_in = QPushButton("+")
        zoom_in.setObjectName("zoomButton")
        zoom_in.setToolTip("Przybliż podgląd  (+)")
        zoom_in.setFixedSize(_BTN, _BTN)
        zoom_in.clicked.connect(self.layout_view.zoom_in)

        zoom_out = QPushButton("-")
        zoom_out.setObjectName("zoomButton")
        zoom_out.setToolTip("Oddal podgląd  (−)")
        zoom_out.setFixedSize(_BTN, _BTN)
        zoom_out.clicked.connect(self.layout_view.zoom_out)

        # Zoom percent indicator + click-to-reset (T1-6).  Uses a QLabel
        # (not QPushButton) to avoid the default QPushButton padding that
        # was clipping "100%" inside a 44-px button.  Click handled manually.
        class _ClickableZoomLabel(QLabel):
            clicked = Signal()

            def mousePressEvent(self, event):  # type: ignore[override]
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit()
                    event.accept()
                    return
                super().mousePressEvent(event)

        self._zoom_percent_label = _ClickableZoomLabel("100%")
        self._zoom_percent_label.setObjectName("zoomPercent")
        self._zoom_percent_label.setToolTip("Aktualne powiększenie. Kliknij, aby zresetować do 100%.")
        # Width matches the button column so the pill looks aligned; height
        # generous enough that the text is never clipped.
        self._zoom_percent_label.setFixedSize(_BTN, 24)
        self._zoom_percent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_percent_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zoom_percent_label.clicked.connect(self.layout_view.reset_zoom)
        self._zoom_percent_label.setStyleSheet(
            "QLabel#zoomPercent {"
            "  background: rgba(15, 23, 42, 0.78);"
            "  color: #e0e7ff;"
            "  border: 1px solid rgba(255,255,255,0.12);"
            "  border-radius: 6px;"
            "  padding: 0px;"
            "  font-size: 10px;"
            "  font-weight: 600;"
            "}"
            "QLabel#zoomPercent:hover {"
            "  background: rgba(30, 41, 59, 0.92);"
            "  color: #ffffff;"
            "}"
        )

        for button in (info_btn, zoom_in, zoom_out):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)
        layout.addWidget(self._zoom_percent_label)

        # Listen for zoom changes from the view itself.
        self.layout_view.zoomChanged.connect(self._on_zoom_changed)

        # 3 buttons × 44 + label 24 + 3 gaps × 5 + 2 × 8 margin
        controls.setFixedSize(_BTN + 16, 3 * _BTN + 24 + 3 * 5 + 16)
        return controls

    def _on_zoom_changed(self, percent: int) -> None:
        if hasattr(self, "_zoom_percent_label"):
            self._zoom_percent_label.setText(f"{percent}%")

    # ------------------------------------------------------------------
    # T1-3: floating sheet-navigator chip bar (rebuilt each time results
    # change so chip count tracks the actual number of cards rendered).
    # ------------------------------------------------------------------
    def _sheet_nav_bar(self) -> QWidget:
        """Fixed-height strip above the canvas holding scrollable sheet-nav chips."""
        _BAR_H = 48

        bar = QWidget()
        bar.setObjectName("sheetNavBar")
        bar.setFixedHeight(_BAR_H)

        # QScrollArea fills the entire bar — no extra outer layout so nothing clips chips.
        scroll = QScrollArea(bar)
        scroll.setObjectName("sheetNavScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # Explicitly match the bar height so the viewport never clips.
        scroll.setFixedHeight(_BAR_H)

        chips_host = QWidget()
        chips_host.setObjectName("sheetNavChips")
        chips_layout = QHBoxLayout(chips_host)
        chips_layout.setContentsMargins(8, 0, 8, 0)
        chips_layout.setSpacing(4)
        scroll.setWidget(chips_host)

        # Stretch scroll area to full bar width using a zero-margin layout.
        outer = QHBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll)

        # Wheel-to-horizontal-scroll: install on all three layers so no event escapes.
        _wheel_filter = _WheelHScrollFilter(scroll)
        chips_host.installEventFilter(_wheel_filter)
        scroll.installEventFilter(_wheel_filter)
        scroll.viewport().installEventFilter(_wheel_filter)
        bar._wheel_filter = _wheel_filter  # keep reference alive

        bar.setStyleSheet(
            "QWidget#sheetNavBar {"
            "  background: #0f172a;"
            "  border-bottom: 1px solid rgba(255,255,255,0.08);"
            "}"
            "QScrollArea#sheetNavScroll { background: transparent; border: none; }"
            "QWidget#sheetNavChips { background: transparent; }"
            "QPushButton.sheetChip {"
            "  background: transparent;"
            "  color: #94a3b8;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 5px 16px;"
            "  font-size: 12px;"
            "  font-weight: 600;"
            "  min-height: 28px;"
            "}"
            "QPushButton.sheetChip:hover {"
            "  background: rgba(59, 130, 246, 0.18);"
            "  color: #e2e8f0;"
            "}"
            "QPushButton.sheetChip:pressed, QPushButton.sheetChip:checked {"
            "  background: rgba(59, 130, 246, 0.30);"
            "  color: #ffffff;"
            "}"
            "QPushButton.sheetChip[missing=\"true\"] {"
            "  color: #fbbf24;"
            "}"
            "QPushButton.sheetChip[missing=\"true\"]:hover {"
            "  background: rgba(251, 191, 36, 0.15);"
            "  color: #fde68a;"
            "}"
        )

        self._sheet_nav_chips_host = chips_host
        self._sheet_nav_layout = chips_layout
        self._sheet_nav_scroll = scroll
        return bar

    def _rebuild_sheet_nav(self, sheet_count: int, missing_count: int) -> None:
        if not hasattr(self, "_sheet_nav_layout"):
            return
        # Wipe the entire layout (widgets + spacers) to avoid stretch accumulation.
        while self._sheet_nav_layout.count():
            item = self._sheet_nav_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        # Leading stretch — chips will be centred between this and the trailing one.
        self._sheet_nav_layout.addStretch(1)

        # Prefer the labels produced by the layout view: identical boards are
        # collapsed into a single card (×N), so one chip == one drawn card and the
        # index passed to focus_sheet stays aligned with _sheet_card_bounds.
        labels: list[str] = []
        if hasattr(self, "layout_view"):
            labels = self.layout_view.nav_chip_labels()
        if not labels:
            labels = [f"Płyta {i + 1}" for i in range(sheet_count)] + [
                f"Brakująca {i + 1}" for i in range(missing_count)
            ]

        total = len(labels)
        # Hide nav entirely when there's nothing to navigate.
        if total <= 1:
            self._sheet_nav_widget.hide()
            return

        for index, label in enumerate(labels):
            is_missing = label.startswith("Brakująca")
            chip = QPushButton(label)
            chip.setProperty("class", "sheetChip")
            chip.setProperty("missing", "true" if is_missing else "false")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Przejdź do: {label}")
            # capture index by default-arg trick
            chip.clicked.connect(lambda _checked=False, idx=index: self.layout_view.focus_sheet(idx))
            self._sheet_nav_layout.addWidget(chip)
            # Subtle drop-shadow "lift" when the cursor is over a chip.
            if not hasattr(self, "_chip_hover_filter"):
                self._chip_hover_filter = HoverLiftFilter(self)
            self._chip_hover_filter.watch(chip)
            # Staggered cascade: each chip fades in slightly after the previous
            # one so the navigator "assembles" itself when a result arrives.
            # With many chips (> 6) the simultaneous QGraphicsOpacityEffect
            # compositing is expensive on slower hardware — skip individual fades
            # and let the parent container fade-in do the reveal instead.
            if total <= 6:
                self._fade_in_widget(chip, duration=200, start_opacity=0.0, delay=30 * index)

        # Trailing stretch — together with the leading one this centres the chips.
        self._sheet_nav_layout.addStretch(1)
        self._sheet_nav_widget.show()
        # Resize the host so QScrollArea computes the correct horizontal range.
        self._sheet_nav_chips_host.adjustSize()
        # Reset scroll to the beginning each time nav is rebuilt.
        if hasattr(self, "_sheet_nav_scroll"):
            self._sheet_nav_scroll.horizontalScrollBar().setValue(0)

    def _apply_card_shadow(self, widget: QWidget, blur: int = 22, y_offset: int = 5, alpha: int = 72) -> None:
        from PySide6.QtGui import QColor
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, y_offset)
        shadow.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(shadow)

    # ------------------------------------------------------------------
    # Animation helpers
    # ------------------------------------------------------------------
    def _fade_in_widget(
        self,
        widget: QWidget,
        duration: int = 240,
        start_opacity: float = 0.0,
        delay: int = 0,
    ) -> None:
        """Soft fade-in for any widget. Effect is removed at the end.

        When *delay* (ms) is given, the opacity effect is installed immediately
        at *start_opacity* (so the widget is hidden right away, no flicker) and
        the animation itself starts after the delay.  This lets callers stagger
        several widgets into a cascade.
        """
        if widget is None:
            return
        # Defensive: widget may be in the process of being torn down (Qt C++
        # object already deleted while Python wrapper is still alive). All
        # graphics-effect / property-animation calls then raise RuntimeError.
        try:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(start_opacity)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", widget)
        except RuntimeError:
            return
        animation.setDuration(duration)
        animation.setStartValue(start_opacity)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        animation.finished.connect(_cleanup)

        def _start() -> None:
            # The widget (and its parented animation) may have been torn down
            # during the delay — e.g. the nav bar was rebuilt before the
            # cascade finished.  Starting then raises RuntimeError; ignore it.
            try:
                animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            except RuntimeError:
                pass

        if delay > 0:
            QTimer.singleShot(delay, _start)
        else:
            _start()

    def _fade_out_widget(self, widget: QWidget, duration: int = 160, callback=None) -> None:
        """Fade a widget to transparent, then call *callback* (if given).

        The opacity effect is removed after the animation so it doesn't keep
        paying compositing cost.
        """
        if widget is None:
            if callback:
                callback()
            return
        try:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", widget)
        except RuntimeError:
            if callback:
                callback()
            return
        animation.setDuration(duration)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.InCubic)

        def _done() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if callback:
                try:
                    callback()
                except Exception:
                    pass

        animation.finished.connect(_done)
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _start_pulse(self, widget: QWidget) -> None:
        """Begin a subtle opacity pulse (fade 1.0 ↔ 0.72) on `widget`.

        Used while a long operation runs. Stop with _stop_pulse(widget).
        Narrower range and longer period keep the animation visually clear
        while reducing compositing work per frame on slower hardware.
        """
        if widget is None:
            return
        existing = getattr(widget, "_pulse_anim", None)
        if existing is not None and existing.state() == QAbstractAnimation.State.Running:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)
        group = QSequentialAnimationGroup(widget)
        down = QPropertyAnimation(effect, b"opacity", widget)
        down.setDuration(850)
        down.setStartValue(1.0)
        down.setEndValue(0.72)
        down.setEasingCurve(QEasingCurve.Type.InOutSine)
        up = QPropertyAnimation(effect, b"opacity", widget)
        up.setDuration(850)
        up.setStartValue(0.72)
        up.setEndValue(1.0)
        up.setEasingCurve(QEasingCurve.Type.InOutSine)
        group.addAnimation(down)
        group.addAnimation(up)
        group.setLoopCount(-1)
        widget._pulse_anim = group  # type: ignore[attr-defined]
        widget._pulse_effect = effect  # type: ignore[attr-defined]
        group.start()

    def _stop_pulse(self, widget: QWidget) -> None:
        if widget is None:
            return
        group = getattr(widget, "_pulse_anim", None)
        if group is not None:
            group.stop()
            widget._pulse_anim = None  # type: ignore[attr-defined]
        widget.setGraphicsEffect(None)
        widget._pulse_effect = None  # type: ignore[attr-defined]

    def _animate_status_utilization(self, final_pct: float, build_message) -> None:
        """Count the utilization percentage up from 0 to *final_pct*.

        ``build_message(pct)`` returns the full status-bar string for a given
        percentage, so only the number animates while the rest of the line stays
        constant.  A previous count-up (if any) is cancelled first so rapid
        recalculations don't fight over the status bar.
        """
        try:
            final_pct = max(0.0, float(final_pct))
        except (TypeError, ValueError):
            final_pct = 0.0

        existing = getattr(self, "_status_util_anim", None)
        if existing is not None:
            try:
                existing.stop()
            except RuntimeError:
                pass
            self._status_util_anim = None

        # Skip the animation for trivial values — just show the final string.
        if final_pct <= 0.05:
            self.statusBar().showMessage(build_message(final_pct))
            return

        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(final_pct)
        animation.setDuration(720)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(
            lambda value: self.statusBar().showMessage(build_message(float(value)))
        )

        def _settle() -> None:
            # Guarantee the exact final value is shown even if the last tick
            # landed slightly short.
            self.statusBar().showMessage(build_message(final_pct))
            self._status_util_anim = None

        animation.finished.connect(_settle)
        self._status_util_anim = animation
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _section_title(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        return label

    def _plate_parameters_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("sectionCard")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)
        self.smart_stock_checkbox = QCheckBox("Inteligentny dobór formatów")
        self.smart_stock_checkbox.setObjectName("smartStockMode")
        self.smart_stock_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.smart_stock_checkbox.setMinimumHeight(30)
        self.smart_stock_checkbox.setToolTip(
            "Program sprawdzi wszystkie wycenione formaty dla materiału i grubości oraz formaty "
            "wpisane ręcznie w tabeli, a następnie sam dobierze liczbę płyt każdego rozmiaru "
            "tak, aby ograniczyć odpad."
        )
        self.smart_stock_checkbox.toggled.connect(self._smart_stock_mode_changed)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self._section_title("Dostępne płyty"))
        title_row.addStretch(1)
        title_row.addWidget(self.smart_stock_checkbox)
        layout.addLayout(title_row)

        add_stock = QPushButton("+ Dodaj")
        add_stock.setObjectName("smallButton")
        remove_stock = QPushButton("- Usuń")
        remove_stock.setObjectName("smallButton")
        undo_stock = QPushButton("Cofnij")
        undo_stock.setObjectName("smallButton")
        undo_stock.setToolTip("Usuń ostatnio dodaną płytę (od dołu tabeli)")
        import_dxf = QToolButton()
        import_dxf.setObjectName("smallIconButton")
        import_dxf.setIcon(_upload_icon())
        import_dxf.setToolTip("Wczytaj formatki z pliku DXF")
        import_dxf.clicked.connect(self.import_dxf_parts)
        add_stock.clicked.connect(self._add_blank_stock_row_and_focus)
        remove_stock.clicked.connect(self._remove_selected_stock_rows)
        undo_stock.clicked.connect(self._remove_last_stock_row)
        add_stock.setMinimumWidth(94)
        remove_stock.setMinimumWidth(82)
        undo_stock.setMinimumWidth(92)
        self.stock_buttons.extend([add_stock, remove_stock, undo_stock, import_dxf, self.smart_stock_checkbox])



        stock_buttons = QHBoxLayout()
        stock_buttons.setSpacing(8)
        stock_buttons.addWidget(add_stock)
        stock_buttons.addWidget(remove_stock)
        stock_buttons.addWidget(undo_stock)
        stock_buttons.addWidget(import_dxf)
        stock_buttons.addStretch(1)

        layout.addLayout(stock_buttons)
        layout.addWidget(self.stock_table)
        return section

    def _smart_stock_mode_changed(self, enabled: bool) -> None:
        self._last_smart_stock_mode = bool(enabled)
        if enabled:
            self.statusBar().showMessage(
                "Tryb inteligentny: program dobierze mieszankę formatów cennikowych i płyt wpisanych ręcznie.",
                7000,
            )
        else:
            self.statusBar().showMessage("Tryb ręczny: używane są formaty i ilości wpisane w tabeli.", 5000)

    def _load_material_catalog(self) -> None:
        stored = repositories.get_setting("material_catalog", [])
        entries = catalog_from_dicts(stored) if isinstance(stored, list) else []
        source = str(repositories.get_setting("material_catalog_source", "") or "")
        uploaded_path = Path(str(repositories.get_setting("material_catalog_path", "") or ""))
        stored_revision = str(repositories.get_setting("material_catalog_revision", "") or "")

        # A selected workbook stays active.  When it is edited later, the
        # changed revision reloads it on startup so new sheets, rows, formats
        # and priced intersections automatically reach the selector.
        if source == "uploaded" and uploaded_path.is_file():
            try:
                revision = catalog_revision(uploaded_path)
                if revision != stored_revision:
                    entries = read_material_catalog(uploaded_path)
                    repositories.set_setting("material_catalog", [item.to_dict() for item in entries])
                    repositories.set_setting("material_catalog_revision", revision)
            except Exception as exc:
                _logger.warning("Could not refresh material catalog %s: %s", uploaded_path, exc)
        bundled = _resource_path("sample_data/material_catalog.xlsx")
        if bundled.exists():
            try:
                bundled_entries = read_material_catalog(bundled)
                bundled_revision = catalog_revision(bundled)
                bundled_stored_revision = str(repositories.get_setting("material_catalog_bundle_revision", "") or "")
                # The bundled Boral matrix is authoritative: no price at an
                # intersection means no available board. Do not retain entries
                # from an older bundled workbook.
                if not entries or (source != "uploaded" and bundled_revision != bundled_stored_revision):
                    entries = bundled_entries
                if entries and source != "uploaded" and bundled_revision != bundled_stored_revision:
                    repositories.set_setting("material_catalog", [item.to_dict() for item in entries])
                    repositories.set_setting("material_catalog_bundle_revision", bundled_revision)
                    repositories.set_setting("material_catalog_source", "bundled")
                    repositories.set_setting("material_catalog_revision", bundled_revision)
            except Exception as exc:
                _logger.warning("Nie udało się wczytać katalogu materiałów: %s", exc)
        self._material_catalog = entries
        self._populate_material_selector()
        self._watch_uploaded_material_catalog()

    def _uploaded_material_catalog_path(self) -> Path | None:
        if str(repositories.get_setting("material_catalog_source", "") or "") != "uploaded":
            return None
        value = str(repositories.get_setting("material_catalog_path", "") or "").strip()
        return Path(value) if value else None

    def _watch_uploaded_material_catalog(self) -> None:
        """Watch the active supplier workbook and its parent directory."""
        if not hasattr(self, "_catalog_watcher"):
            return
        watched = self._catalog_watcher.files() + self._catalog_watcher.directories()
        if watched:
            self._catalog_watcher.removePaths(watched)
        path = self._uploaded_material_catalog_path()
        if path is None:
            return
        watch_paths = [str(path.parent)]
        if path.is_file():
            watch_paths.insert(0, str(path))
        self._catalog_watcher.addPaths(watch_paths)

    def _schedule_material_catalog_refresh(self, *_ignored) -> None:
        if hasattr(self, "_catalog_refresh_timer"):
            self._catalog_refresh_timer.start()

    def _refresh_uploaded_material_catalog_if_changed(self) -> bool:
        """Apply a valid changed XLSX revision and rebuild material controls."""
        path = self._uploaded_material_catalog_path()
        try:
            if path is None or not path.is_file():
                return False
            revision = catalog_revision(path)
            stored_revision = str(repositories.get_setting("material_catalog_revision", "") or "")
            if revision == stored_revision:
                return False
            entries = read_material_catalog(path)
            if not entries:
                raise ValueError("Arkusz nie zawiera dostępnych płyt z ceną za m².")
        except Exception as exc:
            _logger.warning("Nie udało się odświeżyć cennika materiałów: %s", exc)
            return False

        self._material_catalog = entries
        repositories.set_setting("material_catalog", [item.to_dict() for item in entries])
        repositories.set_setting("material_catalog_revision", revision)
        self._populate_material_selector()
        self._watch_uploaded_material_catalog()
        self.statusBar().showMessage(f"Cennik XLSX odświeżony: {len(entries)} pozycji płytowych.", 6000)
        return True

    def _populate_material_selector(self) -> None:
        if not hasattr(self, "material_selector"):
            return
        previous = str(self.material_selector.currentData() or "")
        previous_text = self.material_selector.currentText().strip()
        self.material_selector.blockSignals(True)
        self.material_selector.clear()
        families: dict[str, MaterialCatalogEntry] = {}
        for item in self._material_catalog:
            family = catalog_family_label(item)
            families.setdefault(family, item)
        for family, item in sorted(families.items(), key=lambda pair: pair[0].casefold()):
            self.material_selector.addItem(family, family)
            row = self.material_selector.count() - 1
            self.material_selector.setItemData(row, family, Qt.ItemDataRole.ToolTipRole)
        index = self.material_selector.findData(previous)
        if index >= 0:
            self.material_selector.setCurrentIndex(index)
        elif self.material_selector.count() > 0 and not previous_text:
            # A real catalogue must always provide a deterministic material
            # context.  Starting at ``-1`` left the first part unassigned and
            # later thickness edits could no longer be linked to a board.
            default_thickness = float(getattr(self, "_last_thickness", 18.0) or 18.0)
            compatible_family = next(
                (
                    catalog_family_label(entry)
                    for entry in self._material_catalog
                    if abs(entry.thickness - default_thickness) < 0.001
                ),
                "",
            )
            compatible_index = self.material_selector.findData(compatible_family)
            self.material_selector.setCurrentIndex(compatible_index if compatible_index >= 0 else 0)
        else:
            self.material_selector.setCurrentIndex(-1)
            if previous_text:
                self.material_selector.setEditText(previous_text)
            else:
                self.material_selector.lineEdit().clear()
        self._material_completer.setModel(self.material_selector.model())
        self.material_selector.blockSignals(False)
        self._refresh_catalog_thicknesses()

    def _select_catalog_material_from_completion(self, material: str) -> None:
        index = self.material_selector.findData(material)
        if index < 0:
            index = self.material_selector.findText(material, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            self.material_selector.setCurrentIndex(index)

    def _commit_typed_catalog_material(self) -> None:
        typed = self.material_selector.currentText().strip()
        if not typed:
            return
        exact = next(
            (catalog_family_label(item) for item in self._material_catalog if catalog_family_label(item).casefold() == typed.casefold()),
            "",
        )
        if exact:
            self._select_catalog_material_from_completion(exact)

    def _refresh_catalog_thicknesses(self, *_ignored) -> None:
        if not hasattr(self, "catalog_thickness_selector"):
            return
        material = str(self.material_selector.currentData() or "")
        current = self.catalog_thickness_selector.currentData()
        current_thickness = current.thickness if isinstance(current, MaterialCatalogEntry) else current
        entries = sorted(
            (item for item in self._material_catalog if catalog_family_label(item) == material),
            key=lambda item: (item.thickness, item.product_name.casefold(), item.gross_price_m2),
        )
        self.catalog_thickness_selector.blockSignals(True)
        self.catalog_thickness_selector.clear()
        if not entries:
            self.catalog_thickness_selector.addItem(f"{self._last_thickness:g} mm", None)
        else:
            by_thickness: dict[float, list[MaterialCatalogEntry]] = {}
            for entry in entries:
                by_thickness.setdefault(entry.thickness, []).append(entry)
            for thickness, variants in sorted(by_thickness.items()):
                prices = [entry.gross_price_m2 for entry in variants if entry.gross_price_m2 > 0]
                formats = {(entry.width, entry.height) for entry in variants if entry.width > 0 and entry.height > 0}
                price_label = f"od {min(prices):.2f} zł/m²" if prices else "cena w formacie"
                suffix = f" · {len(formats)} formaty" if len(formats) > 1 else ""
                self.catalog_thickness_selector.addItem(f"{thickness:g} mm · {price_label}{suffix}", thickness)
                row = self.catalog_thickness_selector.count() - 1
                self.catalog_thickness_selector.setItemData(
                    row,
                    "Wybierz format płyty, aby zastosować dokładną cenę za m².",
                    Qt.ItemDataRole.ToolTipRole,
                )
        index = self.catalog_thickness_selector.findData(current_thickness)
        self.catalog_thickness_selector.setCurrentIndex(index if index >= 0 else 0)
        selected_thickness = safeNumber(self.catalog_thickness_selector.currentData())
        if selected_thickness is not None and selected_thickness > 0:
            self._last_thickness = float(selected_thickness)
        self.catalog_thickness_selector.blockSignals(False)
        self._thickness_completer.setModel(self.catalog_thickness_selector.model())
        self._apply_catalog_selection()
        self._refresh_recent_sheet_formats()

    def _selected_catalog_entry(self) -> MaterialCatalogEntry | None:
        selected = self.catalog_thickness_selector.currentData()
        if isinstance(selected, MaterialCatalogEntry):
            return selected
        try:
            thickness = float(selected)
        except (TypeError, ValueError):
            return None
        return self._default_catalog_format(self._selected_material_name(thickness), thickness)

    def _selected_material_name(self, thickness: float) -> str:
        material = str(self.material_selector.currentData() or "").strip()
        if not material:
            typed = self.material_selector.currentText().strip()
            material = typed
        return material

    def _catalog_entries_for(self, material: str) -> list[MaterialCatalogEntry]:
        material = material.strip().casefold()
        return [entry for entry in self._material_catalog if catalog_family_label(entry).casefold() == material]

    def _default_catalog_format(self, material: str, thickness: float) -> MaterialCatalogEntry | None:
        entries = [
            entry for entry in self._catalog_entries_for(material)
            if abs(entry.thickness - thickness) < 0.001 and entry.width > 0 and entry.height > 0
        ]
        # 1000 × 2000 is the production default whenever that exact format is
        # priced for the selected variant.  Falling back to the smallest width
        # previously selected narrow 620 × 2000 stock for POM-C.
        return min(
            entries,
            key=lambda entry: (
                0 if {round(entry.width, 3), round(entry.height, 3)} == {1000.0, 2000.0} else 1,
                entry.width * entry.height,
                entry.width,
                entry.height,
                entry.gross_price_m2,
            ),
            default=None,
        )

    @staticmethod
    def _default_sheet_preset_for_material(material: str) -> tuple[float, float] | None:
        normalized = str(material or "").upper().replace("-", " ")
        tokens = normalized.split()
        if "PA6" in normalized or "POM" in normalized or ("PE" in normalized and "1000" in normalized):
            return 1000.0, 2000.0
        return 1000.0, 2000.0

    def _apply_catalog_selection(self, *_ignored) -> None:
        if not hasattr(self, "stock_table"):
            return
        entry = self._selected_catalog_entry()
        try:
            thickness = float(self.catalog_thickness_selector.currentText().split()[0].replace(",", "."))
        except (TypeError, ValueError, IndexError):
            if entry is None:
                return
            thickness = entry.thickness
        if entry is not None and abs(entry.thickness - thickness) >= 0.001:
            entry = None
        self._last_thickness = thickness
        material = self._selected_material_name(thickness)
        # The selector edits the current board: always the last table row.
        # This also lets the user correct the initial, already completed row
        # without creating and then deleting another board.
        last_row = self.stock_table.rowCount() - 1
        if last_row >= 0:
            self.stock_table.blockSignals(True)
            try:
                item = self.stock_table.item(last_row, STOCK_THICKNESS_COLUMN)
                if item is None:
                    item = self._stock_item("")
                    self.stock_table.setItem(last_row, STOCK_THICKNESS_COLUMN, item)
                item.setText(f"{thickness:g}")
                material_item = self.stock_table.item(last_row, STOCK_MATERIAL_COLUMN)
                if material_item is None:
                    material_item = self._stock_item("")
                    self.stock_table.setItem(last_row, STOCK_MATERIAL_COLUMN, material_item)
                material_item.setText(material)
                material_item.setData(Qt.ItemDataRole.UserRole, False)
                self._set_material_badge(self.stock_table, last_row, STOCK_MATERIAL_COLUMN, material, editable=False)
                preset = self._default_sheet_preset_for_material(material)
                current_width = self._stock_cell_text(last_row, STOCK_HEIGHT_COLUMN).strip()
                current_height = self._stock_cell_text(last_row, STOCK_WIDTH_COLUMN).strip()
                if preset and (
                    not current_width
                    or not current_height
                    or {current_width, current_height} == {"1000", "2000"}
                ):
                    self._set_stock_cell_text(last_row, STOCK_HEIGHT_COLUMN, preset[0])
                    self._set_stock_cell_text(last_row, STOCK_WIDTH_COLUMN, preset[1])
                if entry is not None and entry.width > 0 and entry.height > 0:
                    self._set_stock_cell_text(last_row, STOCK_HEIGHT_COLUMN, entry.width)
                    self._set_stock_cell_text(last_row, STOCK_WIDTH_COLUMN, entry.height)
                self._set_stock_format_selector(last_row)
            finally:
                self.stock_table.blockSignals(False)
        # A draft part row follows the active board context automatically.
        # Existing parts remain untouched, so one order can contain 5 mm and
        # 8 mm parts without accidental rewrites.
        if hasattr(self, "parts") and self.parts.rowCount() > 0:
            draft_row = self.parts.rowCount() - 1
            width_item = self.parts.item(draft_row, PART_WIDTH_COLUMN)
            height_item = self.parts.item(draft_row, PART_HEIGHT_COLUMN)
            is_draft = not (width_item.text().strip() if width_item else "") and not (
                height_item.text().strip() if height_item else ""
            )
            if is_draft:
                self.parts.blockSignals(True)
                try:
                    part_thickness = self.parts.item(draft_row, PART_THICKNESS_COLUMN)
                    if part_thickness is None:
                        part_thickness = QTableWidgetItem()
                        self.parts.setItem(draft_row, PART_THICKNESS_COLUMN, part_thickness)
                    part_thickness.setText(f"{thickness:g}")
                    part_material = self.parts.item(draft_row, PART_MATERIAL_COLUMN)
                    if part_material is None:
                        part_material = QTableWidgetItem()
                        self.parts.setItem(draft_row, PART_MATERIAL_COLUMN, part_material)
                    part_material.setText(material)
                    self._set_material_badge(self.parts, draft_row, PART_MATERIAL_COLUMN, material)
                finally:
                    self.parts.blockSignals(False)
        if entry is not None:
            repositories.set_setting(f"price_per_m2_{material}", entry.gross_price_m2)
            repositories.set_setting(
                f"price_per_m2_{material}_{entry.thickness:g}",
                entry.gross_price_m2,
            )

    def _import_material_catalog(self, *_ignored) -> None:
        last_path = str(repositories.get_setting("material_catalog_path", ""))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wczytaj cennik materiałów",
            last_path,
            "Arkusze Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            entries = read_material_catalog(path)
        except Exception as exc:
            QMessageBox.critical(self, "Nie udało się wczytać cennika", str(exc))
            return
        if not entries:
            QMessageBox.warning(
                self,
                "Brak płyt w cenniku",
                "Nie znaleziono pozycji z jednostką m2, nazwą PŁYTA i grubością GR. ... MM.",
            )
            return
        self._material_catalog = entries
        repositories.set_setting("material_catalog", [item.to_dict() for item in entries])
        repositories.set_setting("material_catalog_path", path)
        repositories.set_setting("material_catalog_source", "uploaded")
        repositories.set_setting("material_catalog_revision", catalog_revision(path))
        self._populate_material_selector()
        self._watch_uploaded_material_catalog()
        family_count = len({catalog_family_label(item) for item in entries})
        QMessageBox.information(
            self,
            "Cennik zaktualizowany",
            "Cennik zaktualizowano pomyślnie.",
        )

    def _parameter_row(self, icon: str, label: str, editor: QWidget, unit: str) -> QWidget:
        row = QWidget()
        row.setObjectName("parameterRow")

        icon_label = QLabel(icon)
        icon_label.setObjectName("parameterIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_label = QLabel(label)
        text_label.setObjectName("parameterLabel")

        unit_label = QLabel(unit)
        unit_label.setObjectName("unitLabel")
        unit_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(icon_label)
        layout.addWidget(text_label, 1)
        layout.addWidget(editor, 1)
        layout.addWidget(unit_label)
        return row

    def _recent_sheet_format_values(self) -> list[dict[str, float]]:
        raw = repositories.get_setting("recent_sheet_formats", [])
        if not isinstance(raw, list):
            return []
        values: list[dict[str, float]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                width = float(item.get("width", 0))
                height = float(item.get("height", 0))
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                signature = (round(width, 2), round(height, 2))
                if signature not in {(round(w, 2), round(h, 2)) for w, h in FIXED_SHEET_PRESETS}:
                    values.append({"width": width, "height": height})
        return values[:8]

    def _catalog_sheet_format_presets(self) -> list[MaterialCatalogEntry]:
        """Return only priced formats for the current material/thickness."""
        if not getattr(self, "_material_catalog", None):
            return []
        material = ""
        thickness: float | None = None
        if hasattr(self, "material_selector"):
            material = str(self.material_selector.currentData() or "").strip()
        if hasattr(self, "catalog_thickness_selector"):
            selected = self.catalog_thickness_selector.currentData()
            if isinstance(selected, MaterialCatalogEntry):
                thickness = selected.thickness
            else:
                thickness = safeNumber(selected)
        if not material or thickness is None or thickness <= 0:
            active_material, active_thickness = self._active_part_context()
            material = material or active_material
            thickness = thickness if thickness and thickness > 0 else active_thickness
        if not material or thickness is None or thickness <= 0:
            return []
        by_format: dict[tuple[float, float], MaterialCatalogEntry] = {}
        for entry in self._catalog_entries_for(material):
            if abs(entry.thickness - float(thickness)) >= 0.001 or entry.width <= 0 or entry.height <= 0:
                continue
            key = (float(entry.width), float(entry.height))
            previous = by_format.get(key)
            if previous is None or entry.gross_price_m2 < previous.gross_price_m2:
                by_format[key] = entry
        return sorted(by_format.values(), key=lambda entry: (entry.width, entry.height, entry.gross_price_m2))

    def _refresh_recent_sheet_formats(self) -> None:
        if not hasattr(self, "recent_sheet_formats"):
            return
        self.recent_sheet_formats.blockSignals(True)
        self.recent_sheet_formats.clear()
        self.recent_sheet_formats.addItem("Wybierz / dodaj format", "")
        catalog_formats = self._catalog_sheet_format_presets()
        if catalog_formats:
            for entry in catalog_formats:
                self.recent_sheet_formats.addItem(
                    f"{entry.width:.0f} x {entry.height:.0f} mm · {entry.gross_price_m2:.2f} zł/m²",
                    f"{entry.width}|{entry.height}",
                )
        elif not getattr(self, "_material_catalog", None):
            # Generic projects have no supplier pricing, so retain the manual
            # production presets and recently used formats.
            for width, height in FIXED_SHEET_PRESETS:
                self.recent_sheet_formats.addItem(f"{width:.0f} x {height:.0f} mm · preset", f"{width}|{height}")
            for item in self._recent_sheet_format_values():
                width = item["width"]
                height = item["height"]
                self.recent_sheet_formats.addItem(f"{width:.0f} x {height:.0f} mm", f"{width}|{height}")
        self.recent_sheet_formats.blockSignals(False)

    def _remember_sheet_formats(self, stocks: list[SheetStock]) -> None:
        current = self._recent_sheet_format_values()
        normalized = current[:]
        fixed = {(round(w, 2), round(h, 2)) for w, h in FIXED_SHEET_PRESETS}
        for stock in reversed(stocks):
            width = float(stock.nominal_width or stock.width)
            height = float(stock.nominal_height or stock.height)
            if width <= 0 or height <= 0 or (round(width, 2), round(height, 2)) in fixed:
                continue
            normalized = [
                item
                for item in normalized
                if round(float(item["width"]), 2) != round(width, 2)
                or round(float(item["height"]), 2) != round(height, 2)
            ]
            normalized.insert(0, {"width": width, "height": height})
        repositories.set_setting("recent_sheet_formats", normalized[:20])
        self._refresh_recent_sheet_formats()

    def _apply_recent_sheet_format(self, *_ignored) -> None:
        data = str(self.recent_sheet_formats.currentData() or "")
        if not data or "|" not in data:
            return
        try:
            width_text, height_text = data.split("|", 1)
            width = float(width_text)
            height = float(height_text)
        except ValueError:
            return
        self._add_or_replace_blank_stock_row(width, height)
        self.recent_sheet_formats.setCurrentIndex(0)

    def _stock_item(self, value: object = "") -> QTableWidgetItem:
        item = QTableWidgetItem(_format_table_number(value))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _material_badge_choices(self, table: QTableWidget, current_material: str) -> list[str]:
        choices = {catalog_family_label(entry) for entry in self._material_catalog}
        if current_material.strip() and current_material.strip() != "-":
            choices.add(current_material.strip())
        return sorted(choices, key=str.casefold)

    def _set_row_material(
        self,
        table: QTableWidget,
        row: int,
        column: int,
        material: str,
        *,
        sync_stock: bool = True,
    ) -> None:
        if row < 0 or row >= table.rowCount():
            return
        item = table.item(row, column)
        if item is None:
            from PySide6.QtWidgets import QTableWidgetItem
            item = QTableWidgetItem()
            from PySide6.QtCore import Qt
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)
        item.setText(material)
        from PySide6.QtCore import Qt
        item.setData(Qt.ItemDataRole.UserRole, False)
        self._set_material_badge(table, row, column, material, editable=table is self.parts)
        if hasattr(self, "parts") and table is self.parts:
            if sync_stock:
                self._sync_stock_with_parts()
            else:
                self._refresh_linked_pair_glows()
            if hasattr(self, "_record_parts_state"):
                self._record_parts_state()

    @staticmethod
    def _linked_pair_key(material: object, thickness: object) -> tuple[str, float] | None:
        """Return the exact material/thickness identity shared by both tables."""
        material_text = str(material or "").strip()
        thickness_value = safeNumber(thickness)
        if not material_text or material_text == "-" or thickness_value is None or thickness_value <= 0:
            return None
        return material_text.casefold(), round(float(thickness_value), 4)

    @staticmethod
    def _linked_pair_color(key: tuple[str, float]) -> QColor:
        """Choose a stable muted accent without Python's randomized hash()."""
        palette = (
            "#60a5fa",
            "#34d399",
            "#f59e0b",
            "#a78bfa",
            "#f472b6",
            "#22d3ee",
            "#fb7185",
            "#a3e635",
        )
        token = f"{key[0]}|{key[1]:g}".encode("utf-8")
        return QColor(palette[zlib.crc32(token) % len(palette)])

    def _apply_link_background(self, item: QTableWidgetItem | None) -> None:
        if item is None:
            return
        if bool(item.data(CELL_ERROR_ROLE)):
            item.setBackground(QBrush(QColor(127, 29, 29, 96)))
            return
        color_name = str(item.data(LINK_GLOW_ROLE) or "")
        if not color_name:
            item.setBackground(QBrush())
            return
        color = QColor(color_name)
        color.setAlpha(14 if self.current_theme() == "dark" else 20)
        item.setBackground(QBrush(color))

    def _refresh_linked_pair_glows(self) -> None:
        """Tint only material/thickness pairs present in parts and stock."""
        if not hasattr(self, "parts") or not hasattr(self, "stock_table"):
            return

        part_keys: set[tuple[str, float]] = set()
        for row in range(self.parts.rowCount()):
            height = self.parts.item(row, PART_HEIGHT_COLUMN)
            width = self.parts.item(row, PART_WIDTH_COLUMN)
            if not ((height and height.text().strip()) or (width and width.text().strip())):
                continue
            material = self.parts.item(row, PART_MATERIAL_COLUMN)
            thickness = self.parts.item(row, PART_THICKNESS_COLUMN)
            key = self._linked_pair_key(
                material.text() if material else "",
                thickness.text() if thickness else "",
            )
            if key is not None:
                part_keys.add(key)

        stock_keys: set[tuple[str, float]] = set()
        for row in range(self.stock_table.rowCount()):
            key = self._linked_pair_key(
                self._stock_cell_text(row, STOCK_MATERIAL_COLUMN),
                self._stock_cell_text(row, STOCK_THICKNESS_COLUMN),
            )
            if key is not None:
                stock_keys.add(key)
        linked_keys = part_keys & stock_keys

        tables = (
            (self.parts, PART_MATERIAL_COLUMN, PART_THICKNESS_COLUMN),
            (self.stock_table, STOCK_MATERIAL_COLUMN, STOCK_THICKNESS_COLUMN),
        )
        previous_signal_states = [table.signalsBlocked() for table, _, _ in tables]
        try:
            for table, _, _ in tables:
                table.blockSignals(True)
            for table, material_column, thickness_column in tables:
                for row in range(table.rowCount()):
                    material_item = table.item(row, material_column)
                    thickness_item = table.item(row, thickness_column)
                    key = self._linked_pair_key(
                        material_item.text() if material_item else "",
                        thickness_item.text() if thickness_item else "",
                    )
                    if table is self.parts:
                        height = table.item(row, PART_HEIGHT_COLUMN)
                        width = table.item(row, PART_WIDTH_COLUMN)
                        if not ((height and height.text().strip()) or (width and width.text().strip())):
                            key = None
                    color = self._linked_pair_color(key) if key in linked_keys else None
                    color_name = color.name() if color is not None else None
                    for column in range(table.columnCount()):
                        item = table.item(row, column)
                        if item is None:
                            continue
                        item.setData(LINK_GLOW_ROLE, color_name)
                        self._apply_link_background(item)

                    badge = table.cellWidget(row, material_column)
                    if badge is not None:
                        base_style = badge.property("linkedPairBaseStyle")
                        if not isinstance(base_style, str):
                            base_style = badge.styleSheet()
                            badge.setProperty("linkedPairBaseStyle", base_style)
                        if color is None:
                            badge.setStyleSheet(base_style)
                        else:
                            neutral_border = "border: 1px solid rgba(255, 255, 255, 0.18);"
                            linked_border = (
                                "border: 1px solid "
                                f"rgba({color.red()}, {color.green()}, {color.blue()}, 0.62);"
                            )
                            badge.setStyleSheet(
                                "/* linked-pair */\n" + base_style.replace(neutral_border, linked_border)
                            )
        finally:
            for (table, _, _), blocked in zip(tables, previous_signal_states):
                table.blockSignals(blocked)
        self.parts.viewport().update()
        self.stock_table.viewport().update()

    def _active_part_context(self) -> tuple[str, float]:
        """Return the latest usable part material/thickness without touching older boards."""
        if not hasattr(self, "parts"):
            return "", float(getattr(self, "_last_thickness", 18.0) or 18.0)
        selected_rows = sorted({index.row() for index in self.parts.selectedIndexes()})
        rows = list(reversed(selected_rows)) + list(range(self.parts.rowCount() - 1, -1, -1))
        seen: set[int] = set()
        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            material_item = self.parts.item(row, PART_MATERIAL_COLUMN)
            thickness_item = self.parts.item(row, PART_THICKNESS_COLUMN)
            material = material_item.text().strip() if material_item else ""
            try:
                thickness = float((thickness_item.text() if thickness_item else "").replace(",", "."))
            except ValueError:
                continue
            if material and material != "-" and thickness > 0:
                return material, thickness
        return "", float(getattr(self, "_last_thickness", 18.0) or 18.0)

    def _material_supports_thickness(self, material: str, thickness: float) -> bool:
        """Whether *material* can safely be used for this catalogue thickness."""
        material = str(material or "").strip()
        if not material or material == "-":
            return False
        if not self._material_catalog:
            return True
        entries = self._catalog_entries_for(material)
        # Manually typed materials which are outside the supplier catalogue
        # remain valid; catalogue families, however, must use a priced variant.
        return not entries or any(abs(entry.thickness - thickness) < 0.001 for entry in entries)

    def _infer_part_material(self, row: int, thickness: float) -> str:
        """Resolve an omitted material deterministically, never from a random board."""
        # The nearest previous part is the strongest context for fast data
        # entry: new rows normally continue the same material family.
        for candidate_row in range(min(row - 1, self.parts.rowCount() - 1), -1, -1):
            item = self.parts.item(candidate_row, PART_MATERIAL_COLUMN)
            material = item.text().strip() if item else ""
            if self._material_supports_thickness(material, thickness):
                return material

        selected = self._selected_material_name(thickness)
        if self._material_supports_thickness(selected, thickness):
            return selected

        families = sorted(
            {
                catalog_family_label(entry)
                for entry in self._material_catalog
                if abs(entry.thickness - thickness) < 0.001
            },
            key=str.casefold,
        )
        return families[0] if len(families) == 1 else ""

    def _set_missing_part_material_state(self, row: int, missing: bool) -> None:
        item = self.parts.item(row, PART_MATERIAL_COLUMN)
        if item is not None:
            item.setData(CELL_ERROR_ROLE, missing)
            self._apply_link_background(item)
            item.setToolTip("Najpierw wybierz materiał formatki." if missing else "")
        badge = self.parts.cellWidget(row, PART_MATERIAL_COLUMN)
        if isinstance(badge, QToolButton):
            if missing:
                badge.setToolTip("Najpierw wybierz materiał. Bez niego formatka nie zostanie połączona z płytą.")
                badge.setStyleSheet(
                    "QToolButton { background: rgba(127,29,29,0.72); color: #fee2e2;"
                    " border: 1px solid #ef4444; border-radius: 7px;"
                    " font-size: 10px; font-weight: 700; padding: 3px 5px; }"
                )

    def _ensure_part_material(self, row: int, thickness: float) -> str:
        item = self.parts.item(row, PART_MATERIAL_COLUMN)
        material = item.text().strip() if item else ""
        if material and material != "-":
            self._set_missing_part_material_state(row, False)
            return material
        inferred = self._infer_part_material(row, thickness)
        if inferred:
            self.parts.blockSignals(True)
            try:
                if item is None:
                    item = QTableWidgetItem()
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.parts.setItem(row, PART_MATERIAL_COLUMN, item)
                item.setText(inferred)
                self._set_material_badge(self.parts, row, PART_MATERIAL_COLUMN, inferred)
            finally:
                self.parts.blockSignals(False)
            return inferred
        self._set_missing_part_material_state(row, True)
        return ""

    def _choose_part_thickness(self, row: int, material: str, source_menu: QMenu | None = None) -> None:
        """Material is selected first; immediately offer its available thicknesses."""
        if source_menu is not None:
            source_menu.hide()
        # Commit the material immediately so the row and its generated board
        # stay visibly linked while the thickness menu is open.
        self._set_row_material(
            self.parts,
            row,
            PART_MATERIAL_COLUMN,
            material,
            sync_stock=False,
        )
        entries = self._catalog_entries_for(material)
        choices = sorted({float(entry.thickness) for entry in entries if entry.thickness > 0})
        if not choices:
            QTimer.singleShot(0, lambda: self._prompt_part_thickness(row, material))
            return
        current_item = self.parts.item(row, PART_THICKNESS_COLUMN)
        current_value = safeNumber(current_item.text() if current_item else "")
        current_is_valid = current_value is not None and any(
            abs(current_value - value) < 0.001 for value in choices
        )
        if current_is_valid:
            # The old numeric value is also a priced thickness of the newly
            # selected material, so the pair is already complete and safe.
            self._sync_stock_with_parts()
        else:
            self.parts.blockSignals(True)
            try:
                if current_item is None:
                    current_item = QTableWidgetItem()
                    self.parts.setItem(row, PART_THICKNESS_COLUMN, current_item)
                current_item.setText("")
            finally:
                self.parts.blockSignals(False)
        chooser = QMenu(self)
        chooser.setTitle(f"{material} — wybierz grubość")
        for thickness in choices:
            action = chooser.addAction(f"{thickness:g} mm")
            action.triggered.connect(
                lambda _checked=False, value=thickness: self._set_part_material_thickness(row, material, value)
            )
        if not entries:
            chooser.addSeparator()
            custom_action = chooser.addAction("Własna grubość...")
            custom_action.triggered.connect(
                lambda _checked=False: self._prompt_part_thickness(row, material)
            )
        thickness_index = self.parts.model().index(row, PART_THICKNESS_COLUMN)
        thickness_rect = self.parts.visualRect(thickness_index)
        position = (
            self.parts.viewport().mapToGlobal(thickness_rect.bottomLeft())
            if thickness_rect.isValid()
            else self.mapToGlobal(self.rect().center())
        )
        # popup() keeps the main event loop responsive; exec() would turn a
        # simple material click into a nested blocking loop.
        self._part_thickness_menu = chooser
        chooser.aboutToHide.connect(chooser.deleteLater)
        QTimer.singleShot(0, lambda: chooser.popup(position))

    def _prompt_part_thickness(self, row: int, material: str) -> None:
        """Apply a manual thickness directly from the first material menu."""
        current_item = self.parts.item(row, PART_THICKNESS_COLUMN)
        current = _number(current_item.text() if current_item else "", 1.0)
        value, accepted = QInputDialog.getDouble(
            self,
            "Własna grubość",
            f"Grubość dla {material} [mm]:",
            max(0.01, current),
            0.01,
            1000.0,
            3,
        )
        if accepted:
            self._set_part_material_thickness(row, material, value)

    def _set_part_material_thickness(self, row: int, material: str, thickness: float) -> None:
        if row < 0 or row >= self.parts.rowCount():
            return
        self.parts.blockSignals(True)
        try:
            thickness_item = self.parts.item(row, PART_THICKNESS_COLUMN)
            if thickness_item is None:
                thickness_item = QTableWidgetItem()
                self.parts.setItem(row, PART_THICKNESS_COLUMN, thickness_item)
            thickness_item.setText(f"{thickness:g}")
            self._set_row_material(self.parts, row, PART_MATERIAL_COLUMN, material)
        finally:
            self.parts.blockSignals(False)
        self._last_thickness = thickness
        self._sync_stock_with_parts()
        self._record_parts_state()
        QTimer.singleShot(0, lambda row=row: self._focus_part_cell(row, PART_HEIGHT_COLUMN))

    def _set_material_badge(
        self, table: QTableWidget, row: int, column: int, material: str, *, editable: bool | None = None
    ) -> None:
        if editable is None:
            editable = table is getattr(self, "parts", None)
        label, background, foreground = _material_badge_spec(material)
        badge = QToolButton()
        badge.setText(label)
        badge.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        badge.setToolTip(material or "Materiał nie został wybrany.")
        badge.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        if not editable:
            badge.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
            badge.setEnabled(False)
            badge.setToolTip(material or "Materiał zostanie przypisany z formatki.")
            badge.setStyleSheet(
                "QToolButton {"
                f" background: {background}; color: {foreground};"
                " border: 1px solid rgba(255, 255, 255, 0.18); border-radius: 7px;"
                " font-size: 10px; font-weight: 700; padding: 3px 5px; text-align: center;"
                "}"
            )
            table.setCellWidget(row, column, badge)
            return
        menu = QMenu(badge)
        from PySide6.QtGui import QActionGroup
        group = QActionGroup(menu)
        group.setExclusive(True)
        if not self._material_catalog:
            empty_action = menu.addAction("-  Brak przypisania")
            empty_action.setCheckable(True)
            empty_action.setActionGroup(group)
            empty_action.setChecked(not material.strip() or material.strip() == "-")
            empty_action.triggered.connect(lambda _checked=False, m=menu: (m.hide(), self._set_row_material(table, row, column, "")))
        choices = self._material_badge_choices(table, material)
        if choices:
            menu.addSeparator()
        search = QLineEdit()
        search.setPlaceholderText("Szukaj materiału")
        search_action = QWidgetAction(menu)
        search_action.setDefaultWidget(search)
        menu.addAction(search_action)
        material_actions = []
        for choice in choices:
            # The compact badge already shows PA6G / POM-C / PP in the table.
            # Repeating that code in front of the full family produced labels
            # such as "PA6G PA6G PŁYTA" and "PE PEEK".
            action = menu.addAction(choice)
            action.setCheckable(True)
            action.setActionGroup(group)
            action.setChecked(choice.casefold() == material.strip().casefold())
            if table is getattr(self, "parts", None):
                action.triggered.connect(
                    lambda _checked=False, value=choice, m=menu: self._choose_part_thickness(row, value, m)
                )
            else:
                action.triggered.connect(
                    lambda _checked=False, value=choice, m=menu: (m.hide(), self._set_row_material(table, row, column, value))
                )
            material_actions.append(action)
        def filter_choices(query: str) -> None:
            needle = query.strip().casefold()
            for action in material_actions:
                action.setVisible(not needle or needle in action.text().casefold())
        search.textChanged.connect(filter_choices)
        menu.aboutToShow.connect(search.setFocus)
        badge.setMenu(menu)
        badge.setStyleSheet(
            "QToolButton {"
            f" background: {background}; color: {foreground};"
            " border: 1px solid rgba(255, 255, 255, 0.18); border-radius: 7px;"
            " font-size: 10px; font-weight: 700; padding: 3px 5px; text-align: center;"
            "}"
        )
        table.setCellWidget(row, column, badge)

    def _stock_catalog_formats(self, material: str, thickness: float) -> list[tuple[float, float]]:
        formats: list[tuple[float, float]] = []
        for entry in self._stock_catalog_entries(material, thickness):
            candidate = (float(entry.width), float(entry.height))
            if candidate not in formats:
                formats.append(candidate)
        return formats

    def _stock_catalog_entries(self, material: str, thickness: float) -> list[MaterialCatalogEntry]:
        needle = material.strip().casefold()
        entries: list[MaterialCatalogEntry] = []
        for entry in self._material_catalog:
            matches = {
                catalog_family_label(entry).casefold(),
                str(entry.material or "").strip().casefold(),
                str(entry.product_name or "").strip().casefold(),
            }
            if needle not in matches or abs(float(entry.thickness) - thickness) >= 0.001:
                continue
            if entry.width > 0 and entry.height > 0:
                entries.append(entry)
        return sorted(entries, key=lambda entry: (entry.width, entry.height, entry.gross_price_m2))

    def _catalog_entry_for_stock(
        self,
        material: str,
        thickness: float,
        width: float,
        height: float,
    ) -> MaterialCatalogEntry | None:
        for entry in self._stock_catalog_entries(material, thickness):
            if (abs(entry.width - width) < 0.001 and abs(entry.height - height) < 0.001) or (
                abs(entry.width - height) < 0.001 and abs(entry.height - width) < 0.001
            ):
                return entry
        return None

    def _set_stock_format_selector(self, row: int) -> None:
        if row < 0 or row >= self.stock_table.rowCount():
            return
        material = self._stock_cell_text(row, STOCK_MATERIAL_COLUMN).strip()
        try:
            thickness = float(self._stock_cell_text(row, STOCK_THICKNESS_COLUMN).replace(",", "."))
        except ValueError:
            thickness = 0.0
        try:
            current = (
                float(self._stock_cell_text(row, STOCK_HEIGHT_COLUMN).replace(",", ".")),
                float(self._stock_cell_text(row, STOCK_WIDTH_COLUMN).replace(",", ".")),
            )
        except ValueError:
            current = (0.0, 0.0)
        selector = QComboBox(self.stock_table)
        selector.setObjectName("stockFormatPicker")
        selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        selector.setMinimumContentsLength(0)
        selector.setFixedWidth(self.stock_table.columnWidth(STOCK_FORMAT_COLUMN))
        selector.setToolTip("Wybierz format dostępny dla materiału i grubości")
        selector.view().setMinimumWidth(220)
        selector.addItem("-", None)
        for entry in self._stock_catalog_entries(material, thickness):
            width, height = float(entry.width), float(entry.height)
            selector.addItem(f"{width:g} × {height:g} mm", (width, height))
            selector.setItemData(
                selector.count() - 1,
                f"Cena katalogowa: {entry.gross_price_m2:.2f} zł/m²",
                Qt.ItemDataRole.ToolTipRole,
            )
        matching = next(
            (
                index for index in range(selector.count())
                if selector.itemData(index) == current
            ),
            -1,
        )
        selector.setCurrentIndex(matching if matching >= 0 else 0)

        def apply_format(index: int) -> None:
            selected = selector.itemData(index)
            if not isinstance(selected, tuple) or len(selected) != 2:
                return
            self.stock_table.blockSignals(True)
            try:
                self._set_stock_cell_text(row, STOCK_HEIGHT_COLUMN, selected[0])
                self._set_stock_cell_text(row, STOCK_WIDTH_COLUMN, selected[1])
            finally:
                self.stock_table.blockSignals(False)

        selector.currentIndexChanged.connect(apply_format)
        self.stock_table.setCellWidget(row, STOCK_FORMAT_COLUMN, selector)

    def _set_stock_priority(self, row: int, enabled: bool) -> None:
        item = self.stock_table.item(row, STOCK_PRIORITY_COLUMN)
        if item is None:
            item = self._stock_item("0")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.stock_table.setItem(row, STOCK_PRIORITY_COLUMN, item)
        value = 1 if enabled else 0
        # The item is storage for the priority value; only the dot is visible.
        item.setText("")
        item.setData(Qt.ItemDataRole.UserRole, value)
        button = self.stock_table.cellWidget(row, STOCK_PRIORITY_COLUMN)
        if isinstance(button, QPushButton):
            button.blockSignals(True)
            button.setChecked(bool(value))
            button.blockSignals(False)

    def _stock_stack_limit(self, row: int) -> int:
        quantity = safeNumber(self._stock_cell_text(row, STOCK_QUANTITY_COLUMN), 1) or 1
        return max(1, int(quantity))

    def _set_stock_stack_size(self, row: int, stack_size: int) -> None:
        if row < 0 or row >= self.stock_table.rowCount():
            return
        stack_size = max(1, int(stack_size))
        if stack_size > 999:
            self.statusBar().showMessage("Sztapel może mieć najwyżej 999 płyt.", 4000)
            return
        item = self.stock_table.item(row, STOCK_STACK_COLUMN)
        if item is None:
            item = self._stock_item("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.stock_table.setItem(row, STOCK_STACK_COLUMN, item)
        # The item is storage only. Rendering its text as well as the selector
        # creates the misleading duplicate number visible in the table.
        item.setText("")
        item.setData(Qt.ItemDataRole.UserRole, stack_size)
        self._set_stack_control(row, stack_size)

    def _prompt_stock_stack_size(self, row: int) -> None:
        limit = self._stock_stack_limit(row)
        current = max(1, int(safeNumber(self._stock_cell_text(row, STOCK_STACK_COLUMN), 1) or 1))
        upper_bound = min(999, max(10, limit, current))
        value, accepted = QInputDialog.getInt(
            self,
            "Sztapel płyt",
            "Liczba płyt ciętych jednocześnie:",
            current,
            1,
            upper_bound,
        )
        if accepted:
            self._set_stock_stack_size(row, value)

    def _set_stack_control(self, row: int, stack_size: int) -> None:
        button = QToolButton()
        available = self._stock_stack_limit(row)
        exceeds_available = stack_size > available
        button.setText(f"{stack_size} ▾")
        button.setIcon(_stack_icon("#fca5a5" if exceeds_available else "#b8cdf0"))
        button.setIconSize(QSize(16, 14))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if exceeds_available:
            button.setToolTip(
                f"Sztapel: {stack_size} płyt, wpisano: {available}. "
                "Obliczenie zostanie zablokowane do czasu zwiększenia ilości płyt."
            )
        else:
            button.setToolTip(
                "Pojedyncza płyta" if stack_size == 1 else f"Sztapel: {stack_size} płyt jednocześnie"
            )
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu = QMenu(button)
        menu.setToolTipsVisible(True)
        menu.addSection("Płyty w jednym sztaplu")
        group = QActionGroup(menu)
        group.setExclusive(True)
        quick_values = list(range(1, 11))
        if stack_size not in quick_values:
            quick_values.append(stack_size)

        def stack_label(value: int) -> str:
            if value == 1:
                return "1 płyta"
            if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
                return f"{value} płyty"
            return f"{value} płyt"

        for value in quick_values:
            action = menu.addAction(stack_label(value))
            action.setCheckable(True)
            action.setChecked(value == stack_size)
            group.addAction(action)
            if value > available:
                action.setToolTip(f"Wymaga wpisania co najmniej {value} płyt w kolumnie SZT.")
            action.triggered.connect(
                lambda _checked=False, selected=value: self._set_stock_stack_size(row, selected)
            )
        menu.addSeparator()
        custom_action = menu.addAction("Inna liczba...")
        custom_action.triggered.connect(lambda _checked=False: self._prompt_stock_stack_size(row))
        button.setMenu(menu)
        text_color = "#fca5a5" if exceeds_available else "#c9dcf7"
        hover_color = "rgba(239, 68, 68, 0.18)" if exceeds_available else "rgba(59, 130, 246, 0.18)"
        button.setStyleSheet(
            "QToolButton { background: transparent; color: " + text_color + "; border: 0; "
            "font-size: 11px; font-weight: 700; padding: 2px; text-align: center; }"
            "QToolButton:hover { color: #ffffff; background: " + hover_color + "; border-radius: 5px; }"
        )
        self.stock_table.setCellWidget(row, STOCK_STACK_COLUMN, button)

    # ── Stock-table keyboard navigation (Tab/Enter like the parts table) ─────
    def _editable_stock_columns(self) -> list[int]:
        # Thickness is an editable attribute, but dimensions are entered most
        # often in sequence. Keep Tab focused on width, height and quantity.
        return [col for col in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN, STOCK_QUANTITY_COLUMN) if not self.stock_table.isColumnHidden(col)]

    def _focus_stock_cell(self, row: int, col: int) -> None:
        if row < 0 or row >= self.stock_table.rowCount():
            return
        columns = self._editable_stock_columns()
        if not columns:
            return
        if col not in columns:
            larger = [candidate for candidate in columns if candidate > col]
            col = larger[0] if larger else columns[-1]
        item = self.stock_table.item(row, col)
        if item is None:
            item = self._stock_item("")
            self.stock_table.setItem(row, col, item)
        self.stock_table.clearSelection()
        self.stock_table.setCurrentCell(row, col)
        item.setSelected(True)
        self.stock_table.scrollToItem(item)
        QTimer.singleShot(0, lambda item=item: self.stock_table.editItem(item))

    def _handle_stock_nav_key(self, row: int, col: int, backwards: bool = False) -> None:
        self._normalize_stock_item(row, col)
        columns = self._editable_stock_columns()
        if not columns:
            return

        if backwards:
            previous_cols = [candidate for candidate in columns if candidate < col]
            if previous_cols:
                self._focus_stock_cell(row, previous_cols[-1])
            elif row > 0:
                self._focus_stock_cell(row - 1, columns[-1])
            else:
                self._focus_stock_cell(row, columns[0])
            return

        # Tab / Enter (forward).
        next_cols = [candidate for candidate in columns if candidate > col]
        if next_cols:
            self._focus_stock_cell(row, next_cols[0])
            return

        # On the quantity (last) cell: create a new stock row and jump to it.
        if row >= self.stock_table.rowCount() - 1:
            self._add_stock_row({"width": "", "height": "", "quantity": ""})
        self._focus_stock_cell(row + 1, columns[0])

    def _add_blank_stock_row_and_focus(self) -> None:
        """'Dodaj płytę' — append a blank row and focus it ready for typing."""
        self._add_stock_row({"width": "", "height": "", "quantity": ""})
        columns = self._editable_stock_columns()
        self._focus_stock_cell(self.stock_table.rowCount() - 1, columns[0] if columns else 1)

    def _remove_last_stock_row(self) -> None:
        """Remove the bottom-most non-blank stock row ('Cofnij ostatnią')."""
        last_row = -1
        for row in range(self.stock_table.rowCount() - 1, -1, -1):
            if not self._stock_row_is_blank(row):
                last_row = row
                break
        if last_row == -1:
            self.statusBar().showMessage("Brak płyt do usunięcia", 2000)
            return
        self.stock_table.removeRow(last_row)
        if self.stock_table.rowCount() == 0:
            self._add_stock_row({"width": "", "height": "", "quantity": ""})
        self._refresh_linked_pair_glows()
        self.statusBar().showMessage(f"Usunięto płytę z wiersza {last_row + 1}", 1800)

    def _normalize_stock_item(self, row: int, column: int) -> None:
        if column in (STOCK_FORMAT_COLUMN, STOCK_MATERIAL_COLUMN, STOCK_PRIORITY_COLUMN, STOCK_STACK_COLUMN):
            return
        item = self.stock_table.item(row, column)
        if item is None:
            return
        text = item.text().strip()
        if not text:
            return
        number = safeNumber(text)
        if number is None:
            self._mark_stock_cell(row, column, True)
            return
        if column == 3:
            if not float(number).is_integer():
                self._mark_stock_cell(row, column, True)
                return
            item.setText(str(int(number)))
        else:
            item.setText(f"{number:g}")
        self._mark_stock_cell(row, column, False)

    def _stock_cell_text(self, row: int, column: int) -> str:
        item = self.stock_table.item(row, column)
        if item is not None and column == STOCK_STACK_COLUMN:
            stored = item.data(Qt.ItemDataRole.UserRole)
            if stored is not None:
                return str(stored)
        return item.text() if item else ""

    def _stock_preferred_cut_axis(self, row: int) -> str:
        height_item = self.stock_table.item(row, STOCK_HEIGHT_COLUMN)
        width_item = self.stock_table.item(row, STOCK_WIDTH_COLUMN)
        if height_item is not None and bool(height_item.data(STOCK_CUT_AXIS_ROLE)):
            return "x"
        if width_item is not None and bool(width_item.data(STOCK_CUT_AXIS_ROLE)):
            return "y"
        return "auto"

    def _set_stock_cut_axis(self, row: int, axis: str) -> None:
        if row < 0 or row >= self.stock_table.rowCount():
            return
        normalized = axis if axis in {"x", "y"} else "auto"
        for column, column_axis in (
            (STOCK_HEIGHT_COLUMN, "x"),
            (STOCK_WIDTH_COLUMN, "y"),
        ):
            item = self.stock_table.item(row, column)
            if item is None:
                item = self._stock_item("")
                self.stock_table.setItem(row, column, item)
            active = normalized == column_axis
            item.setData(STOCK_CUT_AXIS_ROLE, active)
            field = "wysokości" if column == STOCK_HEIGHT_COLUMN else "szerokości"
            item.setToolTip(
                f"Kierunek długich cięć: wzdłuż {field} płyty."
                if active
                else f"Kliknij szarą kropkę, aby ciąć wzdłuż {field} płyty."
            )
        self.stock_table.viewport().update()

    def _toggle_stock_cut_axis(self, row: int, column: int) -> None:
        requested = "x" if column == STOCK_HEIGHT_COLUMN else "y"
        current = self._stock_preferred_cut_axis(row)
        self._set_stock_cut_axis(row, "auto" if current == requested else requested)
        if self._stock_preferred_cut_axis(row) == "auto":
            self.statusBar().showMessage("Kierunek cięcia: automatyczny", 2500)
        else:
            value = self._stock_cell_text(row, column).strip()
            self.statusBar().showMessage(
                f"Kierunek cięcia: wzdłuż boku {value or 'wybranego'} mm", 3500
            )

    def _set_stock_cell_text(self, row: int, column: int, value: object) -> None:
        text = _format_table_number(value)
        item = self.stock_table.item(row, column)
        if item is None:
            item = self._stock_item(text)
            self.stock_table.setItem(row, column, item)
        else:
            item.setText(text)
        self._mark_stock_cell(row, column, False)

    def _on_stock_thickness_changed(self, text: str) -> None:
        try:
            val = float(text.replace(",", "."))
            if val > 0:
                self._last_thickness = val
        except ValueError:
            pass

    def _on_stock_item_changed(self, item: QTableWidgetItem) -> None:
        self._mark_stock_cell(item.row(), item.column(), False)
        if item.column() == STOCK_THICKNESS_COLUMN:
            self._on_stock_thickness_changed(item.text())
        elif item.column() == STOCK_MATERIAL_COLUMN:
            item.setData(Qt.ItemDataRole.UserRole, False)
        if item.column() in (STOCK_THICKNESS_COLUMN, STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN, STOCK_MATERIAL_COLUMN):
            self._set_stock_format_selector(item.row())
            self._refresh_linked_pair_glows()

    def _mark_stock_cell(self, row: int, column: int, invalid: bool) -> None:
        item = self.stock_table.item(row, column)
        if item is None:
            return
        signals_were_blocked = self.stock_table.signalsBlocked()
        self.stock_table.blockSignals(True)
        try:
            item.setData(CELL_ERROR_ROLE, invalid)
            if invalid:
                self._apply_link_background(item)
                item.setToolTip("Popraw wartość w tej komórce.")
            else:
                self._apply_link_background(item)
                if column in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN):
                    active = bool(item.data(STOCK_CUT_AXIS_ROLE))
                    field = "wysokości" if column == STOCK_HEIGHT_COLUMN else "szerokości"
                    item.setToolTip(
                        f"Kierunek długich cięć: wzdłuż {field} płyty."
                        if active
                        else f"Kliknij szarą kropkę, aby ciąć wzdłuż {field} płyty."
                    )
                else:
                    item.setToolTip("")
        finally:
            self.stock_table.blockSignals(signals_were_blocked)

    def _add_stock_row(self, values: dict[str, object] | SheetStock | None = None) -> None:
        if isinstance(values, SheetStock):
            values = {
                "material": values.material,
                "thickness": values.thickness,
                "width": values.nominal_width or values.width,
                "height": values.nominal_height or values.height,
                "quantity": values.quantity,
                "stack_size": values.stack_size,
                "priority": values.priority,
                "preferred_cut_axis": values.preferred_cut_axis,
                "allow_rotation": values.allow_rotation,
            }
        context_material, context_thickness = self._active_part_context()
        data = dict(values or {"thickness": context_thickness, "width": 1000, "height": 2000, "quantity": 1})
        thickness = safeNumber(data.get("thickness"), context_thickness) or context_thickness
        material = str(data.get("material") or context_material or self._selected_material_name(thickness)).strip()
        row = self.stock_table.rowCount()
        self.stock_table.insertRow(row)
        for column, key in (
            (STOCK_THICKNESS_COLUMN, "thickness"),
            (STOCK_HEIGHT_COLUMN, "width"),
            (STOCK_WIDTH_COLUMN, "height"),
            (STOCK_QUANTITY_COLUMN, "quantity"),
        ):
            value = thickness if key == "thickness" and key not in data else data.get(key, "")
            item = self._stock_item(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stock_table.setItem(row, column, item)
        material_item = self._stock_item(material)
        material_item.setFlags(material_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        material_item.setData(Qt.ItemDataRole.UserRole, True)
        material_item.setData(STOCK_TEMPLATE_ROLE, bool(data.get("template", False)))
        material_item.setData(
            STOCK_ALLOW_ROTATION_ROLE,
            bool(data.get("allow_rotation", getattr(self, "_algo_settings", {}).get("allow_rotation_stock", True))),
        )
        self.stock_table.setItem(row, STOCK_MATERIAL_COLUMN, material_item)
        self._set_material_badge(self.stock_table, row, STOCK_MATERIAL_COLUMN, material, editable=False)
        stack_size = max(1, int(safeNumber(data.get("stack_size"), 1) or 1))
        stack_item = self._stock_item("")
        stack_item.setFlags(stack_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        stack_item.setData(Qt.ItemDataRole.UserRole, stack_size)

        # Priority is a small state marker: grey by default and green when the
        # board is selected for priority cutting.
        priority_val = max(0, int(safeNumber(data.get("priority", 0)) or 0))
        prio_btn = QPushButton("●")
        prio_btn.setCheckable(True)
        prio_btn.setChecked(priority_val > 0)
        prio_btn.setToolTip("Oznacz jako priorytetową płytę")
        def _update_prio_style(checked):
            color = "#32b77a" if checked else "#73839a"
            hover = "rgba(50, 183, 122, 0.16)" if checked else "rgba(115, 131, 154, 0.16)"
            prio_btn.setStyleSheet(
                "QPushButton {"
                f" background: transparent; color: {color}; font-size: 18px;"
                " border: 0; padding: 0;"
                "}"
                f" QPushButton:hover {{ background: {hover}; border-radius: 5px; }}"
            )
        _update_prio_style(prio_btn.isChecked())
        prio_btn.toggled.connect(_update_prio_style)
        prio_btn.toggled.connect(lambda checked, stock_row=row: self._set_stock_priority(stock_row, checked))
        priority_item = self._stock_item("")
        priority_item.setFlags(priority_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        priority_item.setData(Qt.ItemDataRole.UserRole, priority_val)
        self.stock_table.setItem(row, STOCK_PRIORITY_COLUMN, priority_item)
        self.stock_table.setCellWidget(row, STOCK_PRIORITY_COLUMN, prio_btn)

        self.stock_table.setItem(row, STOCK_STACK_COLUMN, stack_item)
        self._set_stack_control(row, stack_size)
        self._set_stock_cut_axis(row, str(data.get("preferred_cut_axis", "auto") or "auto"))
        self._set_stock_format_selector(row)

    def _stock_row_is_blank(self, row: int) -> bool:
        values = [
            self._stock_cell_text(row, col).strip()
            for col in (STOCK_THICKNESS_COLUMN, STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN, STOCK_QUANTITY_COLUMN)
        ]
        return not any(values)

    def _stock_row_is_incomplete(self, row: int) -> bool:
        """A draft board may receive the material and thickness from the selector."""
        return any(not self._stock_cell_text(row, column).strip() for column in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN, STOCK_QUANTITY_COLUMN))

    def _add_or_replace_blank_stock_row(self, width: float, height: float) -> None:
        if self.stock_table.rowCount() == 0:
            self._add_stock_row({"thickness": "", "width": "", "height": "", "quantity": ""})
        selected_rows = sorted({index.row() for index in self.stock_table.selectedIndexes()})
        row = selected_rows[0] if selected_rows else 0
        quantity = self._stock_cell_text(row, STOCK_QUANTITY_COLUMN).strip() or "1"
        thickness = self._stock_cell_text(row, STOCK_THICKNESS_COLUMN).strip() or str(self._last_thickness)
        self._set_stock_cell_text(row, STOCK_THICKNESS_COLUMN, thickness)
        self._set_stock_cell_text(row, STOCK_HEIGHT_COLUMN, f"{width:.0f}")
        self._set_stock_cell_text(row, STOCK_WIDTH_COLUMN, f"{height:.0f}")
        self._set_stock_cell_text(row, STOCK_QUANTITY_COLUMN, quantity)
        self._set_stock_format_selector(row)
        columns = self._editable_stock_columns()
        if columns:
            self.stock_table.clearSelection()
            self.stock_table.setCurrentCell(row, columns[0])

    def _remove_selected_stock_rows(self) -> None:
        for row in sorted({index.row() for index in self.stock_table.selectedIndexes()}, reverse=True):
            self.stock_table.removeRow(row)
        if self.stock_table.rowCount() == 0:
            self._add_stock_row({"width": "", "height": "", "quantity": ""})
        self._refresh_linked_pair_glows()

    def _collect_stock(self) -> list[SheetStock]:
        stocks: list[SheetStock] = []
        errors: list[str] = []
        for row in range(self.stock_table.rowCount()):
            for column in range(self.stock_table.columnCount()):
                self._mark_stock_cell(row, column, False)
            if self._stock_row_is_blank(row):
                continue
            thickness_text = self._stock_cell_text(row, STOCK_THICKNESS_COLUMN)
            width_text = self._stock_cell_text(row, STOCK_HEIGHT_COLUMN)
            height_text = self._stock_cell_text(row, STOCK_WIDTH_COLUMN)
            qty_text = self._stock_cell_text(row, STOCK_QUANTITY_COLUMN)
            material_text = self._stock_cell_text(row, STOCK_MATERIAL_COLUMN).strip()
            stack_text = self._stock_cell_text(row, STOCK_STACK_COLUMN).strip() or "1"
            if not material_text:
                material_text = "standard"

            try:
                thickness_val = float(thickness_text.replace(",", ".")) if thickness_text else 1.0
            except ValueError:
                thickness_val = 1.0

            stock, row_errors = validatePlate(
                {
                    "material": material_text,
                    "thickness": thickness_val,
                    "width": width_text,
                    "height": height_text,
                    "quantity": qty_text,
                    "stack_size": stack_text,
                    "price": 0,
                    "priority": 1 if (self.stock_table.cellWidget(row, STOCK_PRIORITY_COLUMN) and self.stock_table.cellWidget(row, STOCK_PRIORITY_COLUMN).isChecked()) else 0,
                    "preferred_cut_axis": self._stock_preferred_cut_axis(row),
                    "allow_rotation": bool(
                        self.stock_table.item(row, STOCK_MATERIAL_COLUMN).data(STOCK_ALLOW_ROTATION_ROLE)
                        if self.stock_table.item(row, STOCK_MATERIAL_COLUMN)
                        and self.stock_table.item(row, STOCK_MATERIAL_COLUMN).data(STOCK_ALLOW_ROTATION_ROLE) is not None
                        else getattr(self, "_algo_settings", {}).get("allow_rotation_stock", True)
                    ),
                    "min_offcut_width": 0,
                    "min_offcut_height": 0,
                    "source": "stock",
                },
                row + 1,
            )
            if row_errors:
                errors.extend(row_errors)
                for column in range(self.stock_table.columnCount()):
                    self._mark_stock_cell(row, column, True)
                continue
            if stock:
                catalog_entry = self._catalog_entry_for_stock(
                    material_text,
                    thickness_val,
                    float(stock.width),
                    float(stock.height),
                )
                # Older flat catalogues have a price per material/thickness
                # but no sheet dimensions.  Keep that legacy fallback; matrix
                # catalogues such as Boral always use the exact format above.
                if catalog_entry is None:
                    catalog_entry = next(
                        (
                            entry for entry in self._catalog_entries_for(material_text)
                            if abs(entry.thickness - thickness_val) < 0.001
                            and entry.width <= 0
                            and entry.height <= 0
                        ),
                        None,
                    )
                if catalog_entry is not None:
                    stock.price = (
                        catalog_entry.gross_price_m2
                        * float(stock.width)
                        * float(stock.height)
                        / 1_000_000.0
                    )
                stocks.append(stock)
            continue
            try:
                width = _strict_number(width_text)
                height = _strict_number(height_text)
                quantity = _strict_int(qty_text)
            except ValueError:
                raise ValueError(f"Popraw płytę w wierszu {row + 1}. Wpisz szerokość, wysokość i całkowitą ilość.") from None
            if width <= 0 or height <= 0 or quantity <= 0:
                raise ValueError(f"Popraw płytę w wierszu {row + 1}. Wymiary i ilość muszą być większe od zera.")
            stocks.append(
                SheetStock(
                    material="standard",
                    thickness=1,
                    width=width,
                    height=height,
                    quantity=quantity,
                    price=0,
                    allow_rotation=True,
                    min_offcut_width=0,
                    min_offcut_height=0,
                    source="stock",
                    nominal_width=width,
                    nominal_height=height,
                    sheet_allowance=0,
                )
            )
        if errors:
            raise ValueError("\n".join(errors[:8]))
        if not stocks:
            raise ValueError("Dodaj przynajmniej jeden dostępny format płyty.")
        return stocks

    def _smart_catalog_stock(
        self,
        parts: list[SheetPart],
        manual_stock: list[SheetStock],
    ) -> list[SheetStock]:
        """Build smart candidates from priced formats and visible manual stock.

        A manual row is an explicit declaration that a board is available.  It
        must not disappear merely because the catalogue contains other formats
        for the same material and thickness.  Catalogue rows still replace an
        equal manual size so that the exact supplier price is retained.
        """
        candidates: list[SheetStock] = []
        missing: list[str] = []
        specifications = sorted(
            {
                (str(part.material or "standard").strip() or "standard", round(float(part.thickness), 4))
                for part in parts
            },
            key=lambda item: (item[0].casefold(), item[1]),
        )
        for material, thickness in specifications:
            templates = [
                stock for stock in manual_stock
                if materials_are_compatible(stock.material, material)
                and abs(float(stock.thickness) - thickness) < 0.001
            ]
            default_template = templates[0] if templates else None
            entries = [
                entry for entry in self._catalog_entries_for(material)
                if abs(float(entry.thickness) - thickness) < 0.001
                and float(entry.width) > 0
                and float(entry.height) > 0
                and float(entry.gross_price_m2) > 0
            ]
            if entries:
                for entry in entries:
                    exact_template = next(
                        (
                            stock for stock in templates
                            if {
                                round(float(stock.width), 3), round(float(stock.height), 3)
                            } == {
                                round(float(entry.width), 3), round(float(entry.height), 3)
                            }
                        ),
                        default_template,
                    )
                    allow_rotation = (
                        bool(exact_template.allow_rotation)
                        if exact_template is not None
                        else bool(self._algo_settings.get("allow_rotation_stock", True))
                    )
                    preferred_axis = (
                        str(exact_template.preferred_cut_axis or "auto")
                        if exact_template is not None
                        else "auto"
                    )
                    area_m2 = float(entry.width) * float(entry.height) / 1_000_000.0
                    candidates.append(
                        SheetStock(
                            material=material,
                            thickness=thickness,
                            width=float(entry.width),
                            height=float(entry.height),
                            quantity=1,
                            price=float(entry.gross_price_m2) * area_m2,
                            allow_rotation=allow_rotation,
                            min_offcut_width=0.0,
                            min_offcut_height=0.0,
                            source="smart-candidate",
                            nominal_width=float(entry.width),
                            nominal_height=float(entry.height),
                            stack_size=1,
                            priority=0,
                            preferred_cut_axis=preferred_axis,
                        )
                    )
                catalog_sizes = {
                    tuple(sorted((round(float(entry.width), 3), round(float(entry.height), 3))))
                    for entry in entries
                }
                candidates.extend(
                    replace(stock, quantity=1, stack_size=1, source="smart-candidate", priority=0)
                    for stock in templates
                    if tuple(
                        sorted((round(float(stock.width), 3), round(float(stock.height), 3)))
                    ) not in catalog_sizes
                )
            elif templates:
                # A manually defined material without a dimensional price list
                # still works; every visible format becomes a smart candidate.
                candidates.extend(
                    replace(stock, quantity=1, stack_size=1, source="smart-candidate", priority=0)
                    for stock in templates
                )
            else:
                missing.append(f"{material}, gr. {thickness:g} mm")

        if missing:
            raise ValueError(
                "Tryb inteligentny nie znalazł dostępnych formatów dla: " + ", ".join(missing) + "."
            )

        unique: dict[tuple[object, ...], SheetStock] = {}
        for stock in candidates:
            key = (
                stock.material.casefold(),
                round(stock.thickness, 4),
                tuple(sorted((round(stock.width, 3), round(stock.height, 3)))),
                round(stock.price, 4),
            )
            unique.setdefault(key, stock)
        return sorted(
            unique.values(),
            key=lambda stock: (stock.material.casefold(), stock.thickness, stock.width * stock.height, stock.price),
        )

    def _load_stock_rows(self, stocks: list[SheetStock]) -> None:
        if stocks and hasattr(self, "material_selector"):
            first = stocks[0]
            material_index = self.material_selector.findData(first.material)
            if material_index < 0:
                catalog_entry = next(
                    (item for item in self._material_catalog if item.material == first.material),
                    None,
                )
                if catalog_entry is not None:
                    material_index = self.material_selector.findData(catalog_family_label(catalog_entry))
            if material_index >= 0:
                self.material_selector.setCurrentIndex(material_index)
                for index in range(self.catalog_thickness_selector.count()):
                    selected = self.catalog_thickness_selector.itemData(index)
                    thickness = selected.thickness if isinstance(selected, MaterialCatalogEntry) else safeNumber(selected)
                    if thickness is not None and abs(float(thickness) - first.thickness) < 0.001:
                        self.catalog_thickness_selector.setCurrentIndex(index)
                        break
            else:
                self.material_selector.setCurrentIndex(-1)
                self.material_selector.setEditText(first.material)
        self.stock_table.setRowCount(0)
        for stock in stocks:
            self._add_stock_row(stock)
        if self.stock_table.rowCount() == 0:
            self._add_stock_row({"width": "", "height": "", "quantity": ""})
        self._refresh_linked_pair_glows()

    def _available_stock_quantity(self) -> int:
        try:
            return sum(int(stock.quantity) for stock in self._collect_stock())
        except Exception:
            return 0

    def _unlock_experimental_tools(self) -> bool:
        """Extension point for restricted tools; regular desktop builds are unlocked."""
        return True

    @safe_ui_action("Nie udało się wczytać formatek z DXF.")
    def import_dxf_parts(self) -> None:
        """Import the complete DXF drawing as one rectangular blank."""
        if not self._unlock_experimental_tools():
            return
        last_dir = str(repositories.get_setting("last_dxf_import_dir", "") or "")
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Wczytaj formatki z DXF",
            last_dir,
            "Plik DXF (*.dxf)",
        )
        if not path_str:
            return
        source = Path(path_str)
        repositories.set_setting("last_dxf_import_dir", str(source.parent))

        material = ""
        thickness = float(getattr(self, "_last_thickness", 0.0) or 0.0)
        try:
            stock = self._collect_stock()
            if stock:
                material = "" if stock[-1].material == "standard" else stock[-1].material
                thickness = stock[-1].thickness
        except ValueError:
            # A partially entered stock row must not prevent the user from
            # importing details. The current material/thickness stay as defaults.
            material = str(self._selected_material_name(thickness) or "")

        from import_export.dxf_io import DxfError, import_dxf_parts

        try:
            imported = import_dxf_parts(source, material=material, thickness=thickness)
        except DxfError as exc:
            QMessageBox.warning(self, "Import DXF", str(exc))
            return

        self._record_parts_state()
        for part in imported:
            self.add_part_row([part.thickness, part.width, part.height, part.quantity, part.material])
        self._record_parts_state()
        total = sum(part.quantity for part in imported)
        QMessageBox.information(
            self,
            "Import DXF",
            f"Dodano rysunek jako {total} formatkę o wymiarze "
            f"{imported[0].width:g} x {imported[0].height:g} mm. "
            "Wymiar obejmuje cały obszar geometrii DXF.",
        )
        self.statusBar().showMessage(f"Wczytano DXF: {source.name}", 5000)

    def _parts_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("sectionCard")

        add = QPushButton("+ Dodaj")
        add.setObjectName("smallButton")
        remove = QPushButton("- Usuń")
        remove.setObjectName("smallButton")
        undo_last = QPushButton("Cofnij")
        undo_last.setObjectName("smallButton")
        undo_last.setToolTip("Usuń ostatnio dodaną formatkę (od dołu tabeli)")
        undo_last.clicked.connect(self._remove_last_part_row)
        technical_editor = QToolButton()
        technical_editor.setObjectName("smallIconButton")
        technical_editor.setIcon(_gear_icon())
        technical_editor.setToolTip("Otwórz eksperymentalny edytor rysunku technicznego")
        technical_editor.clicked.connect(self.open_technical_editor)
        self.cad_inspection_button = QToolButton()
        self.cad_inspection_button.setObjectName("smallButton")
        self.cad_inspection_button.setText("CAD")
        self.cad_inspection_button.setToolTip("Otwórz, obracaj i mierz modele DXF, STEP lub STL")
        self.cad_inspection_button.clicked.connect(self.open_cad_inspection)
        excel_button = QToolButton()
        excel_button.setObjectName("smallButton")
        excel_button.setText("Excel")
        excel_button.setToolTip("Masowy import formatek i płyt z Excela")
        excel_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        excel_menu = QMenu(excel_button)
        excel_menu.addAction("Wczytaj zlecenie z XLSX…", self._import_batch_workbook_dialog)
        excel_menu.addAction("Otwórz szablon w Excelu", self._open_batch_template)
        excel_menu.addAction("Zapisz kopię szablonu…", self._save_batch_template_copy)
        excel_button.setMenu(excel_menu)

        for _btn in (add, remove, undo_last):
            _btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        add.setMinimumWidth(94)
        remove.setMinimumWidth(82)
        undo_last.setMinimumWidth(92)

        add.clicked.connect(self.add_part_row)
        remove.clicked.connect(self.remove_selected_rows)
        self.part_buttons.extend([add, remove, undo_last])

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(undo_last)
        buttons.addWidget(technical_editor)
        buttons.addWidget(self.cad_inspection_button)
        buttons.addWidget(excel_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(9)
        layout.addWidget(self._section_title("Formatki"))
        layout.addLayout(buttons)
        layout.addWidget(self.parts, 1)
        return section

    @safe_ui_action("Nie udało się otworzyć edytora technicznego.")
    def open_technical_editor(self) -> None:
        from app.technical_editor import TechnicalEditorDialog

        dialog = TechnicalEditorDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_dimensions:
            return
        width, height = dialog.result_dimensions
        thickness = float(getattr(self, "_last_thickness", 0.0) or 0.0)
        material = self._selected_material_name(thickness)
        self.add_part_row([thickness, width, height, 1, material])
        self._focus_part_cell(self.parts.rowCount() - 1, PART_WIDTH_COLUMN)

    @safe_ui_action("Nie udało się otworzyć podglądu CAD.")
    def open_cad_inspection(self) -> None:
        from app.cad_viewer import CadInspectionDialog

        CadInspectionDialog(self).exec()

    def _remove_last_part_row(self) -> None:
        """Remove the last non-blank row from the parts table, with undo support."""
        # Find the last row that has any content.
        last_row = -1
        for row in range(self.parts.rowCount() - 1, -1, -1):
            w = self.parts.item(row, PART_WIDTH_COLUMN)
            h = self.parts.item(row, PART_HEIGHT_COLUMN)
            q = self.parts.item(row, PART_QUANTITY_COLUMN)
            if any(
                item is not None and item.text().strip()
                for item in (w, h, q)
            ):
                last_row = row
                break
        if last_row == -1:
            self.statusBar().showMessage("Brak formatek do usunięcia", 2000)
            return
        self._record_parts_state()   # push current state before mutation
        self.parts.removeRow(last_row)
        self._renumber_parts_rows()
        self._refresh_linked_pair_glows()
        self._record_parts_state()   # record new state
        self.statusBar().showMessage(f"Usunięto wiersz {last_row + 1}", 1800)

    def _renumber_parts_rows(self) -> None:
        """Compatibility hook kept for callers after removing row numbering."""

    def _batch_template_path(self) -> Path:
        return _resource_path("sample_data/SIEKACZ9000_szablon_zlecenia.xlsx")

    @safe_ui_action("Nie udało się otworzyć szablonu Excel.")
    def _open_batch_template(self) -> None:
        template = self._batch_template_path()
        if not template.is_file():
            raise FileNotFoundError(f"Brak dołączonego szablonu: {template}")
        from import_export.batch_workbook import write_catalog_synced_template

        synced = Path(tempfile.gettempdir()) / "SIEKACZ9000_szablon_zlecenia.xlsx"
        write_catalog_synced_template(template, synced, self._material_catalog)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(synced.resolve()))):
            raise OSError("System nie znalazł programu do otwierania plików XLSX.")
        self.statusBar().showMessage("Otwarto szablon zsynchronizowany z aktualnym katalogiem.", 5000)

    @safe_ui_action("Nie udało się zapisać szablonu Excel.")
    def _save_batch_template_copy(self) -> None:
        template = self._batch_template_path()
        if not template.is_file():
            raise FileNotFoundError(f"Brak dołączonego szablonu: {template}")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz szablon zlecenia",
            "SIEKACZ9000_szablon_zlecenia.xlsx",
            "Arkusz Excel (*.xlsx)",
        )
        if not path_str:
            return
        destination = Path(path_str)
        if destination.suffix.lower() != ".xlsx":
            destination = destination.with_suffix(".xlsx")
        from import_export.batch_workbook import write_catalog_synced_template

        write_catalog_synced_template(template, destination, self._material_catalog)
        self.statusBar().showMessage(f"Zapisano szablon: {destination.name}", 5000)

    @safe_ui_action("Nie udało się wczytać zlecenia z Excela.")
    def _import_batch_workbook_dialog(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Wczytaj formatki i płyty",
            "",
            "Arkusz Excel (*.xlsx)",
        )
        if not path_str:
            return
        self._load_batch_workbook(Path(path_str), ask_before_replace=True)

    def _load_batch_workbook(self, path: Path, *, ask_before_replace: bool = False) -> None:
        from import_export.batch_workbook import read_batch_workbook

        data = read_batch_workbook(path)
        if ask_before_replace and (
            any(
                (self.parts.item(row, PART_HEIGHT_COLUMN) and self.parts.item(row, PART_HEIGHT_COLUMN).text().strip())
                or (self.parts.item(row, PART_WIDTH_COLUMN) and self.parts.item(row, PART_WIDTH_COLUMN).text().strip())
                for row in range(self.parts.rowCount())
            )
            or any(not self._stock_row_is_blank(row) for row in range(self.stock_table.rowCount()))
        ):
            answer = QMessageBox.question(
                self,
                "Wczytaj zlecenie z Excela",
                "Zastąpić obecne formatki i płyty danymi z wybranego skoroszytu?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._apply_batch_data(data.parts, data.stocks, replace=True)
        self.statusBar().showMessage(
            f"Wczytano {len(data.parts)} wierszy formatek i {len(data.stocks)} wierszy płyt z {path.name}.",
            7000,
        )

    def _append_batch_parts(self, rows: list[dict[str, object]]) -> None:
        for record in rows:
            row = self.parts.rowCount()
            self.add_part_row(
                [
                    record["thickness"],
                    record["width"],
                    record["height"],
                    record["quantity"],
                    record.get("material", ""),
                ]
            )
            item = self.parts.item(row, PART_MATERIAL_COLUMN)
            if item is not None:
                item.setData(PART_ALLOW_ROTATION_ROLE, bool(record.get("allow_rotation", True)))
                item.setData(PART_PRIORITY_ROLE, int(record.get("priority", 0) or 0))
                item.setData(PART_LABEL_ROLE, str(record.get("label", "") or ""))
                item.setData(PART_NOTES_ROLE, str(record.get("notes", "") or ""))

    def _append_batch_stocks(self, rows: list[dict[str, object]]) -> None:
        for record in rows:
            self._add_stock_row(record)

    def _apply_batch_data(
        self,
        parts: list[dict[str, object]],
        stocks: list[dict[str, object]],
        *,
        replace: bool,
    ) -> None:
        was_suspended = self._parts_undo_suspended
        self._parts_undo_suspended = True
        try:
            if replace:
                self.parts.setRowCount(0)
                self.stock_table.setRowCount(0)
            self._append_batch_stocks(stocks)
            self._append_batch_parts(parts)
            if self.parts.rowCount() == 0:
                self.add_part_row()
            if self.stock_table.rowCount() == 0:
                self._add_stock_row({"width": "", "height": "", "quantity": ""})
            self._sync_stock_with_parts()
            self._refresh_linked_pair_glows()
        finally:
            self._parts_undo_suspended = was_suspended
        self._reset_parts_undo_history()

    def _paste_part_rows(self, text: str) -> None:
        from import_export.batch_workbook import parse_clipboard_rows

        try:
            rows = parse_clipboard_rows(text, "parts")
        except ValueError as exc:
            QMessageBox.warning(self, "Wklej formatki z Excela", str(exc))
            return
        if not rows:
            return
        # Remove untouched draft rows before appending real Excel data.
        for row in range(self.parts.rowCount() - 1, -1, -1):
            height = self.parts.item(row, PART_HEIGHT_COLUMN)
            width = self.parts.item(row, PART_WIDTH_COLUMN)
            if not ((height and height.text().strip()) or (width and width.text().strip())):
                self.parts.removeRow(row)
        self._append_batch_parts(rows)
        self._sync_stock_with_parts()
        self._record_parts_state()
        self.statusBar().showMessage(f"Wklejono {len(rows)} wierszy formatek z Excela.", 5000)

    def _paste_stock_rows(self, text: str) -> None:
        from import_export.batch_workbook import parse_clipboard_rows

        try:
            rows = parse_clipboard_rows(text, "stocks")
        except ValueError as exc:
            QMessageBox.warning(self, "Wklej płyty z Excela", str(exc))
            return
        if not rows:
            return
        if self.stock_table.rowCount() == 1:
            material_item = self.stock_table.item(0, STOCK_MATERIAL_COLUMN)
            if self._stock_row_is_blank(0) or bool(material_item and material_item.data(STOCK_TEMPLATE_ROLE)):
                self.stock_table.setRowCount(0)
        self._append_batch_stocks(rows)
        self._refresh_linked_pair_glows()
        self.statusBar().showMessage(f"Wklejono {len(rows)} wierszy płyt z Excela.", 5000)

    @safe_ui_action("Nie udało się zapisać szablonu.")
    def _export_parts_template(self) -> None:
        """T1-8: write a CSV/XLSX template with the canonical part headers."""
        default_name = "szablon_formatki.csv"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz szablon formatek",
            default_name,
            "Plik CSV (*.csv);;Plik Excel (*.xlsx)",
        )
        if not path_str:
            return
        path = Path(path_str)
        rows = [
            {"Szerokość": 800, "Długość": 600, "Ilość": 4},
            {"Szerokość": 400, "Długość": 300, "Ilość": 12},
            {"Szerokość": 200, "Długość": 100, "Ilość": 30},
        ]
        if path.suffix.lower() == ".xlsx":
            from import_export.xlsx_io import write_xlsx
            # write_xlsx expects {sheet_name: rows}
            write_xlsx(path, {"Formatki": rows})
        else:
            # Ensure .csv extension
            if path.suffix.lower() != ".csv":
                path = path.with_suffix(".csv")
            from import_export.csv_io import write_csv
            write_csv(path, rows)
        self.statusBar().showMessage(f"Zapisano szablon: {path.name}")

    def _calculate_button(self) -> QPushButton:
        self.calculate_button = QPushButton("Oblicz rozkrój")
        self.calculate_button.setObjectName("primaryCalculate")
        self.calculate_button.setToolTip("Oblicz rozkrój (Ctrl+Enter)")
        self.calculate_button.clicked.connect(self.calculate)
        return self.calculate_button

    def _history_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("workspaceRoot")

        self.history_search = QLineEdit()
        self.history_search.setObjectName("premiumInput")
        self.history_search.setPlaceholderText("Szukaj projektu, klienta lub trybu...")
        self.history_search.textChanged.connect(self._refresh_history_view)

        # T1-7: filter dropdowns (Client, Sort) — populated dynamically from
        # the saved project history.  Material filter intentionally removed
        # at user request (saved projects rarely carry distinct material tags).
        self.history_client_filter = QComboBox()
        self.history_client_filter.setObjectName("premiumInput")
        self.history_client_filter.setMinimumWidth(150)
        self.history_client_filter.currentIndexChanged.connect(self._refresh_history_view)

        self.history_sort_filter = QComboBox()
        self.history_sort_filter.setObjectName("premiumInput")
        self.history_sort_filter.setMinimumWidth(190)
        self.history_sort_filter.addItem("Najnowsze najpierw", "modified_desc")
        self.history_sort_filter.addItem("Najstarsze najpierw", "modified_asc")
        self.history_sort_filter.addItem("Nazwa A→Z", "name_asc")
        self.history_sort_filter.addItem("Nazwa Z→A", "name_desc")
        self.history_sort_filter.addItem("Wykorzystanie ↓", "utilization_desc")
        self.history_sort_filter.addItem("Liczba płyt ↑", "sheets_asc")
        self.history_sort_filter.currentIndexChanged.connect(self._refresh_history_view)

        self.history_list = QWidget()
        self.history_list.setObjectName("historyList")
        self.history_list_layout = QVBoxLayout(self.history_list)
        self.history_list_layout.setContentsMargins(0, 0, 0, 0)
        self.history_list_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.history_list)

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 18, 24, 24)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.history_search, 2)
        top.addWidget(QLabel("Klient:"))
        top.addWidget(self.history_client_filter)
        top.addWidget(QLabel("Sortuj:"))
        top.addWidget(self.history_sort_filter)
        layout.addLayout(top)
        layout.addWidget(scroll, 1)
        return tab

    def _populate_history_filter_options(self, projects: list[dict[str, object]]) -> None:
        """Refresh the client dropdown contents to reflect saved projects."""
        clients = sorted({
            str(record.get("client_name") or "Bez klienta").strip() or "Bez klienta"
            for record in projects
        })

        def _repopulate(combo: QComboBox, items: list[str], all_label: str) -> None:
            previous = combo.currentData()
            blocker = combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(all_label, "")
                for value in items:
                    combo.addItem(value, value)
                if previous:
                    idx = combo.findData(previous)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            finally:
                combo.blockSignals(blocker)

        _repopulate(self.history_client_filter, clients, "Wszyscy klienci")

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _refresh_history_view(self, *_ignored) -> None:
        if not hasattr(self, "history_list_layout"):
            return
        self._clear_layout(self.history_list_layout)
        projects = project_history.list_projects()

        # T1-7: keep filter dropdown contents in sync with available data.
        if hasattr(self, "history_client_filter"):
            self._populate_history_filter_options(projects)

        # ── Text search ───────────────────────────────────────────────
        query = self.history_search.text().strip().lower() if hasattr(self, "history_search") else ""
        if query:
            def matches(record: dict[str, object]) -> bool:
                summary = dict(record.get("summary") or {})
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        record.get("name"),
                        record.get("client_name"),
                        record.get("created_at"),
                        record.get("modified_at"),
                        summary.get("mode"),
                        summary.get("sheet_format"),
                        summary.get("material"),
                    )
                ).lower()
                return query in haystack

            projects = [record for record in projects if matches(record)]

        # ── Client filter ─────────────────────────────────────────────
        if hasattr(self, "history_client_filter"):
            client_filter = (self.history_client_filter.currentData() or "").strip()
            if client_filter:
                projects = [
                    record for record in projects
                    if (str(record.get("client_name") or "Bez klienta").strip() or "Bez klienta") == client_filter
                ]

        # ── Sort ──────────────────────────────────────────────────────
        if hasattr(self, "history_sort_filter"):
            sort_key = self.history_sort_filter.currentData() or "modified_desc"

            def _name_of(record: dict[str, object]) -> str:
                return str(record.get("name") or "").lower()

            def _modified_of(record: dict[str, object]) -> str:
                return str(record.get("modified_at") or record.get("created_at") or "")

            def _util_of(record: dict[str, object]) -> float:
                return float((record.get("summary") or {}).get("utilization") or 0.0)

            def _sheets_of(record: dict[str, object]) -> int:
                return int((record.get("summary") or {}).get("used_sheets") or 0)

            sorters = {
                "modified_desc": lambda r: _modified_of(r),
                "modified_asc": lambda r: _modified_of(r),
                "name_asc": _name_of,
                "name_desc": _name_of,
                "utilization_desc": _util_of,
                "sheets_asc": _sheets_of,
            }
            reverse_keys = {"modified_desc", "name_desc", "utilization_desc"}
            projects = sorted(projects, key=sorters.get(sort_key, sorters["modified_desc"]),
                              reverse=sort_key in reverse_keys)

        if not projects:
            empty = QLabel(
                "Brak projektów pasujących do wyszukiwania."
                if query
                else "Brak zapisanych projektów. Po obliczeniu rozkroju kliknij Zapisz projekt."
            )
            empty.setObjectName("summaryLabel")
            empty.setWordWrap(True)
            self.history_list_layout.addWidget(empty)
            self.history_list_layout.addStretch(1)
            return
        for record in projects:
            self.history_list_layout.addWidget(self._history_card(record))
        self.history_list_layout.addStretch(1)

    def _history_card(self, record: dict[str, object]) -> QWidget:
        card = QWidget()
        card.setObjectName("sectionCard")
        name = str(record.get("name") or "Projekt bez nazwy")
        summary = dict(record.get("summary") or {})
        client_name = str(record.get("client_name") or "Bez klienta")
        mode = str(summary.get("mode") or "comfort").upper()
        modified = _display_date(str(record.get("modified_at") or record.get("created_at") or ""))
        used_sheets = int(summary.get("used_sheets") or 0)
        utilization = float(summary.get("utilization") or 0.0)

        title = QLabel(name)
        title.setObjectName("sectionTitle")
        meta = QLabel(
            f"{client_name}  |  {modified}  |  {mode}  |  "
            f"Płyty: {used_sheets}  |  Wykorzystanie: {utilization:.1f}%"
        )
        meta.setObjectName("summaryLabel")
        meta.setWordWrap(True)

        open_button = QPushButton("Otwórz")
        open_button.setObjectName("primaryButton")
        duplicate_button = QPushButton("Duplikuj")
        duplicate_button.setObjectName("smallButton")
        delete_button = QPushButton("Usuń")
        delete_button.setObjectName("smallButton")
        export_button = QPushButton("Eksportuj PNG")
        export_button.setObjectName("smallButton")
        project_id = str(record.get("id") or "")
        details_button = QPushButton("Zwiń" if project_id in self.expanded_history_ids else "Rozwiń")
        details_button.setObjectName("smallButton")

        open_button.clicked.connect(lambda checked=False, pid=project_id: self.open_history_project(pid))
        duplicate_button.clicked.connect(lambda checked=False, pid=project_id: self.duplicate_history_project(pid))
        delete_button.clicked.connect(lambda checked=False, pid=project_id: self.delete_history_project(pid))
        export_button.clicked.connect(lambda checked=False, pid=project_id: self.export_history_project_png(pid))
        details_button.clicked.connect(lambda checked=False, pid=project_id: self._toggle_history_details(pid))

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(open_button)
        actions.addWidget(duplicate_button)
        actions.addWidget(delete_button)
        actions.addWidget(export_button)
        actions.addWidget(details_button)
        actions.addStretch(1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(meta)
        layout.addLayout(actions)
        if project_id in self.expanded_history_ids:
            layout.addWidget(self._history_details_widget(record))
        return card

    def _toggle_history_details(self, project_id: str) -> None:
        if project_id in self.expanded_history_ids:
            self.expanded_history_ids.remove(project_id)
        else:
            self.expanded_history_ids.add(project_id)
        self._refresh_history_view()

    def _history_details_widget(self, record: dict[str, object]) -> QWidget:
        details = QWidget()
        details.setObjectName("historyDetails")
        layout = QVBoxLayout(details)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        try:
            project = sanitizeProjectState(dict(record.get("project") or {}), max_total_parts=None)
        except Exception:
            project = Project()

        sheet_lines = [
            f"{(stock.source if stock.source not in {'stock', 'missing'} else 'Płyta')}: "
            f"{stock.nominal_width or stock.width:.0f} x {stock.nominal_height or stock.height:.0f} mm, {int(stock.quantity)} szt."
            for stock in project.sheet_stock
        ]
        part_lines = [
            f"{part.width:.0f} x {part.height:.0f} mm — {part.quantity} szt."
            for part in project.sheet_parts
        ]
        notes = str(record.get("notes") or project.meta.notes or "").strip()
        summary = dict(record.get("summary") or {})
        detail_text = "\n".join(
            [
                f"Projekt: {record.get('name') or 'Projekt bez nazwy'}",
                f"Klient: {record.get('client_name') or 'Bez klienta'}",
                f"Data zapisu: {_display_date(str(record.get('modified_at') or record.get('created_at') or ''))}",
                f"Tryb cięcia: {str(summary.get('mode') or project.settings.optimization_mode or 'comfort').upper()}",
                f"Płyty: {'; '.join(sheet_lines) if sheet_lines else 'Brak danych'}",
                f"Formatki: {'; '.join(part_lines) if part_lines else 'Brak danych'}",
                f"Wynik: {int(summary.get('used_sheets') or 0)} płyt, wykorzystanie {float(summary.get('utilization') or 0.0):.1f}%",
                f"Notatka: {notes if notes else 'Brak notatki'}",
            ]
        )
        label = QLabel(detail_text)
        label.setObjectName("summaryLabel")
        label.setWordWrap(True)
        layout.addWidget(label)
        return details

    def _load_example_rows(self) -> None:
        self.add_part_row(["", "", 1])

    def _row_index_item(self, row: int) -> QTableWidgetItem:
        item = QTableWidgetItem(str(row + 1))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _refresh_row_numbers(self) -> None:
        """Compatibility hook kept for callers after removing row numbering."""

    def add_part_row(self, values: list[object] | None = None) -> None:
        context_material, context_thickness = self._active_part_context()
        if not values or isinstance(values, bool):
            values = [
                context_thickness,
                "",
                "",
                1,
                context_material or self._selected_material_name(context_thickness),
            ]
        elif len(values) == 3:
            # Legacy short form is (szerokość, wysokość, ilość).
            values = [self._last_thickness] + list(values)
        if len(values) == 6:
            values = values[1:]
        values = list(values)
        if len(values) < 5:
            values.append(
                context_material
                or self._selected_material_name(float(values[0] or context_thickness or self._last_thickness))
            )
        try:
            row_thickness = float(str(values[0] or context_thickness or self._last_thickness).replace(",", "."))
        except (TypeError, ValueError):
            row_thickness = float(context_thickness or self._last_thickness)
        if not str(values[4] or "").strip():
            inherited = context_material if self._material_supports_thickness(context_material, row_thickness) else ""
            values[4] = inherited or self._infer_part_material(self.parts.rowCount(), row_thickness)
        was_suspended = self._parts_undo_suspended
        self._parts_undo_suspended = True
        try:
            row = self.parts.rowCount()
            self.parts.insertRow(row)
            display_values = [values[4], values[0], values[2], values[1], values[3]]
            for col, value in zip(
                (PART_MATERIAL_COLUMN, PART_THICKNESS_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN, PART_QUANTITY_COLUMN),
                display_values,
            ):
                item = QTableWidgetItem(_format_table_number(value))
                if col == PART_MATERIAL_COLUMN:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.parts.setItem(row, col, item)
            self._set_material_badge(self.parts, row, PART_MATERIAL_COLUMN, str(values[4] or ""))
        finally:
            self._parts_undo_suspended = was_suspended
        if hasattr(self, "stock_table"):
            self._sync_stock_with_parts()
        if not was_suspended:
            self._record_parts_state()

    def _editable_part_columns(self) -> list[int]:
        return [col for col in (PART_THICKNESS_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN, PART_QUANTITY_COLUMN) if not self.parts.isColumnHidden(col)]

    def _focus_part_cell(self, row: int, col: int) -> None:
        if row < 0 or row >= self.parts.rowCount():
            return
        if col not in (PART_THICKNESS_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN, PART_QUANTITY_COLUMN):
            col = self._editable_part_columns()[0]
        item = self.parts.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            self.parts.setItem(row, col, item)
        self.parts.clearSelection()
        self.parts.setCurrentCell(row, col)
        item.setSelected(True)
        self.parts.scrollToItem(item)
        QTimer.singleShot(0, lambda item=item: self.parts.editItem(item))

    def _focus_part_width_cell(self, row: int) -> None:
        columns = self._editable_part_columns()
        self._focus_part_cell(row, PART_WIDTH_COLUMN if PART_WIDTH_COLUMN in columns else (columns[0] if columns else PART_WIDTH_COLUMN))

    def _edit_part_cell_on_click(self, row: int, col: int) -> None:
        """A real click starts editing any part parameter, including thickness."""
        if col not in (PART_THICKNESS_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN, PART_QUANTITY_COLUMN):
            return
        QTimer.singleShot(0, lambda row=row, col=col: self._focus_part_cell(row, col))

    def _handle_part_nav_key(self, row: int, col: int, backwards: bool = False) -> None:
        columns = self._editable_part_columns()
        if not columns:
            return

        if backwards:
            previous_cols = [candidate for candidate in columns if candidate < col]
            if previous_cols:
                self._focus_part_cell(row, previous_cols[-1])
            elif row > 0:
                self._focus_part_cell(row - 1, columns[-1])
            else:
                self._focus_part_cell(row, columns[0])
            return

        next_cols = [candidate for candidate in columns if candidate > col]
        if next_cols:
            self._focus_part_cell(row, next_cols[0])
            return

        if row >= self.parts.rowCount() - 1:
            self.add_part_row()
        # A new line starts with its dimensions. Thickness is inherited from
        # the active material context and remains editable on demand.
        self._focus_part_cell(
            row + 1,
            PART_THICKNESS_COLUMN if PART_THICKNESS_COLUMN in columns else columns[0],
        )

    def _add_or_focus_next_part_row(self, row: int) -> None:
        columns = self._editable_part_columns()
        self._handle_part_nav_key(row, columns[-1] if columns else PART_QUANTITY_COLUMN, False)

    def remove_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.parts.selectedIndexes()}, reverse=True)
        if not rows:
            return
        was_suspended = self._parts_undo_suspended
        self._parts_undo_suspended = True
        try:
            for row in rows:
                self.parts.removeRow(row)
            self._refresh_row_numbers()
        finally:
            self._parts_undo_suspended = was_suspended
        if not was_suspended:
            self._record_parts_state()

    def _on_parts_files_dropped(self, paths: list[Path]) -> None:
        try:
            parsed: list[tuple[float, float, int]] = []
            for path in paths:
                parsed.extend(parse_parts_file(path))
        except ValueError as exc:
            QMessageBox.warning(self, "Import formatek", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Import formatek — błąd odczytu",
                f"Nie można otworzyć pliku:\n{exc.filename or ''}\n\n"
                "Upewnij się, że plik nie jest otwarty w innym programie i że masz uprawnienia do jego odczytu.",
            )
            return
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Import formatek — nieoczekiwany błąd",
                f"Wystąpił błąd podczas wczytywania pliku:\n{type(exc).__name__}: {exc}",
            )
            return
        if not parsed:
            self.statusBar().showMessage("Plik nie zawiera żadnych formatek.", 3000)
            return

        was_suspended = self._parts_undo_suspended
        self._parts_undo_suspended = True
        try:
            for width, height, qty in parsed:
                self.add_part_row([width, height, qty])
        finally:
            self._parts_undo_suspended = was_suspended
        if not was_suspended:
            self._record_parts_state()

        names = ", ".join(p.name for p in paths)
        self.statusBar().showMessage(
            f"Wczytano {len(parsed)} formatek z: {names}", 4000
        )

    def _snapshot_parts(self) -> tuple:
        rows: list[tuple] = []
        for row in range(self.parts.rowCount()):
            t_item = self.parts.item(row, PART_THICKNESS_COLUMN)
            w_item = self.parts.item(row, PART_WIDTH_COLUMN)
            h_item = self.parts.item(row, PART_HEIGHT_COLUMN)
            q_item = self.parts.item(row, PART_QUANTITY_COLUMN)
            m_item = self.parts.item(row, PART_MATERIAL_COLUMN)
            rows.append(
                (
                    t_item.text() if t_item else "",
                    w_item.text() if w_item else "",
                    h_item.text() if h_item else "",
                    q_item.text() if q_item else "",
                    m_item.text() if m_item else "",
                    bool(m_item.data(PART_ALLOW_ROTATION_ROLE))
                    if m_item and m_item.data(PART_ALLOW_ROTATION_ROLE) is not None
                    else bool(getattr(self, "_algo_settings", {}).get("allow_rotation_parts", True)),
                    int(m_item.data(PART_PRIORITY_ROLE) or 0) if m_item else 0,
                    str(m_item.data(PART_LABEL_ROLE) or "") if m_item else "",
                    str(m_item.data(PART_NOTES_ROLE) or "") if m_item else "",
                )
            )
        return tuple(rows)

    def _apply_parts_snapshot(self, snapshot: tuple) -> None:
        self._parts_undo_suspended = True
        try:
            self.parts.setRowCount(0)
            for row_data in snapshot:
                allow_rotation = bool(getattr(self, "_algo_settings", {}).get("allow_rotation_parts", True))
                priority, label, notes = 0, "", ""
                if len(row_data) == 3:
                    t, w, h, q, material = str(self._last_thickness), row_data[0], row_data[1], row_data[2], self._selected_material_name(self._last_thickness)
                elif len(row_data) == 4:
                    t, w, h, q, material = row_data[0], row_data[1], row_data[2], row_data[3], self._selected_material_name(float(row_data[0] or self._last_thickness))
                elif len(row_data) >= 9:
                    t, w, h, q, material, allow_rotation, priority, label, notes = row_data[:9]
                else:
                    t, w, h, q, material = row_data
                row = self.parts.rowCount()
                self.parts.insertRow(row)
                self.parts.setItem(row, PART_THICKNESS_COLUMN, QTableWidgetItem(_format_table_number(t)))
                self.parts.setItem(row, PART_WIDTH_COLUMN, QTableWidgetItem(_format_table_number(w)))
                self.parts.setItem(row, PART_HEIGHT_COLUMN, QTableWidgetItem(_format_table_number(h)))
                self.parts.setItem(row, PART_QUANTITY_COLUMN, QTableWidgetItem(_format_table_number(q)))
                material_item = QTableWidgetItem(str(material))
                material_item.setFlags(material_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                material_item.setData(PART_ALLOW_ROTATION_ROLE, bool(allow_rotation))
                material_item.setData(PART_PRIORITY_ROLE, int(priority or 0))
                material_item.setData(PART_LABEL_ROLE, str(label or ""))
                material_item.setData(PART_NOTES_ROLE, str(notes or ""))
                self.parts.setItem(row, PART_MATERIAL_COLUMN, material_item)
                self._set_material_badge(self.parts, row, PART_MATERIAL_COLUMN, str(material or ""))
        finally:
            self._parts_undo_suspended = False
        self._parts_current_snapshot = snapshot
        self._sync_stock_with_parts()
        self._refresh_linked_pair_glows()

    def _record_parts_state(self) -> None:
        if self._parts_undo_suspended:
            return
        snap = self._snapshot_parts()
        if self._parts_current_snapshot is not None and snap == self._parts_current_snapshot:
            return
        if self._parts_current_snapshot is not None:
            self._parts_undo_stack.append(self._parts_current_snapshot)
            if len(self._parts_undo_stack) > 50:
                self._parts_undo_stack.pop(0)
            self._parts_redo_stack.clear()
        self._parts_current_snapshot = snap

    def _sync_stock_with_parts(self) -> None:
        if getattr(self, "_syncing_stock", False):
            return
        self._syncing_stock = True
        try:
            # Dict preserves the visible part-row order.  The previous set made
            # assignment of the first template board non-deterministic.
            required: dict[tuple[str, float], str] = {}
            for r in range(self.parts.rowCount()):
                height_item = self.parts.item(r, PART_HEIGHT_COLUMN)
                width_item = self.parts.item(r, PART_WIDTH_COLUMN)
                height_text = height_item.text().strip() if height_item else ""
                width_text = width_item.text().strip() if width_item else ""
                if not height_text and not width_text:
                    # Draft rows carry default quantity/thickness/material for
                    # faster entry, but they are not material demand yet.
                    continue
                mat_item = self.parts.item(r, PART_MATERIAL_COLUMN)
                thk_item = self.parts.item(r, PART_THICKNESS_COLUMN)
                mat_text = mat_item.text().strip() if mat_item else ""
                thk_text = thk_item.text().strip() if thk_item else ""
                if not mat_text and not self._material_catalog:
                    mat_text = "standard"
                if mat_text and mat_text != "-" and thk_text:
                    try:
                        thk = round(float(thk_text.replace(",", ".")), 4)
                        if thk > 0:
                            required.setdefault((mat_text.casefold(), thk), mat_text)
                    except ValueError:
                        pass

            existing = []
            existing_set = set()
            for r in range(self.stock_table.rowCount()):
                mat = self._stock_cell_text(r, STOCK_MATERIAL_COLUMN).strip()
                thk_text = self._stock_cell_text(r, STOCK_THICKNESS_COLUMN).strip()
                if mat and thk_text:
                    try:
                        thk = round(float(thk_text.replace(",", ".")), 4)
                        existing.append({"row": r, "mat": mat.casefold(), "thk": thk})
                        existing_set.add((mat.casefold(), thk))
                    except ValueError:
                        pass

            missing = []
            for (req_mat, req_thk), display in required.items():
                if (req_mat, req_thk) not in existing_set:
                    missing.append((req_mat, req_thk, display))

            # The initial board is a template, not a committed material.
            # Prefer replacing it before adding another row for the first part
            # material/thickness pair. Older explicitly typed boards are never
            # repurposed just because a later part has a different thickness.
            repurposable_rows = []
            for row in range(self.stock_table.rowCount()):
                material = self._stock_cell_text(row, STOCK_MATERIAL_COLUMN).strip().casefold()
                material_item = self.stock_table.item(row, STOCK_MATERIAL_COLUMN)
                is_initial_template = bool(material_item and material_item.data(STOCK_TEMPLATE_ROLE))
                if not material or material == "standard" or is_initial_template:
                    repurposable_rows.append(row)
            # Never recycle a material-specific board when a part changes.
            # Keeping that stock and adding the newly required specification is
            # safer than silently changing a different board in the table.

            for (req_mat, req_thk, display) in missing:
                catalog_format = self._default_catalog_format(display, req_thk)
                catalog_preset = (
                    (catalog_format.width, catalog_format.height)
                    if catalog_format is not None and catalog_format.width > 0 and catalog_format.height > 0
                    else None
                )
                if repurposable_rows:
                    row_to_edit = repurposable_rows.pop(0)
                    self._set_stock_cell_text(row_to_edit, STOCK_THICKNESS_COLUMN, f"{req_thk:g}")
                    self._set_stock_cell_text(row_to_edit, STOCK_MATERIAL_COLUMN, display)
                    material_item = self.stock_table.item(row_to_edit, STOCK_MATERIAL_COLUMN)
                    if material_item is not None:
                        material_item.setData(STOCK_TEMPLATE_ROLE, False)
                    if catalog_preset is not None:
                        self._set_stock_cell_text(row_to_edit, STOCK_HEIGHT_COLUMN, catalog_preset[0])
                        self._set_stock_cell_text(row_to_edit, STOCK_WIDTH_COLUMN, catalog_preset[1])
                    self._set_stock_cut_axis(row_to_edit, "auto")
                    self._set_material_badge(self.stock_table, row_to_edit, STOCK_MATERIAL_COLUMN, display, editable=False)
                    self._set_stock_format_selector(row_to_edit)
                else:
                    preset = catalog_preset or self._default_sheet_preset_for_material(display)
                    if preset is None:
                        preset = (2000.0, 1000.0)
                    self._add_stock_row({
                        "thickness": req_thk,
                        "material": display,
                        "width": preset[0],
                        "height": preset[1],
                        "quantity": 1
                    })

            self._last_known_required = set(required)
            self._refresh_linked_pair_glows()
        finally:
            self._syncing_stock = False

    def _on_parts_item_changed(self, item: QTableWidgetItem) -> None:
        if self._parts_undo_suspended:
            return
        if item.column() == PART_THICKNESS_COLUMN:
            try:
                val = float(item.text().replace(",", "."))
                if val > 0:
                    self._last_thickness = val
                    material = self._ensure_part_material(item.row(), val)
                    if material or not self._material_catalog:
                        self._sync_stock_with_parts()
                    else:
                        self.statusBar().showMessage(
                            f"Wybierz materiał dla formatki w wierszu {item.row() + 1}.",
                            5000,
                        )
                    QTimer.singleShot(
                        0,
                        lambda row=item.row(): self._focus_part_cell(row, PART_HEIGHT_COLUMN),
                    )
            except ValueError:
                pass
        self._record_parts_state()

    def _reset_parts_undo_history(self) -> None:
        self._parts_undo_stack.clear()
        self._parts_redo_stack.clear()
        self._parts_current_snapshot = self._snapshot_parts()

    def _undo_parts(self) -> None:
        if not self._parts_undo_stack:
            self.statusBar().showMessage("Brak czego cofnąć", 2000)
            return
        if self._parts_current_snapshot is not None:
            self._parts_redo_stack.append(self._parts_current_snapshot)
        target = self._parts_undo_stack.pop()
        self._apply_parts_snapshot(target)
        self.statusBar().showMessage("Cofnięto zmianę formatek (Ctrl+Z)", 2000)

    def _redo_parts(self) -> None:
        if not self._parts_redo_stack:
            self.statusBar().showMessage("Brak czego przywrócić", 2000)
            return
        if self._parts_current_snapshot is not None:
            self._parts_undo_stack.append(self._parts_current_snapshot)
        target = self._parts_redo_stack.pop()
        self._apply_parts_snapshot(target)
        self.statusBar().showMessage("Przywrócono zmianę formatek (Ctrl+Y)", 2000)

    def _collect_parts(self) -> list[SheetPart]:
        parts: list[SheetPart] = []
        total_quantity = 0
        for row in range(self.parts.rowCount()):
            thickness_text = self.parts.item(row, PART_THICKNESS_COLUMN).text().strip() if self.parts.item(row, PART_THICKNESS_COLUMN) else ""
            height_text = self.parts.item(row, PART_HEIGHT_COLUMN).text().strip() if self.parts.item(row, PART_HEIGHT_COLUMN) else ""
            width_text = self.parts.item(row, PART_WIDTH_COLUMN).text().strip() if self.parts.item(row, PART_WIDTH_COLUMN) else ""
            qty_text = self.parts.item(row, PART_QUANTITY_COLUMN).text().strip() if self.parts.item(row, PART_QUANTITY_COLUMN) else ""
            material_text = self.parts.item(row, PART_MATERIAL_COLUMN).text().strip() if self.parts.item(row, PART_MATERIAL_COLUMN) else ""
            # Quantity defaults to 1, so it cannot decide whether a row is a
            # draft.  A row without either dimension is genuinely empty.
            if not width_text and not height_text:
                continue

            try:
                thickness_val = float(thickness_text.replace(",", ".")) if thickness_text else 1.0
            except ValueError:
                thickness_val = 1.0

            if not material_text or material_text == "-":
                material_text = self._ensure_part_material(row, thickness_val)
            if not material_text:
                if self._material_catalog:
                    raise ValueError(
                        f"Wybierz materiał dla formatki w wierszu {row + 1}. "
                        "Program nie połączy formatki z przypadkową płytą."
                    )
                material_text = "standard"

            material_item = self.parts.item(row, PART_MATERIAL_COLUMN)
            imported_rotation = (
                material_item.data(PART_ALLOW_ROTATION_ROLE) if material_item is not None else None
            )
            allow_rotation = (
                bool(imported_rotation)
                if imported_rotation is not None
                else bool(getattr(self, "_algo_settings", {}).get("allow_rotation_parts", True))
            )
            priority = int(material_item.data(PART_PRIORITY_ROLE) or 0) if material_item else 0
            label = str(material_item.data(PART_LABEL_ROLE) or "") if material_item else ""
            notes = str(material_item.data(PART_NOTES_ROLE) or "") if material_item else ""

            part, row_errors = validatePart(
                {
                    "name": "",
                    "width": width_text,
                    "height": height_text,
                    "quantity": qty_text,
                    "material": material_text,
                    "thickness": thickness_val,
                    "allow_rotation": allow_rotation,
                    "priority": priority,
                    "label": label,
                    "notes": notes,
                },
                row + 1,
            )
            if row_errors:
                raise ValueError("\n".join(row_errors)) from None
            assert part is not None
            total_quantity += part.quantity
            if (
                total_quantity > MAX_TOTAL_PARTS
                and not self._part_limit_override_authorized
                and not self._authorize_part_limit_override(total_quantity)
            ):
                raise ValueError(
                    f"Za dużo formatek naraz ({_format_piece_count(total_quantity)}). "
                    f"Standardowy limit to {_format_piece_count(MAX_TOTAL_PARTS)} szt. "
                    "Aby go przekroczyć, podaj PIN administratora."
                )
            parts.append(part)
        if not parts:
            raise ValueError("Dodaj przynajmniej jedną formatkę.")
        return parts

    def _authorize_part_limit_override(self, total_quantity: int) -> bool:
        """Ask once per application window before bypassing the safe limit."""
        if self._part_limit_override_authorized:
            return True
        pin, accepted = QInputDialog.getText(
            self,
            "Duże zlecenie",
            (
                f"Zlecenie ma {_format_piece_count(total_quantity)} formatek, a standardowy limit wynosi "
                f"{_format_piece_count(MAX_TOTAL_PARTS)}.\nPodaj PIN, aby uruchomić pełną optymalizację."
            ),
            QLineEdit.EchoMode.Password,
        )
        if accepted and pin.strip() == PART_LIMIT_OVERRIDE_PIN:
            self._part_limit_override_authorized = True
            self.statusBar().showMessage(
                "Odblokowano duże zlecenia dla tej sesji.", 5000
            )
            return True
        return False

    def _project_for_calculation(self, parts: list[SheetPart]) -> Project:
        stock = self._collect_stock()
        smart_stock_mode = bool(
            hasattr(self, "smart_stock_checkbox") and self.smart_stock_checkbox.isChecked()
        )
        if smart_stock_mode:
            stock = self._smart_catalog_stock(parts, stock)
        self._last_smart_stock_mode = smart_stock_mode
        project = Project()
        project.sheet_stock = stock
        project.sheet_parts = parts
        _s = self._algo_settings
        project.settings = OptimizationSettings(
            job_type="sheet",
            algorithm="auto",
            multi_core=_s.get("multi_core", True),
            mode=str(_s.get("mode", "minimize_waste")),
            kerf=float(self._algo_settings.get("kerf", 5.0)),
            kerf_tolerance=float(self._algo_settings.get("kerf_tolerance", 0.2)),
            margin=0,
            sheet_allowance=0.0,
            min_reusable_offcut_size=self.min_reusable_offcut.value(),
            display_orientation=self.current_display_orientation(),
            cutting_mode=str(_s.get("cutting_mode", "hybrid")),
            optimization_mode=self.current_optimization_mode(),
            prefer_long_rip_cuts=True,
            allow_rotation=bool(_s.get("allow_rotation_parts", True)),
            saw_feed_m_per_min=float(_s.get("saw_feed_m_per_min", 12.0)),
            animation_mode=str(_s.get("animation_mode", "economy")),
            smart_stock_mode=smart_stock_mode,
        )
        max_parts = None if self._part_limit_override_authorized else MAX_TOTAL_PARTS
        errors = getValidationErrors(project, max_total_parts=max_parts)
        if errors:
            raise ValueError("\n".join(errors[:8]))
        self._remember_sheet_formats(stock)
        return project

    @staticmethod
    def _oversized_part_issues(project: Project) -> list[str]:
        """Return actionable preflight messages before starting the optimizer."""
        issues: list[str] = []
        allow_global_rotation = bool(project.settings.allow_rotation)
        for part in project.sheet_parts:
            compatible = [
                stock
                for stock in project.sheet_stock
                if materials_are_compatible(stock.material, part.material)
                and abs(float(stock.thickness) - float(part.thickness)) < 1e-4
            ]
            if not compatible:
                continue

            fits = False
            for stock in compatible:
                direct = part.width <= stock.width + 1e-6 and part.height <= stock.height + 1e-6
                rotated = (
                    allow_global_rotation
                    and part.allow_rotation
                    and stock.allow_rotation
                    and part.grain_direction == "none"
                    and stock.grain_direction == "none"
                    and part.height <= stock.width + 1e-6
                    and part.width <= stock.height + 1e-6
                )
                if direct or rotated:
                    fits = True
                    break
            if fits:
                continue

            available = sorted(
                {(float(stock.width), float(stock.height)) for stock in compatible},
                key=lambda size: size[0] * size[1],
                reverse=True,
            )
            format_text = ", ".join(f"{width:g} × {height:g} mm" for width, height in available[:3])
            material = "" if part.material in {"", "standard"} else f" · {part.material}"
            issues.append(
                f"Formatka {part.width:g} × {part.height:g} mm{material}, gr. {part.thickness:g} mm "
                f"nie mieści się na dostępnych płytach ({format_text})."
            )
        return issues

    def _show_oversized_parts_message(self, issues: list[str]) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Formatka jest większa niż dostępna płyta")
        box.setText("<b>Nie można rozpocząć obliczeń.</b>")
        box.setInformativeText(
            "\n\n".join(issues[:3])
            + "\n\nDodaj większy format płyty, zmniejsz formatkę albo włącz dozwolony obrót."
        )
        if len(issues) > 3:
            box.setDetailedText("\n".join(issues))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _project_from_current_inputs(self) -> Project:
        project = self._project_for_calculation(self._collect_parts())
        project.meta.client_name = self.current_client_name
        project.meta.material = project.sheet_stock[0].material if project.sheet_stock else ""
        project.meta.notes = self.current_project_notes
        if self.current_project_created_at:
            project.meta.creation_date = self.current_project_created_at
        return project

    def _default_project_name(self) -> str:
        if self.current_project_name:
            return self.current_project_name
        return "Projekt " + datetime.now().strftime("%d.%m.%Y %H:%M")

    def _build_project_record(self, name: str, client_name: str, notes: str, force_new: bool = False) -> dict[str, object]:
        project = self._project_from_current_inputs()
        project.meta.client_name = client_name
        project.meta.notes = notes
        project.meta.material = project.sheet_stock[0].material if project.sheet_stock else ""
        summary = project_history.record_summary(project, self.last_result)
        project_id = None if force_new else self.current_project_id
        created_at = "" if force_new else self.current_project_created_at
        return {
            "id": project_id,
            "name": name or self._default_project_name(),
            "client_name": client_name,
            "created_at": created_at or project_history.now_iso(),
            "modified_at": project_history.now_iso(),
            "material": summary.get("material", ""),
            "thickness": summary.get("thickness", 0),
            "sheet_format": summary.get("sheet_format", ""),
            "kerf": summary.get("kerf", 0),
            "mode": summary.get("mode", "comfort"),
            "notes": notes,
            "summary": summary,
            "project": project.to_dict(),
            "order_groups": self._serialize_order_groups(),
            "result": project_history.result_to_dict(self.last_result),
        }

    @safe_ui_action("Nie udało się zapisać projektu.")
    def save_project(self) -> None:
        if not self.last_result:
            QMessageBox.warning(self, "Zapis projektu", "Najpierw oblicz rozkrój, żeby zapisać projekt razem z wynikiem.")
            return
        try:
            self._collect_parts()
        except Exception as exc:
            QMessageBox.warning(self, "Zapis projektu", str(exc))
            return
        dialog = ProjectSaveDialog(self, self._default_project_name(), self.current_client_name, self.current_project_notes)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip() or self._default_project_name()
        client_name = dialog.client_name.text().strip()
        notes = dialog.notes.toPlainText().strip()
        save_as_new = dialog.save_as_new
        if self.current_project_id and not save_as_new:
            confirm = QMessageBox(self)
            confirm.setWindowTitle("Projekt już istnieje")
            confirm.setIcon(QMessageBox.Icon.Question)
            confirm.setText(f'Projekt „{self.current_project_name or name}” jest już zapisany.')
            confirm.setInformativeText("Czy chcesz nadpisać istniejący projekt, czy utworzyć nowy?")
            overwrite_button = confirm.addButton("Nadpisz projekt", QMessageBox.ButtonRole.AcceptRole)
            new_button = confirm.addButton("Utwórz nowy", QMessageBox.ButtonRole.ActionRole)
            confirm.addButton("Anuluj", QMessageBox.ButtonRole.RejectRole)
            confirm.exec()
            if confirm.clickedButton() is new_button:
                save_as_new = True
            elif confirm.clickedButton() is not overwrite_button:
                return
        record = self._build_project_record(name, client_name, notes, force_new=save_as_new)
        saved = project_history.save_project_record(record)
        self.current_project_id = str(saved.get("id") or "")
        self.current_project_name = str(saved.get("name") or "")
        self.current_client_name = str(saved.get("client_name") or "")
        self.current_project_notes = str(saved.get("notes") or "")
        self.current_project_created_at = str(saved.get("created_at") or "")
        self._refresh_history_view()
        self.statusBar().showMessage(f"Zapisano projekt: {self.current_project_name}")

    def _load_project_inputs(self, project: Project) -> None:
        self._load_stock_rows(project.sheet_stock)
        if hasattr(self, "smart_stock_checkbox"):
            self.smart_stock_checkbox.setChecked(bool(getattr(project.settings, "smart_stock_mode", False)))
        self._last_smart_stock_mode = bool(getattr(project.settings, "smart_stock_mode", False))
        stock = project.sheet_stock[0] if project.sheet_stock else None
        if stock:
            self.sheet_width.setValue(stock.nominal_width or stock.width)
            self.sheet_height.setValue(stock.nominal_height or stock.height)
            self.sheet_qty.setValue(max(1, int(stock.quantity)))
        self._algo_settings["kerf"] = project.settings.kerf
        self._algo_settings["kerf_tolerance"] = getattr(project.settings, "kerf_tolerance", 0.2)
        self._algo_settings["saw_feed_m_per_min"] = float(getattr(project.settings, "saw_feed_m_per_min", 12.0))
        self._algo_settings["animation_mode"] = str(getattr(project.settings, "animation_mode", "economy"))
        self._update_algo_btn()
        self.sheet_allowance.setValue(float(getattr(project.settings, "sheet_allowance", 0.0)))
        
        min_offcut = float(getattr(project.settings, "min_reusable_offcut_size", 200.0))
        if min_offcut < 10.0: min_offcut = 200.0
        self.min_reusable_offcut.setValue(min_offcut)
        self._set_display_orientation_combo(project.settings.display_orientation)
        self.parts.setRowCount(0)
        self._parts_undo_suspended = True
        try:
            for part in project.sheet_parts:
                self.add_part_row([part.thickness, part.width, part.height, part.quantity, part.material])
                material_item = self.parts.item(self.parts.rowCount() - 1, PART_MATERIAL_COLUMN)
                if material_item is not None:
                    material_item.setData(PART_ALLOW_ROTATION_ROLE, bool(part.allow_rotation))
                    material_item.setData(PART_PRIORITY_ROLE, int(part.priority))
                    material_item.setData(PART_LABEL_ROLE, str(part.label or ""))
                    material_item.setData(PART_NOTES_ROLE, str(part.notes or ""))
            if self.parts.rowCount() == 0:
                self.add_part_row(["", "", 1])
        finally:
            self._parts_undo_suspended = False
        self._reset_parts_undo_history()
        self._refresh_linked_pair_glows()

    @safe_ui_action("Nie udało się otworzyć projektu z historii.")
    def open_history_project(self, project_id: str) -> None:
        record = project_history.get_project(project_id)
        if not record:
            QMessageBox.warning(self, "Historia projektów", "Nie znaleziono projektu.")
            self._refresh_history_view()
            return
        try:
            project = sanitizeProjectState(dict(record.get("project") or {}), max_total_parts=None)
            result = project_history.result_from_dict(dict(record.get("result") or {}))
        except Exception as exc:
            QMessageBox.warning(self, "Historia projektów", f"Nie udało się otworzyć projektu: {exc}")
            return
        self.current_project_id = str(record.get("id") or "")
        self.current_project_name = str(record.get("name") or "")
        self.current_client_name = str(record.get("client_name") or "")
        self.current_project_notes = str(record.get("notes") or "")
        self.current_project_created_at = str(record.get("created_at") or "")
        self._load_project_inputs(project)
        self._restore_order_groups(record.get("order_groups") or [])
        self.last_result = result
        self.layout_view.show_result(result)
        self.warning.hide()
        if hasattr(self, "_zoom_widget") and result is not None:
            self._zoom_widget.show()
            if hasattr(self, "_canvas_overlay_filter"):
                self._canvas_overlay_filter._reposition()
        # T1-3: also refresh the sheet nav when loading a project from history.
        if result is not None:
            sheet_count = len(getattr(result, "sheet_layouts", []) or [])
            missing_count = len(getattr(result, "missing_sheet_layouts", []) or [])
            self._rebuild_sheet_nav(sheet_count, missing_count)
        elif hasattr(self, "_sheet_nav_widget"):
            self._sheet_nav_widget.hide()
        self._select_page(0)
        self.statusBar().showMessage(f"Otwarty projekt: {self.current_project_name}")

    @safe_ui_action("Nie udało się zduplikować projektu.")
    def duplicate_history_project(self, project_id: str) -> None:
        clone = project_history.duplicate_project(project_id)
        if not clone:
            QMessageBox.warning(self, "Historia projektów", "Nie udało się zduplikować projektu.")
            return
        self._refresh_history_view()
        self.statusBar().showMessage(f"Utworzono kopię: {clone.get('name')}")

    @safe_ui_action("Nie udało się usunąć projektu.")
    def delete_history_project(self, project_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "Usuń projekt",
            "Na pewno usunąć ten projekt z historii",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        project_history.delete_project(project_id)
        if self.current_project_id == project_id:
            self.current_project_id = None
        self._refresh_history_view()
        self.statusBar().showMessage("Projekt usunięty")

    def _png_header_text(self, client_name: str) -> str:
        date_text = datetime.now().strftime("%d.%m.%Y")
        parts = []
        if client_name:
            parts.append(f"Klient: {client_name}")
        parts.append(f"Data: {date_text}")
        notes = self.current_project_notes.strip()
        if notes:
            parts.append("Notatka: " + (notes[:140] + "..." if len(notes) > 140 else notes))
        return " | ".join(parts)

    def _ask_png_export_options(self) -> tuple[str, str] | None:
        dialog = ClientNameDialog(self, self.current_client_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        self.current_client_name = dialog.client_name.text().strip()
        return self.current_client_name, str(dialog.export_mode.currentData() or "economy")

    @safe_ui_action("Nie udało się wyeksportować projektu z historii.")
    def export_history_project_png(self, project_id: str) -> None:
        record = project_history.get_project(project_id)
        if not record:
            QMessageBox.warning(self, "Historia projektów", "Nie znaleziono projektu.")
            return
        result = project_history.result_from_dict(dict(record.get("result") or {}))
        if not result:
            QMessageBox.warning(self, "Eksport PNG", "Ten projekt nie ma zapisanego wyniku rozkroju.")
            return
        previous_result = self.last_result
        previous_theme = self.layout_view.theme
        previous_print_mode = self.layout_view.print_mode
        previous_client = self.current_client_name
        previous_notes = self.current_project_notes
        self.current_client_name = str(record.get("client_name") or "")
        self.current_project_notes = str(record.get("notes") or "")
        export_options = self._ask_png_export_options()
        if export_options is None:
            self.current_client_name = previous_client
            self.current_project_notes = previous_notes
            return
        client_name, export_mode = export_options
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz rozkrój jako PNG", "", "PNG (*.png)")
        if not path:
            self.current_client_name = previous_client
            self.current_project_notes = previous_notes
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self.layout_view.set_theme("light")
            self.layout_view.set_print_mode(export_mode == "economy")
            self.layout_view.show_result(result)
            export_scene_png(self.layout_view.scene, path, background="#ffffff", header_text=self._png_header_text(client_name))
        finally:
            self.current_client_name = previous_client
            self.current_project_notes = previous_notes
            self.layout_view.set_print_mode(previous_print_mode)
            self.layout_view.set_theme(previous_theme)
            self.layout_view.show_result(previous_result)
        self.statusBar().showMessage(f"Zapisano {path}")

    # ── Multi-board order groups ─────────────────────────────────────────────
    def _create_order_group(self) -> "OrderGroupWidget":
        index = len(self._order_groups) + 2  # group 1 = primary boxes above
        group = OrderGroupWidget(index, self._groups_container)
        group.remove_requested.connect(self._remove_order_group)
        self._apply_card_shadow(group)
        self._groups_layout.addWidget(group)
        self._order_groups.append(group)
        return group

    @safe_ui_action("Nie udało się dodać grupy.")
    def _add_order_group(self) -> None:
        self._create_order_group()

    def _serialize_order_groups(self) -> list[dict]:
        return [g.to_dict() for g in self._order_groups if not g.is_empty()]

    def _restore_order_groups(self, data: object) -> None:
        self._clear_order_groups()
        for entry in (data or []):
            if isinstance(entry, dict):
                self._create_order_group().load_dict(entry)

    def _remove_order_group(self, group: "OrderGroupWidget") -> None:
        if group in self._order_groups:
            self._order_groups.remove(group)
        self._groups_layout.removeWidget(group)
        group.setParent(None)
        group.deleteLater()
        for i, g in enumerate(self._order_groups, start=2):
            g.set_index(i)

    def _clear_order_groups(self) -> None:
        for group in list(self._order_groups):
            self._groups_layout.removeWidget(group)
            group.setParent(None)
            group.deleteLater()
        self._order_groups.clear()

    def _project_from_group(self, group: "OrderGroupWidget") -> Project | None:
        """Build an isolated Project from one extra group, reusing the active
        algorithm settings.  Returns None if the group is empty."""
        if group.is_empty():
            return None
        _s = self._algo_settings
        allow_parts = bool(_s.get("allow_rotation_parts", True))
        allow_stock = bool(_s.get("allow_rotation_stock", True))
        stock = group.collect_stock(allow_stock)
        parts = group.collect_parts(allow_parts)
        smart_stock_mode = bool(
            hasattr(self, "smart_stock_checkbox") and self.smart_stock_checkbox.isChecked()
        )
        if smart_stock_mode:
            stock = self._smart_catalog_stock(parts, stock)
        material = group.material_name()
        if not stock:
            raise ValueError(f"Grupa „{material}”: dodaj przynajmniej jedną płytę.")
        if not parts:
            raise ValueError(f"Grupa „{material}”: dodaj przynajmniej jedną formatkę.")
        project = Project()
        project.sheet_stock = stock
        project.sheet_parts = parts
        project.meta.material = material
        project.settings = OptimizationSettings(
            job_type="sheet",
            algorithm="auto",
            multi_core=_s.get("multi_core", True),
            mode=str(_s.get("mode", "minimize_waste")),
            kerf=float(_s.get("kerf", 5.0)),
            kerf_tolerance=float(_s.get("kerf_tolerance", 0.2)),
            margin=0,
            sheet_allowance=0.0,
            min_reusable_offcut_size=self.min_reusable_offcut.value(),
            display_orientation=self.current_display_orientation(),
            cutting_mode=str(_s.get("cutting_mode", "hybrid")),
            optimization_mode=self.current_optimization_mode(),
            prefer_long_rip_cuts=True,
            allow_rotation=allow_parts,
            saw_feed_m_per_min=float(_s.get("saw_feed_m_per_min", 12.0)),
            animation_mode=str(_s.get("animation_mode", "economy")),
            smart_stock_mode=smart_stock_mode,
        )
        return project

    def _collect_order_projects(self, primary: Project) -> list[Project]:
        """Primary group + every non-empty extra group, each isolated."""
        projects = self._split_project_by_thickness(primary)

        for group in self._order_groups:
            extra = self._project_from_group(group)
            if extra is not None:
                projects.extend(self._split_project_by_thickness(extra))
        return projects

    def _split_project_by_thickness(self, base_project: Project) -> list[Project]:
        split_projects = []
        def is_generic(material: str) -> bool:
            return str(material or "").strip().casefold() in {"", "standard"}

        generic_thicknesses = {
            round(float(item.thickness), 4)
            for item in list(base_project.sheet_stock) + list(base_project.sheet_parts)
            if is_generic(getattr(item, "material", ""))
        }
        specifications = {
            (
                "" if round(float(part.thickness), 4) in generic_thicknesses else str(part.material or "").strip(),
                round(float(part.thickness), 4),
            )
            for part in base_project.sheet_parts
        }
        for material, thickness in sorted(specifications, key=lambda item: (item[0].casefold(), -item[1])):
            stock_for_t = [
                stock
                for stock in base_project.sheet_stock
                if materials_are_compatible(stock.material, material)
                and abs(stock.thickness - thickness) < 1e-4
            ]
            parts_for_t = [
                part
                for part in base_project.sheet_parts
                if (not material or str(part.material or "").strip().casefold() == material.casefold())
                and abs(part.thickness - thickness) < 1e-4
            ]

            if not stock_for_t:
                display_material = material or "bez nazwy"
                raise ValueError(
                    f"Brakuje płyty {display_material} o grubości {thickness:g} mm dla wpisanych formatek."
                )
            
            from copy import deepcopy
            proj = Project()
            proj.sheet_stock = stock_for_t
            proj.sheet_parts = parts_for_t
            proj.settings = deepcopy(base_project.settings)
            proj.meta = deepcopy(base_project.meta)
            proj.meta.material = material or f"Grubość {thickness:g} mm"
            split_projects.append(proj)
        
        return split_projects



    @safe_ui_action("Nie udało się policzyć rozkroju. Sprawdź dane wejściowe.")
    def calculate(self) -> None:
        if self._is_calculating:
            return
        try:
            parts = self._collect_parts()
            primary = self._project_for_calculation(parts)
            oversized = self._oversized_part_issues(primary)
            if oversized:
                self._show_oversized_parts_message(oversized)
                return
            projects = self._collect_order_projects(primary)
        except Exception as exc:
            QMessageBox.warning(self, "Nie mogę policzyć", str(exc))
            return

        self._pending_project = primary
        payload = projects if len(projects) > 1 else primary

        self.warning.hide()
        self._set_calculating(True)
        self.statusBar().showMessage("Liczenie rozkroju...")
        self.optimization_progress_overlay.start(primary)
        if not self._progress_cancel_connected:
            self.optimization_progress_overlay.cancelled.connect(self._cancel_calculation)
            self._progress_cancel_connected = True
        self._start_calculation_worker(payload)

    def _finish_calculation(self) -> None:
        project = self._pending_project
        self._pending_project = None
        if project is None:
            self._set_calculating(False)
            return
        try:
            self._apply_result(optimize_sheet_project(project))
        except Exception as exc:
            QMessageBox.warning(self, "Nie mogę policzyć", str(exc))
        finally:
            self._set_calculating(False)

    def _start_calculation_worker(self, project: "Project | list[Project]") -> None:
        thread = QThread(self)
        worker = CalculationWorker(project)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.optimization_progress_overlay.set_progress)
        worker.finished.connect(self._calculation_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_calculation_worker_refs)
        self._calculation_thread = thread
        self._calculation_worker = worker
        thread.start()

    def _clear_calculation_worker_refs(self) -> None:
        self._calculation_thread = None
        self._calculation_worker = None

    def _notify_calculation_finished(self, result: object) -> None:
        """Show a native Windows notification without interrupting the operator."""
        tray = self._notification_tray
        if tray is None:
            return
        layouts = list(getattr(result, "sheet_layouts", []) or [])
        missing = list(getattr(result, "missing_sheet_layouts", []) or [])
        message = f"Rozkrój gotowy: {_polish_sheet_count(len(layouts))}"
        if missing:
            message += f". {_polish_sheet_count(len(missing), missing=True)}"
        tray.showMessage(
            "SIEKACZ 9000",
            message,
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _calculation_worker_finished(self, result: object, error: object) -> None:
        # If the user cancelled, drop the result silently — the run already
        # finished but the user no longer wants it.
        cancelled = bool(
            self._calculation_worker is not None
            and getattr(self._calculation_worker, "is_cancelled", lambda: False)()
        )
        # The progress overlay MUST always be stopped, even if _apply_result
        # raises while rendering — otherwise the overlay keeps spinning with a
        # growing timer and looks like a multi-hundred-second "calculation".
        overlay_finished = False
        try:
            if cancelled:
                self.optimization_progress_overlay.stop()
                self.statusBar().showMessage("Obliczenia anulowane")
                overlay_finished = True
            elif error:
                print(f"CALCULATION ERROR: {error}", flush=True)
                self.optimization_progress_overlay.stop()
                overlay_finished = True
                QMessageBox.warning(self, "Nie mogę policzyć", str(error))
            elif result is not None:
                self._apply_result(result)
                self.optimization_progress_overlay.finish()
                self._notify_calculation_finished(result)
                overlay_finished = True
        except Exception as exc:
            _logger.exception("Exception while applying optimization result")
            QMessageBox.warning(self, "Błąd wyświetlania", f"Obliczenia ukończone, ale wystąpił błąd wyświetlania:\n{exc}")
        finally:
            if not overlay_finished:
                self.optimization_progress_overlay.stop()
            self._pending_project = None
            self._set_calculating(False)

    def _cancel_calculation(self) -> None:
        """User clicked the overlay's Cancel button.

        The optimizer cannot be interrupted mid-run (no polling), so we
        mark the in-flight worker as cancelled.  When it eventually emits
        ``finished``, the result is discarded and the UI returns to idle.
        """
        worker = self._calculation_worker
        if worker is None or self._is_exporting:
            return
        if hasattr(worker, "cancel"):
            worker.cancel()
        self.statusBar().showMessage("Anulowanie obliczeń...")

    def _apply_result(self, result) -> None:
        self.last_result = result
        if hasattr(self, "preview_pdf_button"):
            self.preview_pdf_button.setEnabled(self._has_sendable_result())
        # Drain pending events before heavy rendering so Windows does not mark the
        # window as "not responding" while the layout is being drawn for the first time.
        QApplication.processEvents()
        self.layout_view.show_result(result)
        # Drain again so the repaint lands before the fade animation starts.
        QApplication.processEvents()
        self._fade_in_widget(self.layout_view, duration=380, start_opacity=0.0)
        # Show zoom controls inside canvas now that there is a result to zoom into
        if hasattr(self, "_zoom_widget"):
            was_hidden = not self._zoom_widget.isVisible()
            self._zoom_widget.show()
            if hasattr(self, "_canvas_overlay_filter"):
                self._canvas_overlay_filter._reposition()
            # Gently fade the zoom controls in the first time they appear.
            if was_hidden:
                self._fade_in_widget(self._zoom_widget, duration=320, start_opacity=0.0)

        # T1-5: After the fit-to-view settles, smooth-scroll to sheet #1 so
        # the user always lands on the first result rather than wherever
        # they happened to be scrolled before.
        try:
            sheet_count = len(getattr(result, "sheet_layouts", []) or [])
            missing_count = len(getattr(result, "missing_sheet_layouts", []) or [])
        except Exception:
            sheet_count = missing_count = 0
        if sheet_count + missing_count > 0:
            QTimer.singleShot(220, self.layout_view.focus_first_sheet)

        # T1-3: rebuild the floating sheet-navigator chip bar.
        self._rebuild_sheet_nav(sheet_count, missing_count)

        missing_parts = len(result.unplaced_sheet_parts)
        missing_sheets = len(result.missing_sheet_layouts)
        placed_parts = sum(len(layout.parts) for layout in result.sheet_layouts)
        requested_parts = placed_parts + missing_parts
        used = len(result.sheet_layouts)
        # Realistic cutting metrics (saw travel, cut count, time incl. rotation
        # and handling).  Full per-format breakdown lands in the PDF report.
        cut_count = sum(getattr(layout, "cut_count", 0) for layout in result.sheet_layouts)
        saw_suffix = ""
        cut_time_suffix = ""
        display_utilization = result.utilization
        try:
            from algorithms.cut_metrics import compute_cut_summary
            from algorithms.layout_scoring import format_cut_time
            summary = compute_cut_summary(result)
            self._last_cut_summary = summary
            cut_count = summary.total_cuts
            if summary.total_board_area_m2 > 0:
                display_utilization = summary.total_area_m2 / summary.total_board_area_m2 * 100.0
            saw_suffix = f" | Piła: {summary.total_saw_m:.1f} mb"
            cut_time_suffix = f" | Czas: {format_cut_time(summary.total_time_s)}"
        except Exception:
            _logger.exception("Cut-metric summary failed")
            self._last_cut_summary = None
        available_stock = self._available_stock_quantity()
        smart_result = self._last_smart_stock_mode or any(
            str(message).startswith("Inteligentny dobór formatów:")
            for message in (getattr(result, "messages", []) or [])
        )
        smart_mix_summary = ""
        if smart_result:
            format_counts = Counter(
                (round(float(layout.stock.width)), round(float(layout.stock.height)))
                for layout in (getattr(result, "sheet_layouts", []) or [])
            )
            smart_mix_summary = ", ".join(
                f"{quantity}×{width}×{height}"
                for (width, height), quantity in sorted(format_counts.items())
            )
        if hasattr(self, "preview_util_value"):
            total_layouts = len(result.sheet_layouts) + len(getattr(result, "missing_sheet_layouts", []) or [])
            total_parts = sum(len(layout.parts) for layout in result.sheet_layouts + getattr(result, "missing_sheet_layouts", []))
            self.preview_util_value.setText(f"{display_utilization:.1f}%")
            self.preview_sheet_value.setText(str(total_layouts))
            self.preview_part_value.setText(str(total_parts))
            self.preview_waste_value.setText(f"{max(0.0, 100.0 - display_utilization):.1f}%")

        def _build_status(pct: float) -> str:
            sheet_status = (
                f"Płyty: {used} · AUTO {smart_mix_summary}"
                if smart_result
                else f"Płyty: {used} na {available_stock}"
            )
            return (
                f"Policzone | {sheet_status} | "
                f"Wykorzystanie: {pct:.1f}% | Cięcia: {cut_count}"
                f"{saw_suffix}{cut_time_suffix} | Jednostki: mm"
            )

        self._animate_status_utilization(display_utilization, _build_status)
        if missing_parts:
            self.warning.setText(
                f"Brakuje {missing_sheets} dodatkowych płyt na {missing_parts} formatek. "
                f"Z dostępnych płyt wyjdzie {placed_parts} z {requested_parts} formatek. "
                "Czerwone płyty pokazują brakujący materiał."
            )
            self.warning.show()
        else:
            self.warning.hide()

    def _set_calculating(self, calculating: bool) -> None:
        self._is_calculating = calculating
        enabled = not calculating
        for action in (
            self.calc_action,
            self.add_action,
            self.remove_action,
            self.save_project_action,
            self.png_action,
        ):
            action.setEnabled(enabled)
        for button in self.toolbar_buttons + self.part_buttons:
            button.setEnabled(enabled)
        if hasattr(self, "calculate_button"):
            self.calculate_button.setEnabled(enabled)
            self.calculate_button.setText("Liczenie..." if calculating else "Oblicz rozkrój")
            if calculating:
                self._start_pulse(self.calculate_button)
            else:
                self._stop_pulse(self.calculate_button)
        self.calc_action.setText("Liczenie..." if calculating else "Oblicz")
        for widget in (
            self.sheet_width,
            self.sheet_height,
            self.sheet_qty,
            self.sheet_allowance,
            self.min_reusable_offcut,
            self.recent_sheet_formats,
            self.stock_table,
            self.display_orientation,
            self.parts,
        ):
            widget.setEnabled(enabled)
        for button in self.stock_buttons:
            button.setEnabled(enabled)

    @safe_ui_action("Nie udało się wyeksportować PNG.")
    def export_png(self) -> None:
        if self._is_exporting:
            self.statusBar().showMessage("Eksport PNG już trwa.")
            return
        if not self.last_result:
            try:
                parts = self._collect_parts()
                self._apply_result(optimize_sheet_project(self._project_for_calculation(parts)))
            except Exception as exc:
                QMessageBox.warning(self, "Nie mogę policzyć", str(exc))
                return
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz rozkrój jako PNG", "", "PNG (*.png)")
        if not path:
            self._is_exporting = False
            self.png_action.setEnabled(not self._is_calculating)
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        export_options = self._ask_png_export_options()
        if export_options is None:
            self._is_exporting = False
            self.png_action.setEnabled(not self._is_calculating)
            return
        client_name, export_mode = export_options
        current_theme = self.layout_view.theme
        current_print_mode = self.layout_view.print_mode
        self._is_exporting = True
        self.png_action.setEnabled(False)
        try:
            self.layout_view.set_theme("light")
            self.layout_view.set_print_mode(export_mode == "economy")
            self.layout_view.show_result(self.last_result)
            export_scene_png(self.layout_view.scene, path, background="#ffffff", header_text=self._png_header_text(client_name))
        finally:
            self.layout_view.set_print_mode(current_print_mode)
            self.layout_view.set_theme(current_theme)
            self.layout_view.show_result(self.last_result)
            self._is_exporting = False
            self.png_action.setEnabled(not self._is_calculating)
        self.statusBar().showMessage(f"Zapisano {path}")

    def current_theme(self) -> str:
        return str(self.theme.currentData() or "dark")

    def current_display_orientation(self) -> str:
        return str(self.display_orientation.currentData() or "horizontal")

    # ── Algorithm settings gear ──────────────────────────────────────────────

    @safe_ui_action("Nie udało się otworzyć ustawień algorytmu.")
    def _open_algo_settings(self) -> None:
        dialog = AlgorithmSettingsDialog(self, self._algo_settings)
        result = dialog.exec()
        if dialog.tutorial_requested:
            QTimer.singleShot(0, self.open_tutorial)
            return
        if result == QDialog.DialogCode.Accepted:
            self._algo_settings = dialog.result_settings()
            repositories.set_setting("default_kerf", float(self._algo_settings.get("kerf", 5.0)))
            repositories.set_setting("saw_feed_m_per_min", float(self._algo_settings.get("saw_feed_m_per_min", 12.0)))
            repositories.set_setting("calculation_animation_mode", str(self._algo_settings.get("animation_mode", "economy")))
            self._update_algo_btn()

    def _update_algo_btn(self) -> None:
        """Refresh the gear button label to reflect the active algorithm mode."""
        if not hasattr(self, "_algo_btn"):
            return
        mode = self._algo_settings.get("optimization_mode", "comfort")
        cutting = self._algo_settings.get("cutting_mode", "hybrid")
        prio = self._algo_settings.get("mode", "minimize_waste")

        # Build a compact summary for the top bar button and its tooltip.
        mode_label = "Sport" if mode == "sport" else "Komfort"
        extras: list[str] = []
        if prio == "minimize_number_of_sheets":
            extras.append("mniej płyt")
        elif prio == "minimize_cut_length":
            extras.append("mniej cięć")
        if cutting != "hybrid":
            _eng_labels = {"strip": "Pasy", "guillotine": "Gilotyna", "free": "Free"}
            extras.append(_eng_labels.get(cutting, cutting))
        if not self._algo_settings.get("allow_rotation_parts", True):
            extras.append("bez rotacji")
        kerf = float(self._algo_settings.get("kerf", 5.0))
        tol = float(self._algo_settings.get("kerf_tolerance", 0.2))
        extras.append(f"Kerf {kerf:g}+{tol:g}mm")
        suffix = ("  ·  " + "  ·  ".join(extras)) if extras else ""
        self._algo_btn.setText("Ustawienia")
        self._algo_btn.setToolTip(
            "Ustawienia algorytmu rozkroju\n"
            f"{mode_label}{suffix}"
        )

    def current_optimization_mode(self) -> str:
        return str(self._algo_settings.get("optimization_mode", "comfort"))

    def _set_theme_combo(self, theme: str) -> None:
        index = self.theme.findData("light" if theme == "light" else "dark")
        self.theme.setCurrentIndex(max(0, index))

    def _set_display_orientation_combo(self, orientation: str) -> None:
        normalized = orientation if orientation in {"horizontal", "vertical", "auto"} else "horizontal"
        index = self.display_orientation.findData(normalized)
        self.display_orientation.setCurrentIndex(max(0, index))

    @safe_ui_action("Nie udało się zmienić motywu.")
    def apply_selected_theme(self, *_ignored) -> None:
        theme = self.current_theme()

        # Apply the theme instantly (no heavy grab/overlay).  We hide the
        # central widget for one event-loop tick so Qt doesn't paint an ugly
        # intermediate state, then fade it back in from transparent.  This is
        # far cheaper than calling central.grab() which forces a full off-screen
        # render of the entire widget hierarchy.
        central = self.centralWidget()
        if central is not None:
            # Cancel any leftover opacity effect from a previous theme switch so
            # animations don't stack up and cause stuttering.
            old_effect = central.graphicsEffect()
            if old_effect is not None:
                central.setGraphicsEffect(None)

        apply_theme(QApplication.instance(), theme)
        apply_native_title_bar(self, theme)
        self.layout_view.set_theme(theme)
        self.layout_view.show_result(self.last_result)
        self._refresh_linked_pair_glows()
        repositories.set_setting("theme", theme)

        # Gentle fade-in: the new theme paints at full opacity but we mask it
        # with a QGraphicsOpacityEffect that quickly ramps 0 → 1.  Looks like a
        # smooth dissolve with essentially zero CPU cost.
        if central is not None and central.width() > 8:
            self._fade_in_widget(central, duration=200, start_opacity=0.0)

    @safe_ui_action("Nie udało się przełączyć motywu.")
    def toggle_theme(self) -> None:
        self._set_theme_combo("dark" if self.current_theme() == "light" else "light")
        self.apply_selected_theme()

    @safe_ui_action("Nie udało się zmienić orientacji podglądu.")
    def apply_display_orientation(self, *_ignored) -> None:
        orientation = self.current_display_orientation()
        self.layout_view.set_display_orientation(orientation)
        repositories.set_setting("display_orientation", orientation)

    def save_settings(self) -> None:
        repositories.set_setting("theme", self.current_theme())
        repositories.set_setting("sheet_allowance", self.sheet_allowance.value())
        repositories.set_setting("min_reusable_offcut_size", self.min_reusable_offcut.value())
        repositories.set_setting("display_orientation", self.current_display_orientation())
        repositories.set_setting("default_kerf", float(self._algo_settings.get("kerf", 5.0)))
        repositories.set_setting("saw_feed_m_per_min", float(self._algo_settings.get("saw_feed_m_per_min", 12.0)))
        repositories.set_setting("calculation_animation_mode", str(self._algo_settings.get("animation_mode", "economy")))
        self.statusBar().showMessage("Ustawienia zapisane")

    def show_about_dialog(self) -> None:
        AboutDialog(self).exec()

    # ── Ustawienia / wsparcie / aktualizacje ─────────────────────────────────
    @safe_ui_action("Nie udało się otworzyć ustawień.")
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.check_updates_requested.connect(lambda: self.check_for_updates(manual=True))
        dialog.exec()

    def _has_sendable_result(self) -> bool:
        result = self.last_result
        return result is not None and bool(
            getattr(result, "sheet_layouts", None) or getattr(result, "missing_sheet_layouts", None)
        )

    @safe_ui_action("Nie udało się otworzyć metryk cięcia.")
    def open_cut_info_dialog(self) -> None:
        if not self._has_sendable_result():
            QMessageBox.information(
                self, "Metryki cięcia",
                "Najpierw oblicz rozkrój, aby zobaczyć metryki i wycenę.",
            )
            return
        CutInfoDialog(
            self,
            self.last_result,
            float(self._algo_settings.get("saw_feed_m_per_min", 12.0)),
        ).exec()

    @safe_ui_action("Nie udało się przygotować wysyłki.")
    def open_send_dialog(self) -> None:
        dialog = SendDialog(
            self,
            self._has_sendable_result(),
            str(repositories.get_setting("last_send_action", "email")),
            str(repositories.get_setting("last_send_printer", "")),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        action = dialog.selected_action()
        printer = dialog.selected_printer()
        repositories.set_setting("last_send_action", action)
        if printer:
            repositories.set_setting("last_send_printer", printer)
        if dialog.should_save_project():
            self.save_project()
        self._do_send(action, skip_summary=dialog.should_skip_summary(), printer_name=printer)

    def _build_send_project(self) -> Project:
        project = self._project_from_current_inputs()
        if self.current_client_name:
            project.meta.client_name = self.current_client_name
        if self.current_project_notes:
            project.meta.notes = self.current_project_notes
        return project

    def _send_base_name(self) -> str:
        client = (self.current_client_name or "rozkroj").strip() or "rozkroj"
        safe = "".join(ch for ch in client if ch.isalnum() or ch in " -_").strip().replace(" ", "_")
        return f"rozkroj_{safe or 'klient'}_{datetime.now():%Y%m%d_%H%M}"

    def _write_send_pdf(self, target: Path, skip_summary: bool = False) -> list[Path]:
        from import_export.pdf_report import generate_pdf
        project = self._build_send_project()
        return generate_pdf(
            target, project, self.last_result,
            company_name="SIEKACZ 9000",
            skip_summary=skip_summary,
            display_orientation=self.current_display_orientation()
        )

    @safe_ui_action("Nie udało się otworzyć podglądu PDF.")
    def open_temporary_pdf_preview(self) -> None:
        if not self._has_sendable_result():
            QMessageBox.information(self, "Podgląd PDF", "Najpierw oblicz rozkrój.")
            return
        import tempfile

        preview_dir = Path(tempfile.gettempdir()) / "SIEKACZ9000" / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        target = preview_dir / f"{self._send_base_name()}_podglad.pdf"
        self._write_send_pdf(target, skip_summary=False)
        PdfPreviewDialog(target, self).exec()

    @safe_ui_action("Nie udało się zapisać DXF.")
    def _write_send_dxf(self) -> None:
        from import_export.dxf_io import DxfError, export_layout_dxf

        last_dir = str(repositories.get_setting("last_export_dir", "") or "")
        base = self._send_base_name()
        initial_path = str(Path(last_dir) / f"{base}.dxf") if last_dir else f"{base}.dxf"
        path_str, _ = QFileDialog.getSaveFileName(self, "Zapisz rozkrój DXF", initial_path, "Plik DXF (*.dxf)")
        if not path_str:
            return
        try:
            target = export_layout_dxf(Path(path_str), self.last_result)
        except DxfError as exc:
            QMessageBox.warning(self, "Eksport DXF", str(exc))
            return
        repositories.set_setting("last_export_dir", str(target.parent))
        self.statusBar().showMessage(f"Zapisano DXF: {target.name}", 5000)

    @safe_ui_action("Nie udało się wysłać rozkroju.")
    def _do_send(self, action: str, skip_summary: bool = False, printer_name: str = "") -> None:
        import os
        import tempfile

        base = self._send_base_name()
        if action == "dxf":
            QMessageBox.information(
                self,
                "Eksport DXF",
                "Jeszcze nad tym pracuję, ale chyba idzie nieźle.",
            )
            return
        if action == "pdf":
            last_dir = str(repositories.get_setting("last_export_dir", ""))
            initial_path = os.path.join(last_dir, base + ".pdf") if last_dir else base + ".pdf"
            path_str, _ = QFileDialog.getSaveFileName(
                self, "Zapisz raport PDF", initial_path, "Plik PDF (*.pdf)"
            )
            if not path_str:
                return
            target = Path(path_str)
            repositories.set_setting("last_export_dir", str(target.parent))
            if target.suffix.lower() != ".pdf":
                target = target.with_suffix(".pdf")
            files = self._write_send_pdf(target, skip_summary=skip_summary)
            extra = f" (+{len(files) - 1} plik kontynuacji)" if len(files) > 1 else ""
            self.statusBar().showMessage(f"Zapisano PDF: {target.name}{extra}")
            try:
                os.startfile(str(target))  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                pass
            return

        # email / print both need the PDF in a temp folder first.
        temp_dir = Path(tempfile.gettempdir()) / "SIEKACZ9000"
        temp_dir.mkdir(parents=True, exist_ok=True)
        target = temp_dir / f"{base}.pdf"
        files = self._write_send_pdf(target, skip_summary=skip_summary)

        if action == "print":
            try:
                import subprocess
                # If a specific printer is selected, set it as default temporarily
                if printer_name:
                    # Get current default printer to restore later
                    current_ps = subprocess.run(
                        [
                            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-CimInstance -ClassName Win32_Printer -Filter 'Default = True').Name",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    current_printer = current_ps.stdout.strip()
                    
                    if current_printer and current_printer != printer_name:
                        # Pass printer names through the environment.  A name
                        # may legally contain quotes; interpolating it into a
                        # PowerShell program was both fragile and injectable.
                        printer_env = os.environ.copy()
                        printer_env["SIEKACZ_PRINTER_NAME"] = printer_name
                        set_printer = [
                            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "(New-Object -ComObject WScript.Network).SetDefaultPrinter($env:SIEKACZ_PRINTER_NAME)",
                        ]
                        subprocess.run(
                            set_printer,
                            check=True,
                            env=printer_env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        
                        # Print
                        os.startfile(str(target), "print")  # type: ignore[attr-defined]
                        
                        # Wait a bit for spooler to catch it before restoring
                        import time
                        time.sleep(2.0)
                        
                        # Restore default
                        restore_env = os.environ.copy()
                        restore_env["SIEKACZ_PRINTER_NAME"] = current_printer
                        subprocess.run(
                            set_printer,
                            check=False,
                            env=restore_env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    else:
                        os.startfile(str(target), "print")  # type: ignore[attr-defined]
                else:
                    os.startfile(str(target), "print")  # type: ignore[attr-defined]
                    
                self.statusBar().showMessage(f"Wysłano do drukarki: {printer_name or 'domyślna'}")
            except Exception as exc:
                # Fall back to just opening the PDF so the user can print manually.
                try:
                    os.startfile(str(target))  # type: ignore[attr-defined]
                except (OSError, AttributeError):
                    pass
                QMessageBox.information(
                    self, "Wydruk",
                    "Otworzyłem PDF — użyj Ctrl+P, aby wydrukować.\n"
                    f"(Automatyczny wydruk niedostępny: {exc})",
                )
            return

        # action == "email"
        from app.mailer import compose_email, send_via_smtp
        from database.repositories import get_setting

        subject = f"Rozkrój — {self.current_client_name or 'SIEKACZ 9000'}"
        body = (
            "W załączniku raport rozkroju (PDF) z programu SIEKACZ 9000.\n\n"
            f"Klient: {self.current_client_name or '—'}\n"
            f"Data: {datetime.now():%Y-%m-%d %H:%M}\n"
        )
        if len(files) > 1:
            body += f"\nUwaga: raport podzielono na {len(files)} plików PDF (kontynuacja).\n"
            
        config = get_setting("smtp_config")
        if config and isinstance(config, dict) and config.get("host") and config.get("user") and config.get("password"):
            try:
                send_via_smtp(subject, body, str(target), config)
                self.statusBar().showMessage("Wysłano e-mail pomyślnie przez wbudowanego klienta SMTP.")
                return
            except Exception as e:
                QMessageBox.warning(self, "Błąd wysyłania e-maila", f"Nie udało się wysłać przez SMTP: {e}\n\nSprawdź ustawienia.")
                return

        mode = compose_email(subject, body, attachment=str(target))
        if mode == "mapi":
            self.statusBar().showMessage("Otwarto program pocztowy z załączonym PDF.")
        else:
            self.statusBar().showMessage("Otwarto program pocztowy — załącz zapisany PDF ręcznie.")

    # ── Aktualizacje (GitHub Releases) ───────────────────────────────────────
    def _start_update_check(self) -> None:
        """Background update check fired shortly after startup.

        Skipped under the 'offscreen' platform (headless / CI / automated tests)
        so test processes never leave a live network QThread that would crash at
        interpreter teardown — and so test runs don't phone home.
        """
        app = QApplication.instance()
        if app is not None and app.platformName() == "offscreen":
            return
        self._run_update_check(manual=False)

    def _stop_update_thread(self) -> None:
        """Quit and join any in-flight update thread (called on app quit / close)."""
        thread = self._update_thread
        if thread is None:
            return
        try:
            if thread.isRunning():
                thread.quit()
                thread.wait(3000)
        except RuntimeError:
            pass

    @safe_ui_action("Nie udało się sprawdzić aktualizacji.")
    def check_for_updates(self, manual: bool = False) -> None:
        self._run_update_check(manual=manual)

    def _dialog_parent(self) -> QWidget:
        """Parent transient message boxes to the topmost modal dialog (e.g. the
        open 'Pomoc' window) so they never appear *behind* it — which looked
        exactly like a freeze (two modal widgets blocking each other)."""
        modal = QApplication.activeModalWidget()
        return modal if modal is not None else self

    def _run_update_check(self, manual: bool) -> None:
        """Start a *non-blocking* update check on its own QThread.

        Hardened against the freeze that happened before: we never spawn a second
        check while one is running (which previously overwrote — and destroyed —
        a still-running QThread), the worker network call has its own timeout,
        all work happens off the UI thread, and result pop-ups are parented to
        the active modal dialog so they cannot hide behind it.
        """
        from app import updater

        if not updater.is_configured():
            if manual:
                QMessageBox.information(
                    self._dialog_parent(), "Aktualizacje",
                    "Sprawdzanie aktualizacji nie jest jeszcze skonfigurowane.\n"
                    "Ustaw GITHUB_OWNER i GITHUB_REPO w pliku app/updater.py.",
                )
            return

        existing = self._update_thread
        if existing is not None:
            try:
                if existing.isRunning():
                    if manual:
                        self.statusBar().showMessage("Sprawdzanie aktualizacji już trwa...", 3000)
                    return
            except RuntimeError:
                # The C++ QThread was already deleted — safe to start a new one.
                self._update_thread = None

        self._update_manual = manual
        self.statusBar().showMessage("Sprawdzanie aktualizacji...")

        thread = QThread(self)
        worker = updater.UpdateCheckWorker()
        worker.moveToThread(thread)
        self._update_thread = thread
        self._update_worker = worker

        thread.started.connect(worker.run)
        worker.update_available.connect(self._on_update_available)
        worker.no_update.connect(self._on_no_update)
        worker.update_available.connect(thread.quit)
        worker.no_update.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_update_refs)
        thread.start()

    def _clear_update_refs(self) -> None:
        self._update_thread = None
        self._update_worker = None

    def _on_no_update(self) -> None:
        self.statusBar().showMessage("Masz najnowszą wersję.", 3000)
        if getattr(self, "_update_manual", False):
            QMessageBox.information(self._dialog_parent(), "Aktualizacje", "Masz najnowszą wersję programu.")

    def _on_update_available(self, info) -> None:
        self._pending_update = info
        if hasattr(self, "update_button"):
            self.update_button.setText(f"Aktualizacja {info.tag}")
            self.update_button.show()
        self.statusBar().showMessage(f"Dostępna nowa wersja: {info.tag}", 6000)
        # If the user explicitly asked (from the Pomoc dialog), open the changelog
        # straight away, parented to the active modal so it can't hide behind it.
        if getattr(self, "_update_manual", False):
            dialog = UpdateDialog(self._dialog_parent(), info)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._install_update(info)

    def _show_pending_update(self) -> None:
        info = getattr(self, "_pending_update", None)
        if info is None:
            self.check_for_updates(manual=True)
            return
        dialog = UpdateDialog(self, info)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._install_update(info)

    @safe_ui_action("Nie udało się zainstalować aktualizacji.")
    def _install_update(self, info) -> None:
        from app import updater
        if not getattr(info, "asset_url", ""):
            QDesktopServices.openUrl(QUrl(info.html_url))
            return
        self.statusBar().showMessage("Pobieranie aktualizacji...")
        QApplication.processEvents()
        try:
            path = updater.download_asset(info)
        except Exception as exc:
            QMessageBox.warning(self, "Aktualizacja", f"Nie udało się pobrać aktualizacji:\n{exc}")
            return
        try:
            updater.launch_downloaded_update(path)
        except (OSError, AttributeError) as exc:
            QMessageBox.warning(
                self, "Aktualizacja",
                f"Pobrano instalator, ale nie udało się go uruchomić:\n{path}\n{exc}",
            )
            return
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Prevent accidental data loss when the window is closed.

        * While a calculation is running: ask if the user really wants to quit
          (the optimizer thread will be abandoned — results discarded).
        * Otherwise: close immediately without prompting.  The current inputs
          survive in the database's recent-formats list and the user can always
          undo/redo changes made this session.
        """
        if self._is_calculating:
            reply = QMessageBox.question(
                self,
                "Obliczenia w toku",
                "Trwają obliczenia rozkroju.\n\n"
                "Czy na pewno chcesz zamknąć program?\n"
                "Bieżące obliczenia zostaną przerwane i wynik nie zostanie zapisany.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # Kill the calculation process before Qt starts tearing down widgets.
            worker = self._calculation_worker
            if worker is not None and hasattr(worker, "cancel"):
                worker.cancel()
            thread = self._calculation_thread
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2500)
        # Let any in-flight update check finish so its QThread (a child of this
        # window) is not destroyed while still running — that would crash Qt.
        self._stop_update_thread()
        event.accept()
