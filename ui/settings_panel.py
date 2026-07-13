from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLineEdit, QPushButton, QVBoxLayout, QWidget

from database import repositories


class SettingsPanel(QWidget):
    changed = Signal()
    theme_changed = Signal(str)
    display_orientation_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("glassPanel")

        self.unit = QComboBox()
        self.unit.addItems(["mm", "cm", "inches"])

        self.theme = QComboBox()
        self.theme.addItem("Ciemny", "dark")
        self.theme.addItem("Jasny", "light")

        self.display_orientation = QComboBox()
        self.display_orientation.addItem("Poziomo (wysokość płyty wzdłuż ekranu)", "horizontal")
        self.display_orientation.addItem("Pionowo", "vertical")
        self.display_orientation.addItem("Auto", "auto")

        self.default_kerf = QDoubleSpinBox()
        self.default_kerf.setRange(0, 50)
        self.default_kerf.setDecimals(2)
        self.default_kerf.setValue(3.0)

        self.default_margin = QDoubleSpinBox()
        self.default_margin.setRange(0, 200)
        self.default_margin.setDecimals(2)

        self.sheet_allowance = QDoubleSpinBox()
        self.sheet_allowance.setRange(0, 200)
        self.sheet_allowance.setDecimals(2)
        self.sheet_allowance.setSuffix(" mm")

        self.min_reusable_offcut = QDoubleSpinBox()
        self.min_reusable_offcut.setRange(0, 2000)
        self.min_reusable_offcut.setDecimals(2)
        self.min_reusable_offcut.setSuffix(" mm")

        self.default_material = QLineEdit()
        self.company_name = QLineEdit()
        self.logo_path = QLineEdit()
        self.export_folder = QLineEdit()

        save = QPushButton("Zapisz ustawienia")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save)

        cutting_form = QFormLayout()
        cutting_form.addRow("Jednostki", self.unit)
        cutting_form.addRow("Domyślny rzaz", self.default_kerf)
        cutting_form.addRow("Domyślny margines", self.default_margin)
        cutting_form.addRow("Naddatek płyty do obliczeń", self.sheet_allowance)
        cutting_form.addRow("Min. użyteczny odpad", self.min_reusable_offcut)
        cutting_form.addRow("Domyślny materiał", self.default_material)
        cutting_group = QGroupBox("Rozkrój")
        cutting_group.setObjectName("glassPanel")
        cutting_group.setLayout(cutting_form)

        appearance_form = QFormLayout()
        appearance_form.addRow("Motyw", self.theme)
        appearance_form.addRow("Orientacja podglądu", self.display_orientation)
        appearance_group = QGroupBox("Wygląd")
        appearance_group.setObjectName("glassPanel")
        appearance_group.setLayout(appearance_form)

        export_form = QFormLayout()
        export_form.addRow("Nazwa firmy do PDF", self.company_name)
        export_form.addRow("Ścieżka logo PDF", self.logo_path)
        export_form.addRow("Domyślny folder eksportu", self.export_folder)
        export_group = QGroupBox("Eksport")
        export_group.setObjectName("glassPanel")
        export_group.setLayout(export_form)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(cutting_group)
        layout.addWidget(appearance_group)
        layout.addWidget(export_group)
        layout.addWidget(save)
        layout.addStretch(1)

        self.load()
        self.theme.currentIndexChanged.connect(self._emit_theme_changed)
        self.display_orientation.currentIndexChanged.connect(self._emit_display_orientation_changed)
        self.sheet_allowance.valueChanged.connect(lambda *_: self.changed.emit())
        self.min_reusable_offcut.valueChanged.connect(lambda *_: self.changed.emit())

    def current_theme(self) -> str:
        return str(self.theme.currentData() or "dark")

    def display_orientation_value(self) -> str:
        return str(self.display_orientation.currentData() or "horizontal")

    def _set_theme(self, theme: str) -> None:
        index = self.theme.findData("light" if theme == "light" else "dark")
        self.theme.setCurrentIndex(max(0, index))

    def set_display_orientation(self, orientation: str) -> None:
        normalized = orientation if orientation in {"horizontal", "vertical", "auto"} else "horizontal"
        index = self.display_orientation.findData(normalized)
        self.display_orientation.blockSignals(True)
        self.display_orientation.setCurrentIndex(max(0, index))
        self.display_orientation.blockSignals(False)

    def _emit_theme_changed(self) -> None:
        self.theme_changed.emit(self.current_theme())
        self.changed.emit()

    def _emit_display_orientation_changed(self) -> None:
        self.display_orientation_changed.emit(self.display_orientation_value())
        self.changed.emit()

    def sheet_allowance_value(self) -> float:
        return self.sheet_allowance.value()

    def min_reusable_offcut_value(self) -> float:
        return self.min_reusable_offcut.value()

    def set_sheet_allowance(self, value: float) -> None:
        self.sheet_allowance.blockSignals(True)
        self.sheet_allowance.setValue(max(0.0, float(value or 0.0)))
        self.sheet_allowance.blockSignals(False)

    def set_min_reusable_offcut(self, value: float) -> None:
        self.min_reusable_offcut.blockSignals(True)
        self.min_reusable_offcut.setValue(max(0.0, float(value or 0.0)))
        self.min_reusable_offcut.blockSignals(False)

    def load(self) -> None:
        self.unit.setCurrentText(repositories.get_setting("unit", "mm"))
        self._set_theme(repositories.get_setting("theme", "dark"))
        self.set_display_orientation(repositories.get_setting("display_orientation", "horizontal"))
        self.default_kerf.setValue(float(repositories.get_setting("default_kerf", 3.0)))
        self.default_margin.setValue(float(repositories.get_setting("default_margin", 0.0)))
        self.sheet_allowance.setValue(float(repositories.get_setting("sheet_allowance", 0.0)))
        min_offcut = float(repositories.get_setting("min_reusable_offcut_size", 200.0))
        if min_offcut < 10.0:  # Fix for users who had the 0.0 bug saved in their DB
            min_offcut = 200.0
        self.min_reusable_offcut.setValue(min_offcut)
        self.default_material.setText(repositories.get_setting("default_material", ""))
        self.company_name.setText(repositories.get_setting("company_name", ""))
        self.logo_path.setText(repositories.get_setting("logo_path", ""))
        self.export_folder.setText(repositories.get_setting("export_folder", ""))

    def save(self) -> None:
        repositories.set_setting("unit", self.unit.currentText())
        repositories.set_setting("theme", self.current_theme())
        repositories.set_setting("display_orientation", self.display_orientation_value())
        repositories.set_setting("default_kerf", self.default_kerf.value())
        repositories.set_setting("default_margin", self.default_margin.value())
        repositories.set_setting("sheet_allowance", self.sheet_allowance.value())
        repositories.set_setting("min_reusable_offcut_size", self.min_reusable_offcut.value())
        repositories.set_setting("default_material", self.default_material.text())
        repositories.set_setting("company_name", self.company_name.text())
        repositories.set_setting("logo_path", self.logo_path.text())
        repositories.set_setting("export_folder", self.export_folder.text())
        self.changed.emit()
