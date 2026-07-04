from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ReportsPanel(QWidget):
    export_pdf_requested = Signal()
    export_xlsx_requested = Signal()
    export_png_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.summary = QLabel("Generuj raporty i eksportuj dane po uruchomieniu optymalizacji.")
        self.summary.setWordWrap(True)
        pdf = QPushButton("Eksportuj raport PDF")
        xlsx = QPushButton("Eksportuj tabele projektu do XLSX")
        png = QPushButton("Eksportuj rozkrój do PNG")
        pdf.clicked.connect(self.export_pdf_requested.emit)
        xlsx.clicked.connect(self.export_xlsx_requested.emit)
        png.clicked.connect(self.export_png_requested.emit)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(pdf)
        layout.addWidget(xlsx)
        layout.addWidget(png)
        layout.addStretch(1)

