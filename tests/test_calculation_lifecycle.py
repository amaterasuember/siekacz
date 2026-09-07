"""Real process cancellation and deferred Qt window shutdown regressions."""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from app.simple_window import CalculationWorker, SimpleCutWindow
from core.models import OptimizationSettings, Project, SheetPart, SheetStock


def project(quantity=1):
    return Project(
        sheet_stock=[SheetStock("M", 18, 1000, 2000, 100)],
        sheet_parts=[SheetPart("A", 120, 190, quantity, "M", 18)],
        settings=OptimizationSettings(kerf=5, multi_core=False),
    )


def wait_until(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Qt lifecycle did not complete"
        QApplication.processEvents()
        time.sleep(0.025)


def test_worker_cancel_and_success():
    app = QApplication.instance() or QApplication([])
    for cancel in (False, True):
        worker = CalculationWorker(project(5000 if cancel else 1))
        thread = QThread()
        worker.moveToThread(thread)
        events = []
        worker.finished.connect(lambda result, error: events.append((result, error)))
        worker.finished.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.start()
        try:
            if cancel:
                wait_until(lambda: worker._process is not None and worker._process.pid is not None)
                start = time.monotonic()
                worker.cancel()
                assert time.monotonic() - start < 0.1, "Cancel blocked the UI"
            wait_until(lambda: not thread.isRunning())
            assert len(events) == 1
            assert events[0][1] is None
            assert (events[0][0] is None) == cancel
            assert worker._process is None
        finally:
            worker.cancel()
            thread.quit()
            assert thread.wait(10000)
            worker.deleteLater()
            thread.deleteLater()
    assert app is not None


def test_window_close_waits_for_worker():
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    window.show()
    with patch("app.simple_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
        window._set_calculating(True)
        window._start_calculation_worker(project(5000))
        wait_until(lambda: window._calculation_worker is not None and window._calculation_worker._process is not None and window._calculation_worker._process.pid is not None)
        window.close()
        assert window._close_after_calculation
        assert window.isVisible(), "Window was destroyed before worker cleanup"
        wait_until(lambda: window._calculation_thread is None and not window.isVisible())
    assert app is not None



def test_window_waits_for_update_thread():
    from app import updater

    class SlowUpdate(QObject):
        update_available = Signal(object)
        no_update = Signal()
        def run(self):
            time.sleep(0.25)
            self.no_update.emit()

    window = SimpleCutWindow()
    window.show()
    with patch.object(updater, "UpdateCheckWorker", SlowUpdate), patch.object(updater, "is_configured", return_value=True):
        window._run_update_check(manual=False)
        window.close()
        assert window._close_after_update and window.isVisible()
        wait_until(lambda: window._update_thread is None and not window.isVisible())



def test_publisher_cancel_keeps_running_thread_alive():
    from app.publisher import PublisherDialog
    dialog = PublisherDialog(None)
    thread = QThread(dialog)
    dialog._publish_thread = thread
    thread.start()
    dialog.show()
    try:
        with patch("app.publisher.QMessageBox.warning"):
            dialog.reject()  # Escape and the Cancel button both call reject.
        assert dialog.isVisible()
    finally:
        thread.quit()
        assert thread.wait(2000)
        dialog.reject()
    assert not dialog.isVisible()



def test_first_async_result_renders_legend():
    from PySide6.QtWidgets import QGraphicsTextItem
    window = SimpleCutWindow()
    try:
        window._set_calculating(True)
        window._start_calculation_worker(project())
        wait_until(lambda: window._calculation_thread is None and window.last_result is not None)
        labels = [item.toPlainText() for item in window.layout_view.graphics_scene.items() if isinstance(item, QGraphicsTextItem)]
        assert any("120 × 190" in text and "szt." in text for text in labels)
        assert not window._is_calculating
    finally:
        window.close()


if __name__ == "__main__":
    mp.freeze_support()
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    test_first_async_result_renders_legend()
    print("[OK] first async result renders the legend", flush=True)
    test_worker_cancel_and_success()
    print("[OK] worker completion and responsive cancellation", flush=True)
    test_window_close_waits_for_worker()
    print("[OK] deferred window shutdown", flush=True)
    test_window_waits_for_update_thread()
    print("[OK] close during update check", flush=True)
    test_publisher_cancel_keeps_running_thread_alive()
    print("[OK] publisher cancel during background work", flush=True)
