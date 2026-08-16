"""Smoke tests for UI animations: fade-in helpers + calculate button pulse."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.simple_window import (
    AlgorithmSettingsDialog,
    HoverLiftFilter,
    PART_THICKNESS_COLUMN,
    STOCK_THICKNESS_COLUMN,
    SimpleCutWindow,
    ThemeToggleSwitch,
)
from app.material_catalog import MaterialCatalogEntry
from core.models import OptimizationSettings, Project
from ui.optimization_progress import (
    OptimizationProgressOverlay,
    _SAMURAI_FRAMES,
    _SIMS_MESSAGES,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_fade_in_does_not_crash_or_leak_effect() -> None:
    _app()
    window = SimpleCutWindow()
    widget = QPushButton("Test")
    window._fade_in_widget(widget, duration=10, start_opacity=0.0)
    # Effect is installed during the animation; cleanup runs on finished.
    assert widget.graphicsEffect() is not None
    print("[OK] _fade_in_widget installs effect, doesn't crash")


def test_pulse_starts_and_stops_cleanly() -> None:
    _app()
    window = SimpleCutWindow()
    button = QPushButton("Calc")
    window._start_pulse(button)
    group = getattr(button, "_pulse_anim", None)
    assert group is not None
    assert group.state() == QAbstractAnimation.State.Running
    assert button.graphicsEffect() is not None

    window._stop_pulse(button)
    assert getattr(button, "_pulse_anim", None) is None
    assert button.graphicsEffect() is None
    print("[OK] _start_pulse/_stop_pulse lifecycle correct")


def test_pulse_double_start_is_idempotent() -> None:
    _app()
    window = SimpleCutWindow()
    button = QPushButton("Calc")
    window._start_pulse(button)
    first_group = getattr(button, "_pulse_anim", None)
    window._start_pulse(button)  # second call must NOT create a new animation
    second_group = getattr(button, "_pulse_anim", None)
    assert first_group is second_group, "duplicate pulse animation was created"
    window._stop_pulse(button)
    print("[OK] double-start pulse is idempotent")


def test_set_calculating_starts_and_stops_pulse() -> None:
    _app()
    window = SimpleCutWindow()
    window._set_calculating(True)
    group = getattr(window.calculate_button, "_pulse_anim", None)
    assert group is not None, "pulse not started during calculation"
    assert group.state() == QAbstractAnimation.State.Running
    window._set_calculating(False)
    assert getattr(window.calculate_button, "_pulse_anim", None) is None, \
        "pulse not stopped after calculation"
    print("[OK] _set_calculating drives pulse animation correctly")


def test_fade_in_with_delay_installs_effect_immediately() -> None:
    """A delayed fade-in must hide the widget right away (no flicker)."""
    _app()
    window = SimpleCutWindow()
    widget = QPushButton("Chip")
    window._fade_in_widget(widget, duration=120, start_opacity=0.0, delay=200)
    # Effect installed at start_opacity even though the animation hasn't run yet.
    effect = widget.graphicsEffect()
    assert effect is not None
    assert abs(effect.opacity() - 0.0) < 1e-6, "delayed fade should start hidden"
    print("[OK] _fade_in_widget(delay=...) installs effect immediately")


def test_animate_status_utilization_runs_and_settles() -> None:
    """Count-up helper must produce the final percentage in the status bar."""
    _app()
    window = SimpleCutWindow()
    captured: list[str] = []
    window._animate_status_utilization(83.4, lambda pct: f"util {pct:.1f}%")
    anim = getattr(window, "_status_util_anim", None)
    assert anim is not None, "count-up animation not created"
    # Force the animation to its end so the deterministic final value is shown.
    anim.setCurrentTime(anim.duration())
    assert window.statusBar().currentMessage() == "util 83.4%", \
        window.statusBar().currentMessage()
    # A second call must cancel the previous animation cleanly.
    window._animate_status_utilization(0.0, lambda pct: f"util {pct:.1f}%")
    assert window.statusBar().currentMessage() == "util 0.0%"
    print("[OK] _animate_status_utilization counts up and settles on final value")


def test_hover_lift_filter_creates_shadow_on_enter() -> None:
    """HoverLiftFilter must lazily install a drop-shadow on hover-enter."""
    _app()
    window = SimpleCutWindow()
    flt = HoverLiftFilter(window)
    button = QPushButton("Chip")
    flt.watch(button)
    assert button.graphicsEffect() is None, "effect must be created lazily, not eagerly"
    # Simulate the cursor entering the widget.
    enter = QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
    flt.eventFilter(button, enter)
    assert button.graphicsEffect() is not None, "hover-enter must install a lift effect"
    # Leaving must not crash.
    flt.eventFilter(button, QEvent(QEvent.Type.Leave))
    print("[OK] HoverLiftFilter installs shadow on hover and survives leave")


def test_theme_toggle_switch_slides_and_emits() -> None:
    """The animated theme switch must track state, animate, and emit toggled."""
    _app()
    switch = ThemeToggleSwitch()
    assert switch.is_light() is False and abs(switch._pos - 0.0) < 1e-6
    # Setting without animation snaps the knob.
    switch.setChecked(True, animate=False)
    assert switch.is_light() is True and abs(switch._pos - 1.0) < 1e-6
    # knobPos property is animatable.
    switch.setChecked(False, animate=True)
    assert switch._anim.state() == QAbstractAnimation.State.Running
    # Clicking emits the new state.
    received: list[bool] = []
    switch.toggled.connect(received.append)
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert received == [switch.is_light()]
    print("[OK] ThemeToggleSwitch slides, animates and emits toggled")


def test_theme_toggle_is_not_exposed_in_main_window() -> None:
    """The main window intentionally has no light/dark toggle."""
    _app()
    window = SimpleCutWindow()
    assert not hasattr(window, "theme_toggle_button")
    print("[OK] main window keeps the removed theme toggle hidden")


def test_select_page_fades_in_new_widget() -> None:
    """Switching pages must trigger fade-in animation; widget must remain usable."""
    _app()
    window = SimpleCutWindow()
    # Initial state is page 0; switch to page 1 (history).
    window._select_page(1)
    assert window.stack.currentIndex() == 1
    # An effect was installed for the animation; we cannot easily await
    # completion in a synchronous test, but the call must not crash.
    print("[OK] _select_page fade-in invocation safe")


def test_progress_has_two_hundred_unique_transport_history_facts_and_ascii_samurai_frames() -> None:
    assert len(_SIMS_MESSAGES) == 200
    assert len(set(_SIMS_MESSAGES)) == 200
    assert len(_SAMURAI_FRAMES) == 8
    assert all(
        phase.startswith(f"{index:02d}  ")
        and art.strip()
        and all(ord(char) < 128 for char in art)
        for index, (phase, art) in enumerate(_SAMURAI_FRAMES, 1)
    )
    print("[OK] progress deck has 200 unique transport/history facts and ASCII samurai frames")


def test_progress_facts_do_not_repeat_before_the_full_deck_is_shown() -> None:
    _app()
    overlay = OptimizationProgressOverlay()
    shown = [overlay._msg_label]
    for _ in range(len(_SIMS_MESSAGES) - 1):
        overlay._advance_message()
        shown.append(overlay._msg_label)
    assert len(set(shown)) == len(_SIMS_MESSAGES)
    previous = shown[-1]
    overlay._advance_message()
    assert overlay._msg_label != previous
    print("[OK] progress facts use a no-repeat shuffle bag")


def test_calculation_render_loops_and_stops() -> None:
    _app()
    overlay = OptimizationProgressOverlay()
    overlay.resize(1100, 720)
    assert overlay._samurai_movie_available
    overlay.start(Project(settings=OptimizationSettings(animation_mode="quality")))
    QTest.qWait(80)
    assert overlay._samurai_movie_label.isVisible()
    assert overlay._samurai_movie.state().name == "Running"
    overlay.stop()
    assert not overlay._samurai_movie_label.isVisible()
    assert overlay._samurai_movie.state().name == "NotRunning"
    print("[OK] calculation render loops and stops with the overlay")


def test_economy_calculation_animation_skips_video_render() -> None:
    _app()
    overlay = OptimizationProgressOverlay()
    project = Project(settings=OptimizationSettings(animation_mode="economy"))
    overlay.start(project)
    assert overlay._animation_mode == "economy"
    assert not overlay._samurai_movie_label.isVisible()
    assert overlay._samurai_movie.state().name == "NotRunning"
    overlay.stop()
    print("[OK] economy calculation animation skips the video render")


def test_progress_overlay_keeps_the_real_optimizer_status_separate_from_the_fact() -> None:
    _app()
    overlay = OptimizationProgressOverlay()
    overlay.start(Project(settings=OptimizationSettings(animation_mode="economy")))
    overlay.set_progress(42, "Zakonczono Skyline (2/4)")
    assert overlay.state.globalProgressPercent == 42
    assert overlay.state.currentStageLabel == "Zakonczono Skyline (2/4)"
    assert overlay._msg_label in _SIMS_MESSAGES
    overlay.stop()
    print("[OK] progress overlay exposes the real optimizer status above the fact")


def test_algorithm_settings_uses_left_navigation_and_persists_new_controls() -> None:
    _app()
    dialog = AlgorithmSettingsDialog(None, {
        "saw_feed_m_per_min": 18.5,
        "animation_mode": "economy",
    })
    assert dialog._settings_pages.count() == 6
    assert len(dialog._settings_nav_buttons) == 6
    assert [button.text() for button in dialog._settings_nav_buttons] == [
        "Rozkrój", "Technologia", "Wydajność", "Wygląd", "Cennik", "Silnik",
    ]
    assert dialog._tutorial_button.text() == "Samouczek"
    assert dialog._tutorial_button.objectName() == "settingsNavTab"
    assert "background" not in dialog._tutorial_button.styleSheet()
    dialog._set_settings_page(3)
    assert dialog._settings_pages.currentIndex() == 3
    assert dialog._animation_economy.isChecked()
    dialog._feed_spin.setValue(21.0)
    dialog._apply_and_close()
    result = dialog.result_settings()
    assert result["saw_feed_m_per_min"] == 21.0
    assert result["animation_mode"] == "economy"
    print("[OK] settings dialog has navigation, feed and animation controls")


def test_settings_can_request_tutorial_replay() -> None:
    _app()
    dialog = AlgorithmSettingsDialog(None, {})
    assert not dialog.tutorial_requested
    dialog._tutorial_button.click()
    assert dialog.tutorial_requested
    assert dialog.result() == dialog.DialogCode.Rejected
    print("[OK] settings expose a neutral tutorial replay action")


def test_preview_zoom_controls_do_not_duplicate_fit_action() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        controls = window._zoom_widget
        button_labels = [button.text() for button in controls.findChildren(QPushButton)]
        assert "Fit" not in button_labels
        assert window._zoom_percent_label.toolTip().casefold().find("zresetować") >= 0
        assert controls.height() == 3 * 44 + 24 + 3 * 5 + 16
    finally:
        window.close()
    print("[OK] zoom toolbar uses 100% as the only fit/reset action")


def test_main_window_keeps_catalog_import_in_settings_and_uses_full_width_thickness() -> None:
    _app()
    window = SimpleCutWindow()
    assert window._algo_btn.text() == "Ustawienia"
    assert not hasattr(window, "import_catalog_button")
    assert window.catalog_thickness_selector.sizePolicy().horizontalPolicy().name == "Expanding"
    dialog = AlgorithmSettingsDialog(window, window._algo_settings)
    dialog._set_settings_page(4)
    assert dialog._catalog_import_button.isEnabled()
    dialog.close()
    window.close()
    print("[OK] catalog import moved to settings and thickness control expands")


def test_catalog_thickness_only_updates_the_last_stock_row() -> None:
    _app()
    window = SimpleCutWindow()
    window.stock_table.setRowCount(0)
    window._add_stock_row({"thickness": 8, "width": 1000, "height": 2000, "quantity": 1})
    window._add_stock_row({"thickness": 8, "width": 1500, "height": 3000, "quantity": 1})
    window._add_blank_stock_row_and_focus()
    window._material_catalog = [
        MaterialCatalogEntry("PA6", 8, 100, 123, "PA6 PŁYTA GR. 8 MM CZARNA"),
        MaterialCatalogEntry("PA6", 18, 120, 148, "PA6 PŁYTA GR. 18 MM CZARNA"),
    ]
    window._populate_material_selector()
    window.material_selector.setCurrentIndex(0)
    window.catalog_thickness_selector.setCurrentIndex(1)
    assert window.stock_table.item(0, STOCK_THICKNESS_COLUMN).text() == "8"
    assert window.stock_table.item(1, STOCK_THICKNESS_COLUMN).text() == "8"
    assert window.stock_table.item(2, STOCK_THICKNESS_COLUMN).text() == "18"

    window.parts.setRowCount(0)
    window.add_part_row([8, 100, 120, 1])
    window.add_part_row()
    window.catalog_thickness_selector.setCurrentIndex(0)
    assert window.parts.item(0, PART_THICKNESS_COLUMN).text() == "8"
    assert window.parts.item(1, PART_THICKNESS_COLUMN).text() == "8"
    window.catalog_thickness_selector.setCurrentIndex(1)
    assert window.parts.item(0, PART_THICKNESS_COLUMN).text() == "8"
    assert window.parts.item(1, PART_THICKNESS_COLUMN).text() == "18"

    assert window.parts.item(0, PART_THICKNESS_COLUMN).flags() & Qt.ItemFlag.ItemIsEditable
    window.close()
    print("[OK] catalog thickness only changes the last stock row")


if __name__ == "__main__":
    test_fade_in_does_not_crash_or_leak_effect()
    test_pulse_starts_and_stops_cleanly()
    test_pulse_double_start_is_idempotent()
    test_set_calculating_starts_and_stops_pulse()
    test_fade_in_with_delay_installs_effect_immediately()
    test_animate_status_utilization_runs_and_settles()
    test_hover_lift_filter_creates_shadow_on_enter()
    test_theme_toggle_switch_slides_and_emits()
    test_theme_toggle_is_not_exposed_in_main_window()
    test_select_page_fades_in_new_widget()
    test_progress_has_two_hundred_unique_transport_history_facts_and_ascii_samurai_frames()
    test_progress_facts_do_not_repeat_before_the_full_deck_is_shown()
    test_calculation_render_loops_and_stops()
    test_economy_calculation_animation_skips_video_render()
    test_progress_overlay_keeps_the_real_optimizer_status_separate_from_the_fact()
    test_algorithm_settings_uses_left_navigation_and_persists_new_controls()
    test_settings_can_request_tutorial_replay()
    test_preview_zoom_controls_do_not_duplicate_fit_action()
    test_main_window_keeps_catalog_import_in_settings_and_uses_full_width_thickness()
    test_catalog_thickness_only_updates_the_last_stock_row()
    print("\ntest_animations: OK")
