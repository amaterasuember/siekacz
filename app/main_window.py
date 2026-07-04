from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolBar,
)

from app.state import AppState
from app.theme import apply_button_cursors, apply_native_title_bar, apply_theme
from core.models import LinearStock, SheetStock
from core.validation import validate_linear_parts, validate_linear_stock, validate_sheet_parts, validate_sheet_stock
from database import repositories
from import_export.csv_io import read_csv, write_csv
from import_export.image_export import export_scene_png
from import_export.pdf_report import generate_pdf
from import_export.xlsx_io import read_xlsx, write_xlsx
from ui.layout_view import LayoutView
from ui.leftovers_panel import LeftoversPanel
from ui.optimizer_panel import OptimizerPanel
from ui.parts_panel import PartsPanel
from ui.project_panel import ProjectPanel
from ui.reports_panel import ReportsPanel
from ui.settings_panel import SettingsPanel
from ui.stock_panel import StockPanel
from workers.optimizer_worker import OptimizerWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SIEKACZ 9000")
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1400, 900)
        self.state = AppState()
        self.worker_thread: QThread | None = None
        self.worker: OptimizerWorker | None = None

        self.project_panel = ProjectPanel()
        self.stock_panel = StockPanel()
        self.parts_panel = PartsPanel()
        self.optimizer_panel = OptimizerPanel()
        self.layout_view = LayoutView()
        self.reports_panel = ReportsPanel()
        self.leftovers_panel = LeftoversPanel()
        self.settings_panel = SettingsPanel()

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tab_indexes = {
            "project": 0,
            "stock": 1,
            "parts": 2,
            "optimization": 3,
            "preview": 4,
            "reports": 5,
            "leftovers": 6,
            "settings": 7,
        }
        for label, panel in (
            ("Projekt", self.project_panel),
            ("Materiał", self.stock_panel),
            ("Formatki", self.parts_panel),
            ("Optymalizacja", self.optimizer_panel),
            ("Podgląd", self.layout_view),
            ("Raporty", self.reports_panel),
            ("Odpady", self.leftovers_panel),
            ("Ustawienia", self.settings_panel),
        ):
            self.tabs.addTab(panel, label)
        self.setCentralWidget(self.tabs)

        self._build_actions()
        self._connect_panels()
        self._load_initial_project()
        self._sync_to_ui()
        self._theme_changed(repositories.get_setting("theme", "dark"), persist=False)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start(60_000)
        self.statusBar().showMessage("Gotowe")
        apply_button_cursors(self)

    def _build_actions(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&Plik")
        import_menu = menu.addMenu("&Import")
        export_menu = menu.addMenu("&Eksport")
        tools_menu = menu.addMenu("&Narzędzia")

        toolbar = QToolBar("Główne")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.new_action = QAction("Nowy", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.open_action = QAction("Otwórz", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.save_action = QAction("Zapisz", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_as_action = QAction("Zapisz jako", self)
        self.run_action = QAction("Oblicz", self)
        self.export_pdf_action = QAction("Raport PDF", self)
        self.export_png_action = QAction("Podgląd PNG", self)
        self.load_leftovers_action = QAction("Użyj dostępnych odpadów jako materiału", self)

        for action in (self.new_action, self.open_action, self.save_action, self.save_as_action):
            file_menu.addAction(action)
            toolbar.addAction(action)
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu("Ostatnie projekty")
        file_menu.addSeparator()
        exit_action = QAction("Zamknij", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        for label, callback in (
            ("Płyty CSV", lambda: self._import_table("sheet_stock", "csv")),
            ("Pręty CSV", lambda: self._import_table("linear_stock", "csv")),
            ("Formatki CSV", lambda: self._import_table("sheet_parts", "csv")),
            ("Elementy liniowe CSV", lambda: self._import_table("linear_parts", "csv")),
            ("Płyty XLSX", lambda: self._import_table("sheet_stock", "xlsx")),
            ("Pręty XLSX", lambda: self._import_table("linear_stock", "xlsx")),
            ("Formatki XLSX", lambda: self._import_table("sheet_parts", "xlsx")),
            ("Elementy liniowe XLSX", lambda: self._import_table("linear_parts", "xlsx")),
        ):
            action = QAction(label, self)
            action.triggered.connect(callback)
            import_menu.addAction(action)

        export_menu.addAction(self.export_pdf_action)
        export_menu.addAction(QAction("Tabele projektu XLSX", self, triggered=self.export_xlsx))
        export_menu.addAction(QAction("Formatki CSV", self, triggered=self.export_parts_csv))
        export_menu.addAction(self.export_png_action)
        tools_menu.addAction(self.load_leftovers_action)
        toolbar.addSeparator()
        toolbar.addAction(self.run_action)
        toolbar.addAction(self.export_pdf_action)
        toolbar.addAction(self.export_png_action)

        self.new_action.triggered.connect(self.new_project)
        self.open_action.triggered.connect(self.open_project)
        self.save_action.triggered.connect(self.save_project)
        self.save_as_action.triggered.connect(self.save_project_as)
        self.run_action.triggered.connect(self.run_optimization)
        self.export_pdf_action.triggered.connect(self.export_pdf)
        self.export_png_action.triggered.connect(self.export_png)
        self.load_leftovers_action.triggered.connect(self.use_leftovers_as_stock)
        self._refresh_recent_menu()

    def _connect_panels(self) -> None:
        self.optimizer_panel.optimize_requested.connect(self.run_optimization)
        self.optimizer_panel.cancel_requested.connect(self.cancel_optimization)
        self.reports_panel.export_pdf_requested.connect(self.export_pdf)
        self.reports_panel.export_xlsx_requested.connect(self.export_xlsx)
        self.reports_panel.export_png_requested.connect(self.export_png)
        self.settings_panel.theme_changed.connect(self._theme_changed)
        self.settings_panel.display_orientation_changed.connect(self._display_orientation_changed)

    def _load_initial_project(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "sample_data" / "sample_project.json"
        if sample.exists():
            self.state.load(sample)

    def _sync_to_ui(self) -> None:
        project = self.state.project
        self.project_panel.set_meta(project.meta)
        self.stock_panel.set_stock(project.sheet_stock, project.linear_stock)
        self.parts_panel.set_parts(project.sheet_parts, project.linear_parts)
        self.optimizer_panel.set_settings(project.settings)
        self.settings_panel.set_sheet_allowance(project.settings.sheet_allowance or repositories.get_setting("sheet_allowance", 0.0))
        self.settings_panel.set_min_reusable_offcut(
            project.settings.min_reusable_offcut_size or repositories.get_setting("min_reusable_offcut_size", 200.0)
        )
        display_orientation = project.settings.display_orientation or repositories.get_setting("display_orientation", "horizontal")
        self.settings_panel.set_display_orientation(display_orientation)
        self.layout_view.set_display_orientation(display_orientation)
        self.layout_view.show_result(self.state.last_result)
        self._refresh_recent_menu()

    def _collect_from_ui(self) -> None:
        self.state.project.meta = self.project_panel.meta()
        self.state.project.sheet_stock = self.stock_panel.sheet_stock()
        self.state.project.linear_stock = self.stock_panel.linear_stock()
        self.state.project.sheet_parts = self.parts_panel.sheet_parts()
        self.state.project.linear_parts = self.parts_panel.linear_parts()
        settings = self.optimizer_panel.settings()
        settings.sheet_allowance = self.settings_panel.sheet_allowance_value()
        settings.min_reusable_offcut_size = self.settings_panel.min_reusable_offcut_value()
        settings.display_orientation = self.settings_panel.display_orientation_value()
        self.state.project.settings = settings

    def _validate_current(self) -> list[str]:
        project = self.state.project
        errors = []
        errors.extend(validate_sheet_stock(project.sheet_stock))
        errors.extend(validate_linear_stock(project.linear_stock))
        if project.settings.job_type == "sheet":
            allowance = max(0.0, float(project.settings.sheet_allowance or 0.0))
            validation_stock = [
                replace(stock, width=stock.width + allowance, height=stock.height + allowance)
                for stock in project.sheet_stock
            ]
            errors.extend(validate_sheet_parts(project.sheet_parts, validation_stock))
        else:
            errors.extend(validate_linear_parts(project.linear_parts, project.linear_stock))
        return errors

    def new_project(self) -> None:
        self.state.new_project()
        self._sync_to_ui()
        self.statusBar().showMessage("Utworzono nowy projekt")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Cut projects (*.json);;All files (*.*)")
        if not path:
            return
        self.state.load(path)
        self._sync_to_ui()
        self.statusBar().showMessage(f"Otworzono {path}")

    def save_project(self) -> None:
        self._collect_from_ui()
        if not self.state.project_path:
            self.save_project_as()
            return
        path = self.state.save()
        self.statusBar().showMessage(f"Zapisano {path}")
        self._refresh_recent_menu()

    def save_project_as(self) -> None:
        self._collect_from_ui()
        path, _ = QFileDialog.getSaveFileName(self, "Save project as", "", "Cut projects (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        saved = self.state.save(path)
        self.statusBar().showMessage(f"Zapisano {saved}")
        self._refresh_recent_menu()

    def autosave(self) -> None:
        self._collect_from_ui()
        self.state.autosave()
        self.statusBar().showMessage("Autozapis", 3000)

    def _refresh_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        for item in repositories.recent_projects():
            action = QAction(f"{item['path']}  ({item.get('client_name') or 'projekt'})", self)
            action.triggered.connect(lambda checked=False, path=item["path"]: self._open_recent(path))
            self.recent_menu.addAction(action)

    def _open_recent(self, path: str) -> None:
        if not Path(path).exists():
            QMessageBox.warning(self, "Missing project", f"The project file no longer exists:\n{path}")
            return
        self.state.load(path)
        self._sync_to_ui()

    def run_optimization(self) -> None:
        self._collect_from_ui()
        errors = self._validate_current()
        if errors:
            QMessageBox.warning(self, "Błędy walidacji", "\n".join(errors[:12]))
            return
        self.optimizer_panel.set_running(True)
        self.optimizer_panel.set_progress(0, "Start")
        self.worker_thread = QThread(self)
        self.worker = OptimizerWorker(self.state.project)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.optimizer_panel.set_progress)
        self.worker.finished.connect(self._optimization_finished)
        self.worker.failed.connect(self._optimization_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(lambda: self.optimizer_panel.set_running(False))
        self.worker_thread.start()
        self.tabs.setCurrentIndex(self.tab_indexes["optimization"])

    def cancel_optimization(self) -> None:
        if self.worker:
            self.worker.cancel()
        self.statusBar().showMessage("Wysłano prośbę o przerwanie")

    def _optimization_finished(self, result) -> None:
        self.state.last_result = result
        self.optimizer_panel.show_result(result)
        self.layout_view.show_result(result)
        repositories.save_leftovers(result.reusable_offcuts)
        self.leftovers_panel.refresh()
        self.statusBar().showMessage("Obliczenia zakończone")

    def _optimization_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Optymalizacja nie powiodła się", message)
        self.statusBar().showMessage("Optymalizacja nie powiodła się")

    def export_pdf(self) -> None:
        self._collect_from_ui()
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj raport PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        company = repositories.get_setting("company_name", "")
        generate_pdf(path, self.state.project, self.state.last_result, company)
        self.statusBar().showMessage(f"Eksportowano PDF: {path}")

    def export_xlsx(self) -> None:
        self._collect_from_ui()
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj tabele XLSX", "", "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        write_xlsx(
            path,
            {
                "Płyty": [vars(x) for x in self.state.project.sheet_stock],
                "Pręty": [vars(x) for x in self.state.project.linear_stock],
                "Formatki płytowe": [vars(x) for x in self.state.project.sheet_parts],
                "Formatki liniowe": [vars(x) for x in self.state.project.linear_parts],
                "Odpady użyteczne": self.state.last_result.reusable_offcuts if self.state.last_result else [],
            },
        )
        self.statusBar().showMessage(f"Eksportowano XLSX: {path}")

    def export_parts_csv(self) -> None:
        self._collect_from_ui()
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj formatki CSV", "", "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        write_csv(path, [vars(x) for x in self.state.project.sheet_parts])
        self.statusBar().showMessage(f"Eksportowano CSV: {path}")

    def export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj rozkrój PNG", "", "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        current_theme = self.layout_view.theme
        current_print_mode = self.layout_view.print_mode
        try:
            self.layout_view.set_theme("light")
            self.layout_view.set_print_mode(True)
            self.layout_view.show_result(self.state.last_result)
            export_scene_png(self.layout_view.scene, path, background="#ffffff")
        finally:
            self.layout_view.set_print_mode(current_print_mode)
            self.layout_view.set_theme(current_theme)
            self.layout_view.show_result(self.state.last_result)
        self.statusBar().showMessage(f"Eksportowano PNG: {path}")

    def _import_table(self, target: str, file_type: str) -> None:
        filters = "CSV files (*.csv)" if file_type == "csv" else "Excel workbook (*.xlsx)"
        path, _ = QFileDialog.getOpenFileName(self, "Import table", "", filters)
        if not path:
            return
        rows = read_csv(path) if file_type == "csv" else read_xlsx(path)
        if target == "sheet_stock":
            self.stock_panel.sheet_table.load_dicts(rows)
            self.tabs.setCurrentIndex(self.tab_indexes["stock"])
        elif target == "linear_stock":
            self.stock_panel.linear_table.load_dicts(rows)
            self.tabs.setCurrentIndex(self.tab_indexes["stock"])
        elif target == "sheet_parts":
            self.parts_panel.sheet_table.load_dicts(rows)
            self.tabs.setCurrentIndex(self.tab_indexes["parts"])
        elif target == "linear_parts":
            self.parts_panel.linear_table.load_dicts(rows)
            self.tabs.setCurrentIndex(self.tab_indexes["parts"])
        self.statusBar().showMessage(f"Zaimportowano {Path(path).name}")

    def use_leftovers_as_stock(self) -> None:
        self._collect_from_ui()
        leftovers = repositories.list_leftovers("available")
        sheet_added = 0
        linear_added = 0
        for item in leftovers:
            if item["type"] == "sheet" and item.get("width") and item.get("height"):
                self.state.project.sheet_stock.append(
                    SheetStock(
                        material=item["material"],
                        thickness=float(item.get("thickness") or 0),
                        width=float(item["width"]),
                        height=float(item["height"]),
                        quantity=1,
                        price=0,
                        source="leftover",
                    )
                )
                sheet_added += 1
            if item["type"] == "linear" and item.get("length"):
                self.state.project.linear_stock.append(
                    LinearStock(
                        material=item["material"],
                        profile=item.get("profile") or "Offcut",
                        length=float(item["length"]),
                        quantity=1,
                        price=0,
                        source="leftover",
                    )
                )
                linear_added += 1
        self._sync_to_ui()
        self.statusBar().showMessage(f"Dodano odpady do materiału: płyty {sheet_added}, liniowe {linear_added}")

    def _theme_changed(self, theme: str, persist: bool = True) -> None:
        normalized = "light" if theme == "light" else "dark"
        apply_theme(QApplication.instance(), normalized)
        apply_native_title_bar(self, normalized)
        self.layout_view.set_theme(normalized)
        self.layout_view.show_result(self.state.last_result)
        if persist:
            repositories.set_setting("theme", normalized)

    def _display_orientation_changed(self, orientation: str) -> None:
        normalized = orientation if orientation in {"horizontal", "vertical", "auto"} else "horizontal"
        self.layout_view.set_display_orientation(normalized)
        self.layout_view.show_result(self.state.last_result)
        repositories.set_setting("display_orientation", normalized)

    def backup_project_next_to(self, destination: str) -> None:
        if self.state.project_path:
            shutil.copy2(self.state.project_path, destination)
