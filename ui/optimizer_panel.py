from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.models import OptimizationResult, OptimizationSettings
from ui.cutting_mode_switch import CuttingModeSwitch


class OptimizerPanel(QWidget):
    optimize_requested = Signal()
    cancel_requested = Signal()
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.job_type = QComboBox()
        self.job_type.addItems(["sheet", "linear"])
        self.algorithm = QComboBox()
        self.optimization_mode = CuttingModeSwitch()
        self.cutting_mode = QComboBox()
        self.cutting_mode.addItem("Hybrid produkcyjny", "hybrid")
        self.cutting_mode.addItem("Pasy", "strip")
        self.cutting_mode.addItem("Gilotyna", "guillotine")
        self.cutting_mode.addItem("Free nesting (podglad)", "free")
        self.mode = QComboBox()
        self.mode.addItems(["minimize_waste", "minimize_number_of_sheets", "minimize_cut_length", "minimize_cost"])
        self.kerf = QDoubleSpinBox()
        self.kerf.setRange(0, 50)
        self.kerf.setDecimals(2)
        self.kerf.setValue(3.0)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 200)
        self.margin.setDecimals(2)
        self.margin.setValue(0.0)
        self.run_button = QPushButton("Oblicz rozkrój")
        self.run_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton("Przerwij")
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.status = QLabel("Gotowe")
        self.warning = QLabel("")
        self.warning.setObjectName("warningBanner")
        self.warning.setWordWrap(True)
        self.warning.hide()
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)

        form = QFormLayout()
        form.addRow("Typ zadania", self.job_type)
        form.addRow("Charakter pracy", self.optimization_mode)
        form.addRow("Algorytm", self.algorithm)
        form.addRow("Silnik cięcia", self.cutting_mode)
        form.addRow("Tryb optymalizacji", self.mode)
        form.addRow("Rzaz / kerf (mm)", self.kerf)
        form.addRow("Margines (mm)", self.margin)
        group = QGroupBox("Ustawienia optymalizacji")
        group.setObjectName("glassPanel")
        group.setLayout(form)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(group)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.warning)
        layout.addWidget(self.summary, 1)

        self.job_type.currentTextChanged.connect(self._refresh_algorithms)
        self.job_type.currentTextChanged.connect(lambda *_: self.changed.emit())
        self.algorithm.currentTextChanged.connect(lambda *_: self.changed.emit())
        self.optimization_mode.mode_changed.connect(lambda *_: self.changed.emit())
        self.cutting_mode.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.mode.currentTextChanged.connect(lambda *_: self.changed.emit())
        self.kerf.valueChanged.connect(lambda *_: self.changed.emit())
        self.margin.valueChanged.connect(lambda *_: self.changed.emit())
        self.run_button.clicked.connect(self.optimize_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self._refresh_algorithms(self.job_type.currentText())

    def _refresh_algorithms(self, job_type: str) -> None:
        current = self.algorithm.currentText()
        self.algorithm.clear()
        self.algorithm.addItems(
            ["First Fit Decreasing", "Best Fit Decreasing"]
            if job_type == "linear"
            else ["Vertical Segmented Guillotine", "MaxRects", "Guillotine", "Skyline"]
        )
        if current:
            index = self.algorithm.findText(current)
            if index >= 0:
                self.algorithm.setCurrentIndex(index)

    def set_settings(self, settings: OptimizationSettings) -> None:
        self.job_type.setCurrentText(settings.job_type)
        self._refresh_algorithms(settings.job_type)
        self.algorithm.setCurrentText(settings.algorithm)
        self.optimization_mode.set_mode(getattr(settings, "optimization_mode", "comfort"), animated=False)
        index = self.cutting_mode.findData(getattr(settings, "cutting_mode", "hybrid"))
        self.cutting_mode.setCurrentIndex(max(0, index))
        self.mode.setCurrentText(settings.mode)
        self.kerf.setValue(settings.kerf)
        self.margin.setValue(settings.margin)

    def settings(self) -> OptimizationSettings:
        return OptimizationSettings(
            job_type=self.job_type.currentText(),
            algorithm=self.algorithm.currentText(),
            optimization_mode=self.optimization_mode.mode(),
            cutting_mode=str(self.cutting_mode.currentData() or "hybrid"),
            mode=self.mode.currentText(),
            kerf=self.kerf.value(),
            margin=self.margin.value(),
        )

    def set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    def set_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.status.setText(message)

    def show_result(self, result: OptimizationResult) -> None:
        missing_parts = len(result.unplaced_sheet_parts)
        missing_sheets = len(result.missing_sheet_layouts)
        if missing_parts:
            self.warning.setText(f"Brakuje {missing_sheets} dodatkowych płyt / {missing_parts} formatek. Zobacz czerwone płyty w zakładce Podgląd.")
            self.warning.show()
        else:
            self.warning.hide()

        lines = [
            f"Algorytm: {result.algorithm}",
            f"Układy: {len(result.sheet_layouts) or len(result.linear_layouts)}",
            f"Dodatkowe płyty: {missing_sheets}",
            f"Wykorzystanie: {result.utilization:.1f}%",
            f"Cięcia: {sum(getattr(layout, 'cut_count', 0) for layout in result.sheet_layouts)}",
            f"Pasy: {sum(getattr(layout, 'strip_count', 0) for layout in result.sheet_layouts)}",
            "Układ wykonalny gilotynowo: "
            + ("TAK" if result.sheet_layouts and all(getattr(layout, "is_guillotine_feasible", False) for layout in result.sheet_layouts) else "NIE"),
            f"Odpad: {result.waste:.1f}",
            f"Fragmentacja odpadu: {getattr(result, 'fragmentation_score', 0.0):.0f}",
            f"Koszt szacunkowy: {result.total_cost:.2f}",
            f"Odpady wielorazowe: {len(result.reusable_offcuts)}",
        ]
        lines.extend(result.messages)
        self.summary.setPlainText("\n".join(lines))
