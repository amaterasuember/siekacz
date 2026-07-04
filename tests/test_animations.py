"""Smoke tests for UI animations: fade-in helpers + calculate button pulse."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.simple_window import HoverLiftFilter, SimpleCutWindow, ThemeToggleSwitch


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


def test_theme_toggle_button_is_switch_and_syncs() -> None:
    """The window must use the animated switch and keep it in sync with theme."""
    _app()
    window = SimpleCutWindow()
    assert isinstance(window.theme_toggle_button, ThemeToggleSwitch)
    window._set_theme_combo("light")
    window._update_theme_toggle_button()
    assert window.theme_toggle_button.is_light() is True
    window._set_theme_combo("dark")
    window._update_theme_toggle_button()
    assert window.theme_toggle_button.is_light() is False
    print("[OK] window theme switch stays in sync with current theme")


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


if __name__ == "__main__":
    test_fade_in_does_not_crash_or_leak_effect()
    test_pulse_starts_and_stops_cleanly()
    test_pulse_double_start_is_idempotent()
    test_set_calculating_starts_and_stops_pulse()
    test_fade_in_with_delay_installs_effect_immediately()
    test_animate_status_utilization_runs_and_settles()
    test_hover_lift_filter_creates_shadow_on_enter()
    test_theme_toggle_switch_slides_and_emits()
    test_theme_toggle_button_is_switch_and_syncs()
    test_select_page_fades_in_new_widget()
    print("\ntest_animations: OK")
