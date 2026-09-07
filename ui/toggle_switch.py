from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        self._pos = 0.0
        self.setFixedSize(46, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_knob_pos(self) -> float:
        return self._pos

    def _set_knob_pos(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = False) -> None:
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pos = target
            self.update()
        self.toggled.emit(self._checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked, animate=True)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.setChecked(not self._checked, animate=True)
            event.accept()
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        t = self._pos

        track = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        app = QApplication.instance()
        theme = app.property("theme") if app is not None else "dark"
        is_light = theme == "light"

        if is_light:
            off_color = QColor(210, 215, 225)
            on_color = QColor(59, 130, 246)
            border_color = QColor(0, 0, 0, 30)
            knob_color = QColor(255, 255, 255)
        else:
            off_color = QColor(20, 30, 48)
            on_color = QColor(37, 99, 235)
            border_color = QColor(255, 255, 255, 45)
            knob_color = QColor(240, 245, 255)

        track_color = self._mix(off_color, on_color, t)
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, track.height() / 2.0, track.height() / 2.0)

        if t > 0 and not is_light:
            glow_color = QColor(90, 167, 255, int(100 * t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow_color)
            painter.drawRoundedRect(track.adjusted(1, 1, -1, -1), track.height() / 2.0, track.height() / 2.0)

        margin = 3.0
        diameter = h - 2.0 * margin
        knob_x = margin + (w - 2.0 * margin - diameter) * t
        knob_rect = QRectF(knob_x, margin, diameter, diameter)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 50))
        painter.drawEllipse(knob_rect.translated(0, 1))
        
        painter.setPen(QPen(border_color, 0.5))
        painter.setBrush(knob_color)
        painter.drawEllipse(knob_rect)
