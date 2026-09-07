from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class CuttingModeSwitch(QWidget):
    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = "comfort"
        self._knob_position = 0.0
        self._animation = QPropertyAnimation(self, b"knobPosition", self)
        self._animation.setDuration(320)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(232, 42)
        self.setMaximumHeight(44)
        self.setToolTip("COMFORT: Najbardziej optymalny, SPORT: Eksperymentalny, agresywniejszy.")

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, animated: bool = True) -> None:
        normalized = "sport" if mode == "sport" else "comfort"
        if normalized == self._mode and self._knob_position == (1.0 if normalized == "sport" else 0.0):
            return
        self._mode = normalized
        target = 1.0 if normalized == "sport" else 0.0
        if animated:
            self._animation.stop()
            self._animation.setStartValue(self._knob_position)
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._knob_position = target
            self.update()
        self.mode_changed.emit(self._mode)

    def knob_position(self) -> float:
        return self._knob_position

    def set_knob_position(self, value: float) -> None:
        self._knob_position = max(0.0, min(1.0, float(value)))
        self.update()

    knobPosition = Property(float, knob_position, set_knob_position)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_mode("sport" if event.position().x() >= self.width() / 2 else "comfort")
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_C):
            self.set_mode("comfort")
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_S, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.set_mode("sport" if self._mode == "comfort" else "comfort")
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        app = QApplication.instance()
        theme = app.property("theme") if app is not None else "dark"
        is_light = theme == "light"

        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        radius = rect.height() / 2
        painter.setPen(QPen(QColor(15, 23, 42, 32) if is_light else QColor(190, 215, 255, 38), 1.0))
        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if is_light:
            background.setColorAt(0.0, QColor(255, 255, 255, 245))
            background.setColorAt(1.0, QColor(241, 245, 249, 235))
        else:
            background.setColorAt(0.0, QColor(255, 255, 255, 26))
            background.setColorAt(1.0, QColor(255, 255, 255, 8))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, radius, radius)

        half = rect.width() / 2
        knob_w = half - 5
        knob_x = rect.x() + 3 + self._knob_position * (half - 1)
        knob = QRectF(knob_x, rect.y() + 4, knob_w, rect.height() - 8)
        if self._mode == "sport":
            knob_start = QColor(255, 92, 110)
            knob_end = QColor(220, 38, 38)
            glow = QColor(239, 68, 68, 58)
        else:
            knob_start = QColor(90, 167, 255)
            knob_end = QColor(37, 99, 235)
            glow = QColor(90, 167, 255, 45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(knob.adjusted(-5, -3, 5, 3), knob.height() / 2, knob.height() / 2)
        knob_gradient = QLinearGradient(knob.topLeft(), knob.bottomRight())
        knob_gradient.setColorAt(0.0, knob_start)
        knob_gradient.setColorAt(1.0, knob_end)
        painter.setBrush(knob_gradient)
        painter.drawRoundedRect(knob, knob.height() / 2, knob.height() / 2)

        font = QFont("Segoe UI", 8)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        comfort_rect = QRectF(rect.x(), rect.y(), half, rect.height())
        sport_rect = QRectF(rect.x() + half, rect.y(), half, rect.height())
        inactive = QColor(71, 84, 103) if is_light else QColor(155, 174, 204)
        painter.setPen(QColor(255, 255, 255) if self._mode == "comfort" else inactive)
        painter.drawText(comfort_rect, Qt.AlignmentFlag.AlignCenter, "COMFORT")
        painter.setPen(QColor(255, 255, 255) if self._mode == "sport" else inactive)
        painter.drawText(sport_rect, Qt.AlignmentFlag.AlignCenter, "SPORT")

        painter.setPen(QPen(QColor(15, 23, 42, 32) if is_light else QColor(190, 215, 255, 38), 1))
        mid_x = rect.x() + half
        painter.drawLine(int(mid_x), int(rect.y() + 10), int(mid_x), int(rect.bottom() - 10))
