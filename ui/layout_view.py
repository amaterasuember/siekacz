from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from algorithms.layout_grouping import group_identical_layouts
from core.models import LinearLayout, OptimizationResult, SheetLayout, SheetStock


THEMES = {
    "dark": {
        "background": "#0a111c",
        "workspace": "#0a111c",
        "card": "#0c1524",
        "card_alt": "#111d30",
        "text": "#f2f6fb",
        "muted": "#9ba9bb",
        "border": "#22314a",
        "sheet": "#12243d",
        "sheet_pen": "#4b8cff",
        "offcut": QColor(22, 28, 38, 205),
        "offcut_pen": "#66758a",
        "success": "#6da7ff",
        "danger": "#ef4444",
        "danger_dark": "#7f1d1d",
    },
    "light": {
        "background": "#f6f7f9",
        "workspace": "#f8fafc",
        "card": "#ffffff",
        "card_alt": "#fbfdff",
        "text": "#1f2937",
        "muted": "#667085",
        "border": "#d0d7e2",
        "sheet": "#f8fafc",
        "sheet_pen": "#8a98ad",
        "offcut": QColor(226, 232, 240, 170),
        "offcut_pen": "#b6c2d2",
        "success": "#16a34a",
        "danger": "#dc2626",
        "danger_dark": "#991b1b",
    },
}

PART_COLOR_PALETTE = (
    ("#4ade80", "#22c55e", "#bbf7d0"),
    ("#38bdf8", "#0284c7", "#bae6fd"),
    ("#a78bfa", "#7c3aed", "#ddd6fe"),
    ("#facc15", "#ca8a04", "#fef08a"),
    ("#2dd4bf", "#0d9488", "#ccfbf1"),
    ("#60a5fa", "#2563eb", "#dbeafe"),
    ("#84cc16", "#65a30d", "#d9f99d"),
    ("#22d3ee", "#0891b2", "#cffafe"),
    ("#c084fc", "#9333ea", "#f3e8ff"),
    ("#34d399", "#059669", "#a7f3d0"),
    ("#818cf8", "#4f46e5", "#c7d2fe"),
    ("#eab308", "#a16207", "#fef9c3"),
)


def _stock_nominal_dimensions(stock: SheetStock) -> tuple[float, float]:
    return stock.nominal_width or stock.width, stock.nominal_height or stock.height


def _material_code(material: str) -> str:
    normalized = str(material or "").upper().replace("-", " ")
    if normalized.strip() in {"", "STANDARD", "MATERIAŁ", "MATERIAL"}:
        return ""
    tokens = normalized.split()
    if "PE" in normalized and "1000" in normalized:
        return "PE1000"
    if "PE" in normalized and "300" in normalized:
        return "PE300"
    if "PA6G" in normalized or "PA6 G" in normalized:
        return "PA6G"
    if "PA6" in normalized:
        return "PA6"
    if "POM" in normalized and "C" in tokens:
        return "POM-C"
    if "POM" in normalized and "H" in tokens:
        return "POM-H"
    if "POM" in normalized:
        return "POM"
    return "MAT"


def _material_badge_style(material: str) -> tuple[str, QColor, QColor]:
    normalized = str(material or "").upper()
    if "CZARN" in normalized:
        return _material_code(material), QColor("#202936"), QColor("#f3f7ff")
    if "ZIELON" in normalized:
        return _material_code(material), QColor("#188c63"), QColor("#effff8")
    if "NATUR" in normalized:
        return _material_code(material), QColor("#ffffff"), QColor("#111827")
    if "NIEBIESK" in normalized:
        return _material_code(material), QColor("#2777c9"), QColor("#eff8ff")
    return _material_code(material), QColor("#4b5f7d"), QColor("#f1f6ff")


def _dimension_key(width: float, height: float) -> tuple[float, float]:
    first, second = sorted((round(width, 2), round(height, 2)))
    return first, second


def _area_text(area: float) -> str:
    return f"{area:,.0f}".replace(",", " ") + " mm2"


def _resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative_path


class LayoutView(QGraphicsView):
    # Emitted whenever the effective view scale changes.  Argument is an
    # integer percentage where 100 = "fit-to-view" baseline.
    zoomChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("layoutView")
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.theme = "dark"
        self.display_orientation = "horizontal"
        self.print_mode = False
        self._current_result: OptimizationResult | None = None
        self._manual_zoom = 1.0
        self._auto_fit = True
        self._empty_state = True
        self._part_color_map: dict[tuple[float, float], int] = {}
        self._part_symbol_map: dict[tuple[float, float], str] = {}
        # Cache of (top, bottom) y-coordinates for every sheet card we draw —
        # used by T1-3 (sheet navigation) and T1-5 (focus_first_sheet).
        self._sheet_card_bounds: list[QRectF] = []
        # One nav-chip label per drawn card; identical boards are collapsed into a
        # single card with a ×N badge, so this can be shorter than the raw layout
        # count.  Consumed by SimpleCutWindow._rebuild_sheet_nav.
        self._nav_labels: list[str] = []
        # T1-4: per-dimension-key registry so clicking a legend marker can
        # highlight every matching part across every sheet.  Reset per render.
        self._part_rects_by_key: dict[tuple[float, float], list[QGraphicsRectItem]] = {}
        self._legend_markers_by_key: dict[tuple[float, float], list[QGraphicsRectItem]] = {}
        self._highlighted_part_key: tuple[float, float] | None = None
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setToolTip("Rolka przewija podgląd. Użyj przycisków lupy, żeby przybliżyć lub oddalić.")
        self.set_theme("dark")

    def set_theme(self, theme: str) -> None:
        self.theme = "light" if theme == "light" else "dark"
        self.setBackgroundBrush(QColor(THEMES[self.theme]["background"]))

    def set_display_orientation(self, orientation: str) -> None:
        normalized = orientation if orientation in {"horizontal", "vertical", "auto"} else "horizontal"
        if self.display_orientation == normalized:
            return
        self.display_orientation = normalized
        self.show_result(self._current_result)

    def set_print_mode(self, enabled: bool) -> None:
        if self.print_mode == enabled:
            return
        self.print_mode = enabled
        self.setBackgroundBrush(QColor("#ffffff") if enabled else QColor(THEMES[self.theme]["background"]))
        self.show_result(self._current_result)

    def _set_empty_interaction(self, enabled: bool) -> None:
        self._empty_state = enabled
        if enabled:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        # T1-4: if the user clicked a legend marker, toggle the highlight
        # for that dimension key instead of starting a drag.
        if (
            not self._empty_state
            and event.button() == Qt.MouseButton.LeftButton
        ):
            scene_pos = self.mapToScene(event.pos())
            for item in self.scene.items(scene_pos):
                if item.data(1) == "legend_marker":
                    key = item.data(2)
                    if key is not None:
                        self._toggle_part_highlight(tuple(key))
                        event.accept()
                        return
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # T1-4: legend-driven part highlighting.
    # ------------------------------------------------------------------
    _HIGHLIGHT_PEN_WIDTH = 3.5
    _DIM_OPACITY = 0.18

    def _toggle_part_highlight(self, key: tuple[float, float]) -> None:
        if self._highlighted_part_key == key:
            self._clear_part_highlight()
        else:
            self._highlight_part_key(key)

    def _highlight_part_key(self, key: tuple[float, float]) -> None:
        self._clear_part_highlight()
        if key not in self._part_rects_by_key:
            return
        self._highlighted_part_key = key
        # Dim *all* parts, then raise the matching set back to full strength.
        for k, rects in self._part_rects_by_key.items():
            for rect in rects:
                if k == key:
                    rect.setOpacity(1.0)
                    pen = QPen(QColor("#fde047"), self._HIGHLIGHT_PEN_WIDTH)
                    rect.setPen(pen)
                    rect.setZValue(50)
                else:
                    rect.setOpacity(self._DIM_OPACITY)
                    rect.setZValue(0)
        # Visually mark the active legend marker(s).
        for k, markers in self._legend_markers_by_key.items():
            for marker in markers:
                if k == key:
                    marker.setPen(QPen(QColor("#fde047"), 2.4))
                else:
                    marker.setOpacity(0.35)

    def _clear_part_highlight(self) -> None:
        if self._highlighted_part_key is None:
            return
        self._highlighted_part_key = None
        for rects in self._part_rects_by_key.values():
            for rect in rects:
                rect.setOpacity(1.0)
                orig = rect.data(0)
                if isinstance(orig, QPen):
                    rect.setPen(orig)
                rect.setZValue(0)
        for markers in self._legend_markers_by_key.values():
            for marker in markers:
                marker.setOpacity(1.0)
                orig = marker.data(0)
                if isinstance(orig, QPen):
                    marker.setPen(orig)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._empty_state:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event) -> None:
        if self._empty_state:
            event.accept()
            return
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        delta = pixel_delta if pixel_delta else angle_delta
        if delta:
            bar = self.verticalScrollBar()
            step = pixel_delta if pixel_delta else int(angle_delta / 120 * 72)
            bar.setValue(bar.value() - step)
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_in(self) -> None:
        self._zoom_by(1.18)

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.18)

    def reset_zoom(self) -> None:
        """Reset to fit-to-view (100% baseline) and re-centre on content."""
        self._manual_zoom = 1.0
        self._auto_fit = True
        self.fit(reset_zoom=True)
        self._emit_zoom()

    def _zoom_by(self, factor: float) -> None:
        if self._empty_state:
            return
        next_zoom = self._manual_zoom * factor
        if not 0.22 <= next_zoom <= 8.0:
            return
        self._manual_zoom = next_zoom
        self._auto_fit = False
        self.scale(factor, factor)
        self._emit_zoom()

    def _emit_zoom(self) -> None:
        """Emit ``zoomChanged`` with the current manual zoom as a percentage.

        ``_manual_zoom`` is the multiplier *on top of* the fit-to-view base
        scale, so 1.0 → 100%, 1.5 → 150%, 0.7 → 70%.
        """
        try:
            self.zoomChanged.emit(int(round(self._manual_zoom * 100)))
        except RuntimeError:
            pass

    def sheet_card_count(self) -> int:
        """Number of sheet cards currently rendered."""
        return len(self._sheet_card_bounds)

    def nav_chip_labels(self) -> list[str]:
        """One label per drawn card (identical boards collapsed, e.g. 'Płyta 1 ×10')."""
        return list(self._nav_labels)

    def focus_sheet(self, index: int, animated: bool = True) -> None:
        """Smoothly scroll so the *index*-th sheet card is centred in the viewport.

        Out-of-range indices clamp to the nearest valid card.  Used by the
        floating sheet navigator (T1-3) and by ``focus_first_sheet`` (T1-5).
        """
        if self._empty_state or not self._sheet_card_bounds:
            return
        clamped = max(0, min(index, len(self._sheet_card_bounds) - 1))
        target = self._sheet_card_bounds[clamped]
        scene_x = target.center().x()
        scene_y = target.center().y()
        if not animated:
            self.centerOn(scene_x, scene_y)
            return
        target_point = self.mapFromScene(scene_x, scene_y)
        viewport_center = self.viewport().rect().center()
        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()
        h_target = max(h_bar.minimum(), min(h_bar.maximum(), h_bar.value() + (target_point.x() - viewport_center.x())))
        v_target = max(v_bar.minimum(), min(v_bar.maximum(), v_bar.value() + (target_point.y() - viewport_center.y())))
        for bar, t in ((h_bar, h_target), (v_bar, v_target)):
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setDuration(260)
            anim.setStartValue(bar.value())
            anim.setEndValue(int(t))
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def focus_first_sheet(self, animated: bool = True) -> None:
        """Smoothly scroll to the top of the first sheet card.

        Used after a fresh calculation so the user always sees sheet #1
        rather than wherever they happened to be scrolled previously.
        """
        if self._empty_state or not self._sheet_card_bounds:
            return
        first = self._sheet_card_bounds[0]
        scene_x = first.center().x()
        scene_y = first.top() + 1.0  # tiny offset so the top edge isn't clipped
        if not animated:
            self.centerOn(scene_x, scene_y)
            return
        # Map target to viewport coords, then animate the scrollbars.
        target_point = self.mapFromScene(scene_x, scene_y)
        viewport_center = self.viewport().rect().center()
        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()
        h_target = max(h_bar.minimum(), min(h_bar.maximum(), h_bar.value() + (target_point.x() - viewport_center.x())))
        v_target = max(v_bar.minimum(), min(v_bar.maximum(), v_bar.value() + (target_point.y() - first.height() / 4 - viewport_center.y())))
        for bar, target in ((h_bar, h_target), (v_bar, v_target)):
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setDuration(260)
            anim.setStartValue(bar.value())
            anim.setEndValue(int(target))
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._empty_state:
            event.accept()
            return
        # Double-click clears any active legend highlight before re-fitting,
        # so the user can quickly reset both visual states with one gesture.
        self._clear_part_highlight()
        self.fit(reset_zoom=True)
        event.accept()

    def keyPressEvent(self, event) -> None:
        # T1-4: ESC clears the active legend highlight.
        if event.key() == Qt.Key.Key_Escape and self._highlighted_part_key is not None:
            self._clear_part_highlight()
            event.accept()
            return
        super().keyPressEvent(event)

    def _apply_rounded_mask(self) -> None:
        """Clip the view to rounded corners so the preview box matches the card.

        QGraphicsView paints its background over any QSS border-radius, leaving
        square corners; a rounded viewport mask gives the box genuinely round
        corners.
        """
        viewport = self.viewport()
        if viewport is None:
            return
        rect = viewport.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        radius = 20.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        viewport.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_rounded_mask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_rounded_mask()
        if self._empty_state:
            self.scene.clear()
            self._draw_empty_state()
            self._fit_empty_state()
            return
        if self._auto_fit and not self.scene.itemsBoundingRect().isEmpty():
            self.fit(reset_zoom=False)

    def fit(self, reset_zoom: bool = True) -> None:
        if self._empty_state:
            self._fit_empty_state()
            if reset_zoom:
                self._manual_zoom = 1.0
                self._auto_fit = True
            self._emit_zoom()
            return
        bounds = self.scene.itemsBoundingRect().adjusted(-12, -12, 12, 12)
        if not bounds.isEmpty():
            if reset_zoom:
                self._manual_zoom = 1.0
                self._auto_fit = True
            viewport_width = max(1, self.viewport().width() - 8)
            width_scale = viewport_width / max(1.0, bounds.width())
            base_scale = max(0.08, min(width_scale, 3.0))
            self.setSceneRect(bounds)
            self.resetTransform()
            self.scale(base_scale * self._manual_zoom, base_scale * self._manual_zoom)
            self.centerOn(bounds.center().x(), bounds.top())
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self._emit_zoom()

    def _empty_scene_bounds(self) -> QRectF:
        viewport_size = self.viewport().size()
        widget_size = self.size()
        width = max(640, viewport_size.width(), widget_size.width())
        height = max(360, viewport_size.height(), widget_size.height())
        return QRectF(0, 0, float(width), float(height))

    def _fit_empty_state(self) -> None:
        self.resetTransform()
        self.centerOn(self.scene.sceneRect().center())

    def _draw_empty_background_image(self, bounds: QRectF, relative_path: str, opacity: float = 1.0) -> bool:
        image = QPixmap(str(_resource_path(relative_path)))
        if image.isNull():
            return False
        target_width = max(1, int(bounds.width()))
        target_height = max(1, int(bounds.height()))
        scaled = image.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, int((scaled.width() - target_width) / 2))
        crop_y = max(0, int((scaled.height() - target_height) / 2))
        cropped = scaled.copy(crop_x, crop_y, target_width, target_height)
        rounded = QPixmap(target_width, target_height)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, target_width, target_height), 18, 18)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        item = self.scene.addPixmap(rounded)
        item.setOffset(bounds.x(), bounds.y())
        item.setOpacity(opacity)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setZValue(-30)
        return True

    def show_result(self, result: OptimizationResult | None) -> None:
        self._current_result = result
        self.scene.clear()
        self._manual_zoom = 1.0
        self._auto_fit = True
        self._part_color_map = {}
        self._part_symbol_map = {}
        self._sheet_card_bounds = []  # reset per-render
        self._nav_labels = []
        self._part_rects_by_key = {}
        self._legend_markers_by_key = {}
        self._highlighted_part_key = None
        if not result:
            self._set_empty_interaction(True)
            self._draw_empty_state()
            self._fit_empty_state()
            return
        self._set_empty_interaction(False)
        # Show supplemental boards under their matching material/thickness
        # group instead of collecting every missing board at the end.
        regular_by_spec: dict[tuple[str, float], list[SheetLayout]] = {}
        missing_by_spec: dict[tuple[str, float], list[SheetLayout]] = {}
        spec_order: list[tuple[str, float]] = []

        def spec_key(layout: SheetLayout) -> tuple[str, float]:
            stock = layout.stock
            material = str(getattr(stock, "material", "") or "standard").strip().casefold()
            thickness = round(float(getattr(stock, "thickness", 0.0) or 0.0), 3)
            return material, thickness

        def collect(
            layouts: list[SheetLayout],
            target: dict[tuple[str, float], list[SheetLayout]],
        ) -> None:
            for layout in layouts:
                key = spec_key(layout)
                if key not in target:
                    target[key] = []
                if key not in spec_order:
                    spec_order.append(key)
                target[key].append(layout)

        collect(list(result.sheet_layouts or []), regular_by_spec)
        collect(list(getattr(result, "missing_sheet_layouts", []) or []), missing_by_spec)

        y = 32.0
        first_section = True
        for key in spec_order:
            real_layouts = regular_by_spec.get(key, [])
            supplemental_layouts = missing_by_spec.get(key, [])
            if not first_section:
                y += 8.0
            if real_layouts:
                y = self._draw_sheet_layouts(real_layouts, y, missing=False)
            if supplemental_layouts:
                if real_layouts:
                    y += 8.0
                y = self._draw_sheet_layouts(supplemental_layouts, y, missing=True)
            first_section = False
        if result.linear_layouts:
            self._draw_linear_layouts(result.linear_layouts)
        self.fit(reset_zoom=True)

    def _palette(self) -> dict[str, object]:
        if self.print_mode:
            return {
                "background": "#ffffff",
                "workspace": "#ffffff",
                "card": "#ffffff",
                "card_alt": "#ffffff",
                "text": "#000000",
                "muted": "#111111",
                "border": "#000000",
                "sheet": "#ffffff",
                "sheet_pen": "#000000",
                "offcut": QColor("#ffffff"),
                "offcut_pen": "#000000",
                "success": "#000000",
                "danger": "#000000",
                "danger_dark": "#000000",
            }
        return THEMES[self.theme]

    def _draw_empty_state(self) -> None:
        bounds = self._empty_scene_bounds()
        self.scene.setSceneRect(bounds)

        if self.theme == "light" and not self.print_mode:
            if self._draw_empty_background_image(bounds, "assets/preview_empty_light.png", 1.0):
                return

        base = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        base.setColorAt(0.0, QColor(16, 30, 50, 238))
        base.setColorAt(0.46, QColor(7, 15, 29, 248))
        base.setColorAt(1.0, QColor(3, 7, 18, 255))
        background = self.scene.addRect(bounds, QPen(Qt.PenStyle.NoPen), QBrush(base))
        background.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        background.setZValue(-30)

        glow = QRadialGradient(bounds.center(), max(bounds.width(), bounds.height()) * 0.56)
        glow.setColorAt(0.0, QColor(255, 255, 255, 18))
        glow.setColorAt(0.34, QColor(90, 167, 255, 20))
        glow.setColorAt(0.72, QColor(90, 167, 255, 4))
        glow.setColorAt(1.0, QColor(90, 167, 255, 0))
        glow_item = self.scene.addRect(bounds, QPen(Qt.PenStyle.NoPen), QBrush(glow))
        glow_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        glow_item.setZValue(-29)

        image = QPixmap(str(_resource_path("assets/preview_empty_logo.png")))
        if image.isNull():
            placeholder = QGraphicsTextItem()
            placeholder.setHtml(
                "<div align='center' style='font-family: Segoe UI; color: rgba(140,170,210,0.52);'>"
                "<p style='font-size: 32pt; font-weight: 800; margin: 0;'>SIEKACZ 9000</p>"
                "<p style='font-size: 13pt; font-weight: 500; margin: 8px 0 0;'>"
                "Uzupełnij parametry i kliknij Oblicz rozkrój</p>"
                "</div>"
            )
            placeholder.setTextWidth(min(bounds.width() * 0.8, 720.0))
            placeholder.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            placeholder.setZValue(-20)
            self.scene.addItem(placeholder)
            placeholder.setPos(
                bounds.x() + (bounds.width() - placeholder.boundingRect().width()) / 2,
                bounds.y() + (bounds.height() - placeholder.boundingRect().height()) / 2,
            )
            return

        max_w = min(bounds.width() * 0.64, 760.0)
        max_h = bounds.height() * 0.72
        scaled = image.scaled(
            int(max_w),
            int(max_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        item = self.scene.addPixmap(scaled)
        item.setOffset(
            bounds.x() + (bounds.width() - scaled.width()) / 2,
            bounds.y() + (bounds.height() - scaled.height()) / 2,
        )
        item.setOpacity(0.92)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setZValue(-20)

    def _rounded_rect(self, rect: QRectF, radius: float, fill: QColor | QBrush, pen: QPen | None = None):
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        item = self.scene.addPath(path, pen or QPen(Qt.PenStyle.NoPen), fill if isinstance(fill, QBrush) else QBrush(fill))
        item.setZValue(-10)
        return item


    def _draw_technical_grid(self, rect: QRectF) -> None:
        if self.print_mode or self.theme == "light":
            return
        dot_pen = QPen(QColor(80, 101, 130, 42), 1.0)
        dot_pen.setCosmetic(True)
        step = 24.0
        x = rect.left() + 16.0
        while x < rect.right() - 16.0:
            y = rect.top() + 16.0
            while y < rect.bottom() - 16.0:
                dot = self.scene.addLine(x, y, x + 0.1, y + 0.1, dot_pen)
                dot.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                dot.setZValue(-8)
                y += step
            x += step

    def _draw_label(
        self,
        text: str,
        x: float,
        y: float,
        size: int = 9,
        color: QColor | None = None,
        bold: bool = False,
    ) -> QGraphicsTextItem:
        palette = self._palette()
        item = self.scene.addText(text)
        item.setDefaultTextColor(color or QColor(str(palette["text"])))
        font = QFont("Segoe UI")
        font.setPointSize(size)
        font.setBold(bold)
        item.setFont(font)
        item.setPos(x, y)
        return item

    def _draw_right_label(self, text: str, right_x: float, y: float, size: int, color: QColor) -> None:
        item = self._draw_label(text, 0, y, size, color)
        item.setPos(right_x - item.boundingRect().width(), y)

    @staticmethod
    def _elided_label_text(text: str, size: int, max_width: float, bold: bool = False) -> str:
        font = QFont("Segoe UI")
        font.setPointSize(size)
        font.setBold(bold)
        return QFontMetrics(font).elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            max(1, int(max_width)),
        )

    def _symbol_from_index(self, index: int) -> str:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if index < len(alphabet):
            return alphabet[index]
        value = index - len(alphabet)
        first = value // len(alphabet)
        second = value % len(alphabet)
        return alphabet[first % len(alphabet)] + alphabet[second]

    def _part_symbol(self, part_width: float, part_height: float) -> str:
        key = _dimension_key(part_width, part_height)
        if key not in self._part_symbol_map:
            self._part_symbol_map[key] = self._symbol_from_index(len(self._part_symbol_map))
        return self._part_symbol_map[key]

    def _part_text_color(self, part_width: float, part_height: float, missing: bool) -> QColor:
        if self.print_mode:
            return QColor("#000000")
        if missing:
            return QColor("#ffffff")
        start, end, _ = self._part_colors(part_width, part_height, missing)
        luminance = (
            (start.redF() + end.redF()) * 0.5 * 0.2126
            + (start.greenF() + end.greenF()) * 0.5 * 0.7152
            + (start.blueF() + end.blueF()) * 0.5 * 0.0722
        )
        return QColor("#07111f") if luminance > 0.62 else QColor("#ffffff")

    def _draw_part_label(self, rect: QGraphicsRectItem, symbol: str, dimension_text: str, color: QColor) -> None:
        rw = rect.rect().width()
        rh = rect.rect().height()
        rx = rect.rect().x()
        ry = rect.rect().y()

        if rw < 2 or rh < 2:
            return

        # Print mode: black text, symbol only (no dimensions).
        label_color = QColor("#000000") if self.print_mode else color

        if rw < 5 or rh < 5:
            dot = self.scene.addEllipse(
                rect.rect().center().x() - 1.5,
                rect.rect().center().y() - 1.5,
                3, 3,
                QPen(Qt.PenStyle.NoPen),
                QBrush(label_color),
            )
            dot.setParentItem(rect)
            return

        # Dimensions are present in print/export too.  The black label colour
        # above keeps that version legible without changing its geometry.
        is_portrait = rh > rw

        dim_item = QGraphicsTextItem()
        self.scene.addItem(dim_item)
        dim_item.setParentItem(rect)
        dim_item.setDefaultTextColor(color)
        dim_item.document().setDocumentMargin(0)
        dim_font = QFont("Segoe UI")
        dim_font.setWeight(QFont.Weight.Bold)
        dim_font.setPointSizeF(18.0)
        dim_item.setFont(dim_font)
        dim_item.setPlainText(dimension_text)
        dim_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        db = dim_item.boundingRect()

        sym_avail_h = rh
        sym_avail_w = rw
        scene_w = 0.0
        scene_h = 0.0
        margin = min(min(rw, rh) * 0.05, 8.0)

        if db.width() > 0 and db.height() > 0:
            if is_portrait:
                target_scene_x = min(rw * 0.42, 18.0)
                target_scene_y = rh * 0.82
                dim_scale = max(0.04, min(
                    target_scene_x / db.height(),
                    target_scene_y / db.width(),
                ))
                dim_item.setRotation(-90)
                dim_item.setScale(dim_scale)
                scene_w = db.height() * dim_scale
                scene_h = db.width()  * dim_scale
                margin  = min(rh * 0.05, 8.0)
                px = rx + rw - margin - scene_w
                py = ry + rh - margin
                dim_item.setPos(px, py)
                sym_avail_h = rh - scene_h - margin * 2
                sym_avail_w = rw
            else:
                target_h = min(rh * 0.24, 20.0)
                dim_scale = max(0.04, min(
                    target_h / db.height(),
                    rw * 0.8 / db.width(),
                ))
                dim_item.setScale(dim_scale)
                margin = min(rh * 0.05, 8.0)
                scene_w = db.width() * dim_scale
                scene_h = db.height() * dim_scale
                dim_item.setPos(
                    rx + rw - scene_w - margin,
                    ry + rh - scene_h - margin,
                )
                sym_avail_h = rh - scene_h - margin * 2
                sym_avail_w = rw

        # The dimension wins on dense layouts.  A symbol is added only when
        # there is a separate cross-axis lane for it, never on top of the
        # dimension anchored at the lower-right end of the long edge.
        symbol_lane = (rw if is_portrait else rh) - (scene_w if is_portrait else scene_h) - margin * 2
        show_symbol = bool(symbol) and symbol_lane >= max(12.0, (rw if is_portrait else rh) * 0.30)
        if db.width() > 0 and db.height() > 0 and show_symbol:
            sym_item = QGraphicsTextItem()
            self.scene.addItem(sym_item)
            sym_item.setParentItem(rect)
            sym_item.setDefaultTextColor(color)
            sym_item.document().setDocumentMargin(0)
            sym_font = QFont("Segoe UI")
            sym_font.setWeight(QFont.Weight.Black)
            sym_font.setPointSizeF(18.0)
            sym_item.setFont(sym_font)
            sym_item.setPlainText(symbol)
            sym_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            sb = sym_item.boundingRect()
            if sb.width() > 0 and sb.height() > 0:
                if is_portrait:
                    sym_scale = max(0.04, min(
                        rh * 0.56 / sb.width(),
                        symbol_lane * 0.72 / sb.height(),
                        1.2,
                    ))
                    sym_item.setRotation(-90)
                    rotated_w = sb.height() * sym_scale
                    rotated_h = sb.width() * sym_scale
                    sym_item.setScale(sym_scale)
                    sym_item.setPos(
                        rx + max(margin, (symbol_lane - rotated_w) / 2),
                        ry + (rh + rotated_h) / 2,
                    )
                    return
                sym_scale = max(0.04, min(
                    min(sym_avail_h * 0.6, rh * 0.3) / sb.height(),
                    sym_avail_w * 0.6 / sb.width(),
                    1.2,
                ))
                sym_item.setScale(sym_scale)
                margin = min(rh * 0.05, 8.0)
                sym_item.setPos(
                    rx + (rw - sb.width() * sym_scale) / 2,
                    ry + max(margin, (sym_avail_h - sb.height() * sym_scale) / 2),
                )

    def _legend_entries(self, layout: SheetLayout, missing: bool = False) -> list[tuple[str, tuple[float, float], int, QColor]]:
        entries: dict[tuple[float, float], dict[str, object]] = {}
        for part in layout.parts:
            key = _dimension_key(part.part.width, part.part.height)
            if key not in entries:
                entries[key] = {
                    "symbol": self._part_symbol(part.part.width, part.part.height),
                    "width": part.part.width,
                    "height": part.part.height,
                    "count": 0,
                    "color": self._part_colors(part.part.width, part.part.height, missing)[0],
                }
            entries[key]["count"] = int(entries[key]["count"]) + 1
        return [
            (
                str(data["symbol"]),
                (float(data["width"]), float(data["height"])),
                int(data["count"]),
                data["color"] if isinstance(data["color"], QColor) else QColor("#64748b"),
            )
            for _key, data in sorted(entries.items(), key=lambda item: str(item[1]["symbol"]))
        ]

    def _draw_legend(self, entries: list[tuple[str, tuple[float, float], int, QColor]], x: float, y: float, max_width: float) -> float:
        if not entries:
            return 0.0
        palette = self._palette()
        title_color = QColor("#000000") if self.print_mode else QColor(str(palette["text"]))
        text_color = QColor("#000000") if self.print_mode else QColor(str(palette["muted"]))
        self._draw_label("Legenda formatek (kliknij, aby podświetlić)", x, y, 8, title_color, bold=True)
        item_y = y + 24
        col_w = 190.0 if self.print_mode else 210.0
        row_h = 24.0
        cols = max(1, int(max_width // col_w))
        for index, (symbol, dimensions, count, color) in enumerate(entries):
            col = index % cols
            row = index // cols
            lx = x + col * col_w
            ly = item_y + row * row_h
            marker_color = QColor("#ffffff") if self.print_mode else color
            marker_pen = QPen(QColor("#000000") if self.print_mode else QColor(marker_color).darker(125), 0.9)
            marker_rect = self.scene.addRect(QRectF(lx, ly + 3, 14, 14), marker_pen, QBrush(marker_color))
            marker_rect.setToolTip(
                f"{dimensions[0]:.0f} × {dimensions[1]:.0f} mm — {count} szt.\nKliknij, aby podświetlić wszystkie wystąpienia."
            )
            marker_rect.setCursor(Qt.CursorShape.PointingHandCursor)
            marker_rect.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
            marker_rect.setData(0, marker_pen)  # original pen for restoration
            marker_rect.setData(1, "legend_marker")
            marker_rect.setData(2, _dimension_key(dimensions[0], dimensions[1]))
            self._legend_markers_by_key.setdefault(
                _dimension_key(dimensions[0], dimensions[1]),
                [],
            ).append(marker_rect)
            symbol_item = self._draw_label(symbol, lx + 22, ly - 1, 9, title_color, bold=True)
            symbol_item.setToolTip(f"{dimensions[0]:.0f} x {dimensions[1]:.0f} mm")
            self._draw_label(
                f"— {dimensions[0]:.0f} × {dimensions[1]:.0f} mm — {count} szt.",
                lx + 46,
                ly,
                8,
                text_color,
            )
        rows = (len(entries) + cols - 1) // cols
        return 24.0 + rows * row_h

    def _draw_dimensioning(self, sheet_rect: QRectF, display_width: float, display_height: float) -> None:
        palette = self._palette()
        if self.print_mode:
            dim_color = QColor("#000000")
            muted_color = QColor("#111111")
        else:
            dim_color = QColor(str(palette["muted"]))
            dim_color.setAlpha(185 if self.theme == "light" else 165)
            muted_color = QColor(str(palette["muted"]))

        pen = QPen(dim_color, 0.85)
        pen.setCosmetic(True)
        extension_pen = QPen(dim_color, 0.65, Qt.PenStyle.DashLine)
        extension_pen.setCosmetic(True)

        top_y = sheet_rect.top() - 18
        arrow = 5.0

        # ── Width dimension: horizontal arrow above the plate ──
        self.scene.addLine(sheet_rect.left(), top_y, sheet_rect.right(), top_y, pen)
        self.scene.addLine(sheet_rect.left(), top_y, sheet_rect.left(), sheet_rect.top(), extension_pen)
        self.scene.addLine(sheet_rect.right(), top_y, sheet_rect.right(), sheet_rect.top(), extension_pen)
        for sx, direction in ((sheet_rect.left(), 1), (sheet_rect.right(), -1)):
            self.scene.addLine(sx, top_y, sx + direction * arrow, top_y - arrow, pen)
            self.scene.addLine(sx, top_y, sx + direction * arrow, top_y + arrow, pen)

        # Width label centred above the arrow; height shown as "H" suffix
        dim_label = f"{display_width:.0f} × {display_height:.0f} mm"
        width_text = self._draw_label(dim_label, 0, 0, 7, muted_color, bold=True)
        width_text.setPos(sheet_rect.center().x() - width_text.boundingRect().width() / 2, top_y - 19)

    def _compute_bands(self, layout: SheetLayout, axis: str) -> list[dict]:
        """Break the used extent along *axis* into main cutting blocks.

        ``axis='x'`` finds vertical rips (the long-side strips); ``axis='y'``
        finds horizontal cross-cuts (the short-side rows).  A position is a valid
        first-level guillotine cut when no placed part straddles it.  The clear
        cuts slice the extent into fine strips; each strip is tagged with the
        formatka that fills it (orientation-independent, so a rotated 100×200 and
        an upright 100×200 count as the same part).

        Adjacent strips of the *same formatka* are merged into one block, so a
        contiguous region of one part type is reported as a single dimension
        instead of being chopped up (the "205 + 1021" problem).  A block whose
        strips are all the same size (clean repeated columns/rows, e.g. 7×40) is
        labelled ``N × W``; a mixed block (e.g. a grid with a rotated row) is
        labelled with its total size.

        Returns dicts ``{start, end, total, n, w, uniform}`` (layout coords),
        in increasing order.
        """
        parts = list(getattr(layout, "parts", []) or [])
        if not parts:
            return []
        EPS = 0.5
        if axis == "x":
            pos = lambda p: p.x
            size = lambda p: p.width
        else:
            pos = lambda p: p.y
            size = lambda p: p.height
        used = max((pos(p) + size(p)) for p in parts)

        def is_clear(v: float) -> bool:
            for p in parts:
                if pos(p) + EPS < v < pos(p) + size(p) - EPS:
                    return False
            return True

        edges = set()
        for p in parts:
            edges.add(round(pos(p), 2))
            edges.add(round(pos(p) + size(p), 2))
        interior = sorted(e for e in edges if EPS < e < used - EPS and is_clear(e))

        cuts: list[float] = []
        for c in [0.0, *interior, used]:
            if not cuts or abs(c - cuts[-1]) > EPS:
                cuts.append(c)

        # Fine strips tagged with their dominant formatka (by covered area).
        fine: list[dict] = []
        for a, b in zip(cuts, cuts[1:]):
            extent = b - a
            if extent <= EPS:
                continue
            inside = [p for p in parts if pos(p) >= a - EPS and pos(p) + size(p) <= b + EPS]
            if not inside:
                continue
            area: dict[tuple, float] = {}
            for p in inside:
                key = tuple(sorted((round(p.part.width), round(p.part.height))))
                area[key] = area.get(key, 0.0) + p.width * p.height
            dominant = max(area, key=area.get)
            fine.append({"a": a, "b": b, "w": extent, "key": dominant})

        # Merge adjacent strips of the same formatka into blocks.
        merge_gap = 8.0  # kerf-sized tolerance between touching strips
        blocks: list[dict] = []
        width_tol = 3.0  # only fold strips of (essentially) the same size together
        for strip in fine:
            if (
                blocks
                and blocks[-1]["key"] == strip["key"]
                and abs(strip["w"] - blocks[-1]["widths"][-1]) < width_tol
                and (strip["a"] - blocks[-1]["end"]) < merge_gap
            ):
                blocks[-1]["end"] = strip["b"]
                blocks[-1]["widths"].append(strip["w"])
            else:
                blocks.append({
                    "start": strip["a"],
                    "end": strip["b"],
                    "key": strip["key"],
                    "widths": [strip["w"]],
                })

        groups: list[dict] = []
        for blk in blocks:
            widths = blk["widths"]
            count = len(widths)
            total = blk["end"] - blk["start"]
            mean_w = sum(widths) / count
            uniform = count > 1 and (max(widths) - min(widths) < 3.0)
            if uniform and blk["key"]:
                # Snap the label to the real formatka edge for a clean number.
                snap = min(blk["key"], key=lambda d: abs(d - mean_w))
                disp_w = float(snap) if abs(snap - mean_w) < 4.0 else mean_w
            else:
                disp_w = mean_w
            groups.append({
                "start": blk["start"],
                "end": blk["end"],
                "total": total,
                "n": count,
                "w": disp_w,
                "uniform": uniform,
            })
        return groups

    def _compute_vertical_bands(self, layout: SheetLayout) -> list[dict]:
        """Main vertical cutting strips along the long (X) side."""
        return self._compute_bands(layout, "x")

    def _compute_horizontal_bands(self, layout: SheetLayout) -> list[dict]:
        """Main horizontal cross-cuts along the short (Y) side."""
        return self._compute_bands(layout, "y")

    def _draw_multiplier_badge(self, cx: float, cy: float, count: int, missing: bool) -> None:
        """Draw a circular ``×N`` badge marking how many identical boards this card represents."""
        label = f"×{count}"
        radius = 13.0
        if self.print_mode:
            fill = QColor("#ffffff")
            ring = QPen(QColor("#000000"), 1.0)
            txt_color = QColor("#000000")
        else:
            fill = QColor("#ef4444") if missing else QColor("#2563eb")
            ring = QPen(Qt.PenStyle.NoPen)
            txt_color = QColor("#ffffff")
        ring.setCosmetic(True)
        badge = self.scene.addEllipse(cx - radius, cy - radius, radius * 2, radius * 2, ring, QBrush(fill))
        badge.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        badge.setZValue(50)
        badge.setToolTip(f"{count} identycznych płyt o tym samym rozkroju")
        text = self._draw_label(label, 0, 0, 8, txt_color, bold=True)
        bounds = text.boundingRect()
        text.setPos(cx - bounds.width() / 2, cy - bounds.height() / 2)
        text.setZValue(51)
        text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _draw_cut_order_badge(self, cx: float, cy: float, number: int | str) -> None:
        """Draw a small numbered circle marking the cutting order of a block."""
        label = str(number)
        radius = max(7.0, 4.8 + len(label) * 2.3)
        if self.print_mode:
            fill = QColor("#ffffff")
            ring = QPen(QColor("#000000"), 0.9)
            ring.setCosmetic(True)
            txt_color = QColor("#000000")
        else:
            fill = QColor("#fbbf24")  # amber — visible on dark UI and red parts
            ring = QPen(Qt.PenStyle.NoPen)
            txt_color = QColor("#1f2937")
        badge = self.scene.addEllipse(cx - radius, cy - radius, radius * 2, radius * 2, ring, QBrush(fill))
        badge.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        badge.setZValue(50)
        font_size = 5 if len(label) > 2 else 6
        text = self._draw_label(label, 0, 0, font_size, txt_color, bold=True)
        bounds = text.boundingRect()
        text.setPos(cx - bounds.width() / 2, cy - bounds.height() / 2)
        text.setZValue(51)
        text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    # ── Four-sided cut-order / size strips (non-rotated boards) ──────────────
    def _strip_styles(self):
        palette = self._palette()
        if self.print_mode:
            dim_color = QColor("#000000")
            text_color = QColor("#111111")
        else:
            dim_color = QColor(str(palette["muted"]))
            dim_color.setAlpha(180 if self.theme == "light" else 145)
            text_color = QColor(str(palette["muted"]))
        pen = QPen(dim_color, 0.85)
        pen.setCosmetic(True)
        inner = QPen(dim_color, 0.5, Qt.PenStyle.DashLine)
        inner.setCosmetic(True)
        return pen, inner, text_color

    @staticmethod
    def _band_label(group) -> str:
        if bool(group["uniform"]) and int(group["n"]) > 1:
            return f"{int(group['n'])} × {group['w']:.0f} mm"
        return f"{group['total']:.0f} mm"

    @staticmethod
    def _shows_total_cut_span(groups) -> bool:
        """Show a single summary dimension only when multiple bands exist."""
        return len(groups) > 1

    @staticmethod
    def _total_cut_span_label(groups) -> str:
        # The first-to-last span is the actual occupied cut width. It includes
        # each effective kerf gap (kerf plus configured tolerance), including
        # gaps between different repeated bands.
        total = max(group["end"] for group in groups) - min(group["start"] for group in groups)
        return f"{total:.0f} mm"

    def _bracket_h(self, x1, x2, base_y, count, uniform, label_text, order, txt, pen, inner, label_above) -> None:
        no = Qt.MouseButton.NoButton
        tick, inner_tick = 4.5, 2.5
        self.scene.addLine(x1, base_y, x2, base_y, pen).setAcceptedMouseButtons(no)
        for sx in (x1, x2):
            self.scene.addLine(sx, base_y - tick, sx, base_y + tick, pen).setAcceptedMouseButtons(no)
        if uniform and count > 1 and (x2 - x1) > 26:
            step = (x2 - x1) / count
            for k in range(1, count):
                ix = x1 + step * k
                self.scene.addLine(ix, base_y - inner_tick, ix, base_y + inner_tick, inner).setAcceptedMouseButtons(no)
        center_x = (x1 + x2) / 2
        label = self._draw_label(label_text, 0, 0, 6, txt, bold=True)
        lr = label.boundingRect()
        if order:
            br, gap = max(7.0, 4.8 + len(str(order)) * 2.3), 5.0
            unit_left = center_x - (br * 2 + gap + lr.width()) / 2
            ly = base_y - 7 - lr.height() if label_above else base_y + 4
            label.setPos(unit_left + br * 2 + gap, ly)
            self._draw_cut_order_badge(unit_left + br, ly + lr.height() / 2, order)
        else:
            ly = base_y - 4 - lr.height() if label_above else base_y + 4
            label.setPos(center_x - lr.width() / 2, ly)
        label.setAcceptedMouseButtons(no)

    def _bracket_v(self, y1, y2, base_x, count, uniform, label_text, order, txt, pen, inner, label_left) -> None:
        no = Qt.MouseButton.NoButton
        tick, inner_tick = 4.5, 2.5
        self.scene.addLine(base_x, y1, base_x, y2, pen).setAcceptedMouseButtons(no)
        for sy in (y1, y2):
            self.scene.addLine(base_x - tick, sy, base_x + tick, sy, pen).setAcceptedMouseButtons(no)
        if uniform and count > 1 and (y2 - y1) > 26:
            step = (y2 - y1) / count
            for k in range(1, count):
                iy = y1 + step * k
                self.scene.addLine(base_x - inner_tick, iy, base_x + inner_tick, iy, inner).setAcceptedMouseButtons(no)
        center_y = (y1 + y2) / 2
        label = self._draw_label(label_text, 0, 0, 6, txt, bold=True)
        label.setRotation(-90)
        lr = label.boundingRect()
        if order:
            br = max(7.0, 4.8 + len(str(order)) * 2.3)
            if label_left:
                badge_cx = base_x - br - 4
                self._draw_cut_order_badge(badge_cx, center_y, order)
                label.setPos(badge_cx - br - 2 - lr.height(), center_y + lr.width() / 2)
            else:
                badge_cx = base_x + br + 4
                self._draw_cut_order_badge(badge_cx, center_y, order)
                label.setPos(badge_cx + br + 2, center_y + lr.width() / 2)
        else:
            if label_left:
                label.setPos(base_x - 5 - lr.height(), center_y + lr.width() / 2)
            else:
                label.setPos(base_x + 5, center_y + lr.width() / 2)
        label.setAcceptedMouseButtons(no)

    def _draw_cut_strips_all_sides(self, layout: SheetLayout, sheet_rect: QRectF, scale: float) -> None:
        """Draw cutting strips on all four sides of a non-rotated board.

        TOP   = vertical rips (widths) numbered in cut order;
        LEFT  = horizontal cross-cuts (heights) numbered in cut order;
        BOTTOM/RIGHT = the same sizes mirrored (no numbers) so the operator can
        read a dimension from whichever edge is nearest.
        """
        pen, inner, txt = self._strip_styles()
        vbands = self._compute_vertical_bands(layout)    # long-side rips (widths)
        hbands = self._compute_horizontal_bands(layout)  # cross-cuts (heights)
        left, top = sheet_rect.left(), sheet_rect.top()

        # TOP — rip sizes
        for g in vbands:
            x1, x2 = left + g["start"] * scale, left + g["end"] * scale
            if x2 - x1 < 14:
                continue
            self._bracket_h(x1, x2, top - 18, int(g["n"]), bool(g["uniform"]),
                            self._band_label(g), 0, txt, pen, inner, label_above=True)
        # BOTTOM — rip sizes mirrored
        for g in vbands:
            x1, x2 = left + g["start"] * scale, left + g["end"] * scale
            if x2 - x1 < 14:
                continue
            self._bracket_h(x1, x2, sheet_rect.bottom() + 18, int(g["n"]), bool(g["uniform"]),
                            self._band_label(g), 0, txt, pen, inner, label_above=False)
        # LEFT — cross-cut sizes
        for g in hbands:
            y1, y2 = top + g["start"] * scale, top + g["end"] * scale
            if y2 - y1 < 14:
                continue
            self._bracket_v(y1, y2, left - 18, int(g["n"]), bool(g["uniform"]),
                            self._band_label(g), 0, txt, pen, inner, label_left=True)
        # RIGHT — cross-cut sizes mirrored
        for g in hbands:
            y1, y2 = top + g["start"] * scale, top + g["end"] * scale
            if y2 - y1 < 14:
                continue
            self._bracket_v(y1, y2, sheet_rect.right() + 18, int(g["n"]), bool(g["uniform"]),
                            self._band_label(g), 0, txt, pen, inner, label_left=False)

    def _draw_long_side_rotated(self, layout, sheet_rect, scale, groups, pen, tick_pen, inner_pen, text_color, tick, inner_tick) -> None:
        """Long-side rip bracket for the rotated (vertical) display — drawn on the left edge."""
        no_btn = Qt.MouseButton.NoButton
        sw = layout.stock.width
        bracket_x = sheet_rect.left() - 18
        for group in groups:
            # layout X [start, end] -> display Y [sw - end, sw - start]
            y_top = sheet_rect.top() + (sw - group["end"]) * scale
            y_bot = sheet_rect.top() + (sw - group["start"]) * scale
            if y_bot - y_top < 14:
                continue
            count = int(group["n"])
            uniform = bool(group["uniform"])
            center_y = (y_top + y_bot) / 2
            line = self.scene.addLine(bracket_x, y_top, bracket_x, y_bot, pen)
            line.setAcceptedMouseButtons(no_btn)
            for sy in (y_top, y_bot):
                t = self.scene.addLine(bracket_x - tick, sy, bracket_x + tick, sy, tick_pen)
                t.setAcceptedMouseButtons(no_btn)
            if uniform and count > 1 and (y_bot - y_top) > 26:
                step = (y_bot - y_top) / count
                for k in range(1, count):
                    iy = y_top + step * k
                    inner = self.scene.addLine(bracket_x - inner_tick, iy, bracket_x + inner_tick, iy, inner_pen)
                    inner.setAcceptedMouseButtons(no_btn)
            label_text = f"{count} × {group['w']:.0f} mm" if (uniform and count > 1) else f"{group['total']:.0f} mm"
            label = self._draw_label(label_text, 0, 0, 6, text_color, bold=True)
            lr = label.boundingRect()
            label.setPos(bracket_x - 5 - lr.width(), center_y - lr.height() / 2)
            label.setAcceptedMouseButtons(no_btn)

        if self._shows_total_cut_span(groups):
            total_x = bracket_x - 19
            y_top = sheet_rect.top() + (sw - max(group["end"] for group in groups)) * scale
            y_bot = sheet_rect.top() + (sw - min(group["start"] for group in groups)) * scale
            line = self.scene.addLine(total_x, y_top, total_x, y_bot, pen)
            line.setAcceptedMouseButtons(no_btn)
            for sy in (y_top, y_bot):
                t = self.scene.addLine(total_x - tick, sy, total_x + tick, sy, tick_pen)
                t.setAcceptedMouseButtons(no_btn)
            total_label = self._draw_label(self._total_cut_span_label(groups), 0, 0, 6, text_color, bold=True)
            total_bounds = total_label.boundingRect()
            total_label.setPos(total_x - 5 - total_bounds.width(), (y_top + y_bot - total_bounds.height()) / 2)
            total_label.setAcceptedMouseButtons(no_btn)

    def _draw_short_side_rotated(self, layout, sheet_rect, scale, groups, pen, tick_pen, inner_pen, text_color, tick, inner_tick) -> None:
        """Cross-cut bracket for the rotated (vertical) display — drawn along the bottom edge."""
        no_btn = Qt.MouseButton.NoButton
        bottom_y = sheet_rect.bottom() + 13
        for group in groups:
            # layout Y [start, end] -> display X [start, end]
            x1 = sheet_rect.left() + group["start"] * scale
            x2 = sheet_rect.left() + group["end"] * scale
            if x2 - x1 < 14:
                continue
            count = int(group["n"])
            uniform = bool(group["uniform"])
            center_x = (x1 + x2) / 2
            line = self.scene.addLine(x1, bottom_y, x2, bottom_y, pen)
            line.setAcceptedMouseButtons(no_btn)
            for sx in (x1, x2):
                t = self.scene.addLine(sx, bottom_y - tick, sx, bottom_y + tick, tick_pen)
                t.setAcceptedMouseButtons(no_btn)
            if uniform and count > 1 and (x2 - x1) > 26:
                step = (x2 - x1) / count
                for k in range(1, count):
                    ix = x1 + step * k
                    inner = self.scene.addLine(ix, bottom_y - inner_tick, ix, bottom_y + inner_tick, inner_pen)
                    inner.setAcceptedMouseButtons(no_btn)
            label_text = f"{count} × {group['w']:.0f} mm" if (uniform and count > 1) else f"{group['total']:.0f} mm"
            label = self._draw_label(label_text, 0, 0, 6, text_color, bold=True)
            lr = label.boundingRect()
            label.setPos(center_x - lr.width() / 2, bottom_y + 4)
            label.setAcceptedMouseButtons(no_btn)

        if self._shows_total_cut_span(groups):
            total_y = bottom_y + 20
            x1 = sheet_rect.left() + min(group["start"] for group in groups) * scale
            x2 = sheet_rect.left() + max(group["end"] for group in groups) * scale
            line = self.scene.addLine(x1, total_y, x2, total_y, pen)
            line.setAcceptedMouseButtons(no_btn)
            for sx in (x1, x2):
                t = self.scene.addLine(sx, total_y - tick, sx, total_y + tick, tick_pen)
                t.setAcceptedMouseButtons(no_btn)
            total_label = self._draw_label(self._total_cut_span_label(groups), 0, 0, 6, text_color, bold=True)
            total_bounds = total_label.boundingRect()
            total_label.setPos((x1 + x2 - total_bounds.width()) / 2, total_y + 4)
            total_label.setAcceptedMouseButtons(no_btn)

    def _draw_segment_dimensioning(self, layout: SheetLayout, sheet_rect: QRectF, scale: float, display_rotated: bool) -> None:
        groups = self._compute_vertical_bands(layout)
        if not groups:
            return

        palette = self._palette()
        if self.print_mode:
            dim_color = QColor("#000000")
            text_color = QColor("#111111")
        else:
            dim_color = QColor(str(palette["muted"]))
            dim_color.setAlpha(180 if self.theme == "light" else 145)
            text_color = QColor(str(palette["muted"]))

        pen = QPen(dim_color, 0.85)
        pen.setCosmetic(True)
        tick_pen = QPen(dim_color, 0.85)
        tick_pen.setCosmetic(True)
        inner_pen = QPen(dim_color, 0.5, Qt.PenStyle.DashLine)
        inner_pen.setCosmetic(True)
        tick = 4.5
        inner_tick = 2.5

        # Rotated display: the long-side rips run vertically on screen, so the
        # ordered cut bracket moves to the LEFT edge (axes swap, see
        # _map_rect_to_display).  Without this the strips simply vanished in
        # vertical / auto orientation — the main "nie zawsze działa" case.
        if display_rotated:
            self._draw_long_side_rotated(
                layout, sheet_rect, scale, groups, pen, tick_pen, inner_pen, text_color, tick, inner_tick
            )
            return

        bottom_y = sheet_rect.bottom() + 13
        for group in groups:
            x1 = sheet_rect.left() + group["start"] * scale
            x2 = sheet_rect.left() + group["end"] * scale
            if x2 - x1 < 14:
                continue
            count = int(group["n"])
            uniform = bool(group["uniform"])
            center_x = (x1 + x2) / 2

            line = self.scene.addLine(x1, bottom_y, x2, bottom_y, pen)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for sx in (x1, x2):
                tick_line = self.scene.addLine(sx, bottom_y - tick, sx, bottom_y + tick, tick_pen)
                tick_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            # Internal rip marks where repeated identical strips meet.
            if uniform and count > 1 and (x2 - x1) > 26:
                step = (x2 - x1) / count
                for k in range(1, count):
                    ix = x1 + step * k
                    inner = self.scene.addLine(ix, bottom_y - inner_tick, ix, bottom_y + inner_tick, inner_pen)
                    inner.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            if uniform and count > 1:
                label_text = f"{count} × {group['w']:.0f} mm"
            else:
                label_text = f"{group['total']:.0f} mm"
            # Draw the measurement label centred
            label = self._draw_label(label_text, 0, 0, 6, text_color, bold=True)
            lr = label.boundingRect()
            base_y = bottom_y + 4
            label.setPos(center_x - lr.width() / 2, base_y)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if self._shows_total_cut_span(groups):
            total_y = bottom_y + 20
            x1 = sheet_rect.left() + min(group["start"] for group in groups) * scale
            x2 = sheet_rect.left() + max(group["end"] for group in groups) * scale
            total_line = self.scene.addLine(x1, total_y, x2, total_y, pen)
            total_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for sx in (x1, x2):
                tick_line = self.scene.addLine(sx, total_y - tick, sx, total_y + tick, tick_pen)
                tick_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            total_label = self._draw_label(self._total_cut_span_label(groups), 0, 0, 6, text_color, bold=True)
            total_bounds = total_label.boundingRect()
            total_label.setPos((x1 + x2 - total_bounds.width()) / 2, total_y + 4)
            total_label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _draw_short_side_dimensioning(self, layout: SheetLayout, sheet_rect: QRectF, scale: float, display_rotated: bool) -> None:
        """Dimension the main horizontal cross-cuts on the short (left) side.

        Mirrors :meth:`_draw_segment_dimensioning` but along Y, drawn as vertical
        brackets to the left of the plate so the operator also sees the row
        sizes (e.g. ``3 × 300 mm``), not just the long-side strips.
        """
        groups = self._compute_horizontal_bands(layout)
        if not groups:
            return
        if len(groups) == 1 and not groups[0]["uniform"]:
            return

        palette = self._palette()
        if self.print_mode:
            dim_color = QColor("#000000")
            text_color = QColor("#111111")
        else:
            dim_color = QColor(str(palette["muted"]))
            dim_color.setAlpha(180 if self.theme == "light" else 145)
            text_color = QColor(str(palette["muted"]))

        pen = QPen(dim_color, 0.85)
        pen.setCosmetic(True)
        tick_pen = QPen(dim_color, 0.85)
        tick_pen.setCosmetic(True)
        inner_pen = QPen(dim_color, 0.5, Qt.PenStyle.DashLine)
        inner_pen.setCosmetic(True)
        tick = 4.5
        inner_tick = 2.5

        # Rotated display: cross-cuts run horizontally on screen → bracket along
        # the BOTTOM edge (axes swap).
        if display_rotated:
            self._draw_short_side_rotated(
                layout, sheet_rect, scale, groups, pen, tick_pen, inner_pen, text_color, tick, inner_tick
            )
            return

        left_x = sheet_rect.left() - 13
        for group in groups:
            y1 = sheet_rect.top() + group["start"] * scale
            y2 = sheet_rect.top() + group["end"] * scale
            if y2 - y1 < 14:
                continue
            count = int(group["n"])
            uniform = bool(group["uniform"])
            center_y = (y1 + y2) / 2

            line = self.scene.addLine(left_x, y1, left_x, y2, pen)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for sy in (y1, y2):
                tick_line = self.scene.addLine(left_x - tick, sy, left_x + tick, sy, tick_pen)
                tick_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            if uniform and count > 1 and (y2 - y1) > 26:
                step = (y2 - y1) / count
                for k in range(1, count):
                    iy = y1 + step * k
                    inner = self.scene.addLine(left_x - inner_tick, iy, left_x + inner_tick, iy, inner_pen)
                    inner.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            if uniform and count > 1:
                label_text = f"{count} × {group['w']:.0f} mm"
            else:
                label_text = f"{group['total']:.0f} mm"
            label = self._draw_label(label_text, 0, 0, 6, text_color, bold=True)
            lb = label.boundingRect()
            label.setPos(left_x - 5 - lb.width(), center_y - lb.height() / 2)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if self._shows_total_cut_span(groups):
            total_x = left_x - 19
            y1 = sheet_rect.top() + min(group["start"] for group in groups) * scale
            y2 = sheet_rect.top() + max(group["end"] for group in groups) * scale
            total_line = self.scene.addLine(total_x, y1, total_x, y2, pen)
            total_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for sy in (y1, y2):
                tick_line = self.scene.addLine(total_x - tick, sy, total_x + tick, sy, tick_pen)
                tick_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            total_label = self._draw_label(self._total_cut_span_label(groups), 0, 0, 6, text_color, bold=True)
            total_bounds = total_label.boundingRect()
            total_label.setPos(total_x - 5 - total_bounds.width(), (y1 + y2 - total_bounds.height()) / 2)
            total_label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _part_colors(self, part_width: float, part_height: float, missing: bool) -> tuple[QColor, QColor, QColor]:
        if self.print_mode:
            return QColor("#ffffff"), QColor("#ffffff"), QColor("#000000")
        if missing:
            return QColor("#ef4444"), QColor("#b91c1c"), QColor("#fecaca")
        key = _dimension_key(part_width, part_height)
        if key not in self._part_color_map:
            self._part_color_map[key] = len(self._part_color_map)
        start, end, border = PART_COLOR_PALETTE[self._part_color_map[key] % len(PART_COLOR_PALETTE)]
        return QColor(start), QColor(end), QColor(border)

    def _part_brush(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        part_width: float,
        part_height: float,
        missing: bool,
    ) -> QBrush:
        if self.print_mode:
            return QBrush(QColor("#ffffff"))
        start, end, _ = self._part_colors(part_width, part_height, missing)
        gradient = QLinearGradient(x, y, x + width, y + height)
        gradient.setColorAt(0, start)
        gradient.setColorAt(1, end)
        return QBrush(gradient)

    def _display_sheet_geometry(self, layout: SheetLayout, consumed_only: bool = False) -> tuple[float, float, bool, bool]:
        stock_w = layout.stock.width
        stock_h = layout.stock.height

        # Rotation is decided on original stock dimensions (not the trimmed fragment).
        if self.display_orientation == "vertical":
            rotate = stock_w > stock_h
        else:
            rotate = stock_h > stock_w

        # For missing/virtual sheets use only the consumed fragment.
        cutoff = False
        if consumed_only and layout.parts:
            used_w = layout.used_width
            if 0 < used_w < stock_w - 1:
                stock_w = used_w
                cutoff = True

        if rotate:
            return stock_h, stock_w, True, cutoff
        return stock_w, stock_h, False, cutoff

    def _map_rect_to_display(
        self,
        layout: SheetLayout,
        rect: tuple[float, float, float, float],
        display_rotated: bool | None = None,
    ) -> tuple[float, float, float, float]:
        x, y, width, height = rect
        if display_rotated is None:
            _, _, rotate, _ = self._display_sheet_geometry(layout)
        else:
            rotate = display_rotated
        if not rotate:
            return x, y, width, height
        return y, x, height, width

    def _map_point_to_display(
        self,
        layout: SheetLayout,
        x: float,
        y: float,
        display_rotated: bool,
    ) -> tuple[float, float]:
        if not display_rotated:
            return x, y
        return y, x

    def _draw_cut_operations_overlay(
        self,
        layout: SheetLayout,
        sheet_rect: QRectF,
        scale: float,
        display_rotated: bool,
    ) -> None:
        return  # CUT SEQUENCES DISABLED TEMPORARILY
        operations = sorted(getattr(layout, "cut_operations", []) or [], key=lambda op: int(getattr(op, "step", 0)))
        if not operations:
            return
        if len(operations) > 90:
            reduced = [op for op in operations if getattr(op, "kind", "") in {"rip", "cross"}]
            operations = (reduced or operations)[:90]
        no_btn = Qt.MouseButton.NoButton
        for op in operations:
            orientation = str(getattr(op, "orientation", ""))
            x = float(getattr(op, "x", 0.0) or 0.0)
            y = float(getattr(op, "y", 0.0) or 0.0)
            length = float(getattr(op, "length", 0.0) or 0.0)
            if length <= 0.0:
                continue
            if orientation == "vertical":
                p1 = self._map_point_to_display(layout, x, y, display_rotated)
                p2 = self._map_point_to_display(layout, x, y + length, display_rotated)
            else:
                p1 = self._map_point_to_display(layout, x, y, display_rotated)
                p2 = self._map_point_to_display(layout, x + length, y, display_rotated)
            x1 = sheet_rect.left() + p1[0] * scale
            y1 = sheet_rect.top() + p1[1] * scale
            x2 = sheet_rect.left() + p2[0] * scale
            y2 = sheet_rect.top() + p2[1] * scale
            kind = str(getattr(op, "kind", "cut"))
            if self.print_mode:
                color = QColor("#000000")
            elif kind == "rip":
                color = QColor("#60a5fa")
            elif kind == "cross":
                color = QColor("#f59e0b")
            else:
                color = QColor("#c084fc")
            pen = QPen(color, 1.1, Qt.PenStyle.DashLine if kind == "trim" else Qt.PenStyle.SolidLine)
            pen.setCosmetic(True)
            line = self.scene.addLine(x1, y1, x2, y2, pen)
            line.setZValue(45)
            line.setToolTip(str(getattr(op, "description", "")) or f"Cięcie {getattr(op, 'step', '')}")
            line.setAcceptedMouseButtons(no_btn)
            mid_x = (x1 + x2) / 2
            mid_y = (y1 + y2) / 2
            if len(operations) <= 50:
                self._draw_cut_order_badge(mid_x, mid_y, int(getattr(op, "step", 0) or 0))

    def _stock_tooltip(self, layout: SheetLayout, missing: bool) -> str:
        nominal_width, nominal_height = _stock_nominal_dimensions(layout.stock)
        lines = [
            f"Nominalnie: {nominal_width:.2f} x {nominal_height:.2f} mm",
            f"Obliczeniowo: {layout.stock.width:.2f} x {layout.stock.height:.2f} mm",
        ]
        if layout.stock.sheet_allowance:
            lines.append(f"Naddatek: {layout.stock.sheet_allowance:.2f} mm")
        if missing:
            lines.append("Dodatkowa płyta wymagana ponad wpisany limit.")
        return "\n".join(lines)

    def _draw_sheet_layouts(self, layouts: list[SheetLayout], y_offset: float, missing: bool) -> float:
        palette = self._palette()
        scale = 0.42
        x = 34.0
        y = y_offset
        # Collapse identically-cut boards into one card with a ×N badge so the
        # operator reads one drawing N times instead of scrolling N copies.
        for group in group_identical_layouts(layouts):
            layout = group.representative
            group_count = group.count
            group_label = (getattr(layout, "group_label", "") or "").strip()
            sheet_numbers = [str(value) for value in getattr(group, "display_sheet_indices", [])] or [str(getattr(layout, "display_sheet_index", layout.sheet_index))]
            if len(sheet_numbers) == 1:
                number_text = sheet_numbers[0]
            elif len(sheet_numbers) == 2:
                number_text = f"{sheet_numbers[0]} i {sheet_numbers[1]}"
            else:
                number_text = ", ".join(sheet_numbers[:-1]) + f" i {sheet_numbers[-1]}"
            thickness = float(getattr(layout.stock, "thickness", 0.0) or 0.0)
            material = str(getattr(layout.stock, "material", "") or group_label or "Materiał").strip()
            base_label = f"{material} · gr. {thickness:g} mm · nr {number_text}"
            if missing:
                base_label = "Brakująca " + base_label.lower()
            nav_label = base_label
            if group_label and group_label.casefold() != material.casefold():
                nav_label += f" · {group_label}"
            self._nav_labels.append(nav_label)
            display_w, display_h, display_rotated, cutoff = self._display_sheet_geometry(layout, consumed_only=missing)
            sheet_w = display_w * scale
            sheet_h = display_h * scale
            legend_entries = self._legend_entries(layout, missing)
            # Non-rotated boards get cut-order/size strips on ALL FOUR sides
            # (top = rip order, left = cross-cut order, right/bottom = sizes), so
            # we reserve margins around the sheet for them.  The rotated view uses
            # its own (swapped-axis) strips and the older, tighter margins.
            if display_rotated:
                dimension_left_margin = 20.0
                dimension_top_margin = 20.0
                dimension_bottom_margin = 54.0
                dimension_right_margin = 0.0
            else:
                dimension_left_margin = 8.0    # minimal left margin
                dimension_top_margin = 20.0    # top dimension line
                dimension_bottom_margin = 54.0  # repeated-band labels plus total occupied span
                dimension_right_margin = 8.0   # minimal right margin
            preview_card_w = max(sheet_w + 36 + dimension_left_margin + dimension_right_margin, 720)
            legend_cols = max(1, int((preview_card_w - 36) // (190.0 if self.print_mode else 210.0)))
            legend_rows = (len(legend_entries) + legend_cols - 1) // legend_cols if legend_entries else 0
            legend_h = 0 if not legend_entries else 32 + legend_rows * 24
            card_w = max(sheet_w + 36 + dimension_left_margin + dimension_right_margin, 720)
            card_h = sheet_h + 138 + legend_h + dimension_top_margin + dimension_bottom_margin
            card_rect = QRectF(x, y, card_w, card_h)
            # Record card bounds for sheet navigation (T1-3) and auto-focus (T1-5).
            self._sheet_card_bounds.append(QRectF(card_rect))
            if self.print_mode:
                card_fill: QBrush = QBrush(QColor("#ffffff"))
                pen_color = QColor("#000000")
            else:
                card_gradient = QLinearGradient(x, y, x, y + card_h)
                if missing:
                    # Karta brakującej płyty: subtelne czerwone tło + ostrzejszy
                    # akcent obwódki, ale spójny z designem (nie krzykliwy).
                    card_gradient.setColorAt(0, QColor(248, 113, 113, 22))   # ~rgba(danger, 0.085)
                    card_gradient.setColorAt(1, QColor(248, 113, 113, 12))
                    pen_color = QColor(248, 113, 113, 110)                   # rgba(danger, 0.43)
                else:
                    card_gradient.setColorAt(0, QColor(str(palette["card_alt"])))
                    card_gradient.setColorAt(1, QColor(str(palette["card"])))
                    pen_color = QColor(str(palette["border"]))
                card_fill = QBrush(card_gradient)
            self._rounded_rect(card_rect, 8, card_fill, QPen(pen_color, 1.0))
            self._draw_technical_grid(card_rect)

            title = base_label
            if group_label and group_label.casefold() != material.casefold():
                title += f" · {group_label}"
            title_color = QColor(str(palette["danger"])) if missing else QColor(str(palette["text"]))
            title_item = self._draw_label(title, x + 18, y + 13, 11, title_color, bold=True)
            badge_code, badge_background, badge_foreground = _material_badge_style(material)
            display_label = f"Wymiary: {display_w:.2f} x {display_h:.2f} mm"
            display_font = QFont("Segoe UI")
            display_font.setPointSize(10)
            display_width = QFontMetrics(display_font).horizontalAdvance(display_label)
            badge_width = max(38, len(badge_code) * 7 + 12) if badge_code else 0
            title_available = card_w - 36 - display_width - 24 - badge_width
            title_item.setPlainText(
                self._elided_label_text(title, 11, max(120.0, title_available), bold=True)
            )
            if badge_code:
                badge_x = x + 24 + title_item.boundingRect().width()
                badge_rect = QRectF(badge_x, y + 10, badge_width, 20)
                badge_shape = self._rounded_rect(
                    badge_rect,
                    6,
                    QBrush(badge_background),
                    QPen(QColor(255, 255, 255, 48), 0.8),
                )
                badge_shape.setZValue(1)
                badge_item = self._draw_label(badge_code, badge_x, y + 13, 8, badge_foreground, bold=True)
                badge_item.setPos(badge_x + (badge_rect.width() - badge_item.boundingRect().width()) / 2, y + 13)
                badge_item.setZValue(2)
            # Centered "Wykorzystanie" label — independent of title length, so it
            # never overlaps the (longer) "Brakująca płyta N" header.
            util_color = QColor(str(palette["danger"])) if missing else QColor(str(palette["success"]))
            util_text = f"Wykorzystanie: {layout.utilization:.1f}%"
            util_item = self._draw_label(util_text, 0, y + 38, 10, util_color, bold=True)
            util_w = util_item.boundingRect().width()
            util_item.setPos(x + (card_w - util_w) / 2, y + 38)
            self._draw_right_label(
                display_label,
                x + card_w - 18,
                y + 13,
                10,
                QColor(str(palette["muted"])),
            )

            sheet_x = x + 6
            sheet_y = y + 88
            sheet_rect = QRectF(sheet_x, sheet_y, sheet_w, sheet_h)
            if self.print_mode:
                sheet_brush = QBrush(QColor("#ffffff"))
            else:
                sheet_brush = QBrush(QColor(str(palette["sheet"])))
            sheet_item = self._rounded_rect(
                sheet_rect,
                6,
                sheet_brush,
                QPen(QColor(str(palette["sheet_pen"])), 1.1),
            )
            sheet_item.setZValue(0)
            sheet_item.setToolTip(self._stock_tooltip(layout, missing))
            sheet_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            if display_rotated:
                self._draw_dimensioning(sheet_rect, display_w, display_h)
                self._draw_segment_dimensioning(layout, sheet_rect, scale, display_rotated)
                self._draw_short_side_dimensioning(layout, sheet_rect, scale, display_rotated)
            else:
                # Cut strips (boxes) disabled per user request, but keep the simple
                # dimension lines (brackets) around the board.
                self._draw_dimensioning(sheet_rect, display_w, display_h)
                self._draw_segment_dimensioning(layout, sheet_rect, scale, display_rotated)
                self._draw_short_side_dimensioning(layout, sheet_rect, scale, display_rotated)

            for ox, oy, ow, oh in layout.offcuts:
                if missing:
                    if ox >= display_w - 0.001 or oy >= display_h - 0.001:
                        continue
                    ow = min(ow, display_w - ox)
                    oh = min(oh, display_h - oy)
                    if ow <= 0.001 or oh <= 0.001:
                        continue
                dx, dy, dw, dh = self._map_rect_to_display(layout, (ox, oy, ow, oh), display_rotated)
                offcut = self.scene.addRect(
                    QRectF(sheet_x + dx * scale, sheet_y + dy * scale, dw * scale, dh * scale),
                    QPen(QColor(str(palette["offcut_pen"])), 0.8, Qt.PenStyle.DashLine),
                    palette["offcut"],
                )
                offcut.setToolTip(f"Odpad {ow:.0f} x {oh:.0f} mm")
                offcut.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            segment_color = QColor("#000000") if self.print_mode else (QColor(47, 128, 255, 95) if self.theme == "light" else QColor(76, 201, 255, 120))
            segment_pen = QPen(segment_color, 0.8, Qt.PenStyle.DashLine)
            segment_pen.setCosmetic(True)
            segment_boundaries: set[float] = set()
            for segment in getattr(layout, "vertical_segments", []):
                try:
                    segment_boundaries.add(float(segment.get("x", 0.0)))
                    segment_boundaries.add(float(segment.get("right", 0.0)))
                except (TypeError, ValueError, AttributeError):
                    continue
            # Boundaries that coincide exactly with any part's left or right edge
            # are already visually represented by the part rectangle itself.  Drawing
            # an extra dashed line at those positions creates a false impression of an
            # "illegal cut through the part", so suppress them.
            _EPS_BND = 0.5  # mm tolerance — snap segments at sub-mm positions
            _part_x_edges: set[float] = set()
            for _p in layout.parts:
                _part_x_edges.add(round(_p.x, 1))
                _part_x_edges.add(round(_p.x + _p.width, 1))
            boundary_limit = display_w if missing and not display_rotated else layout.stock.width
            for boundary in sorted(segment_boundaries):
                if boundary <= 0.001 or boundary >= boundary_limit - 0.001:
                    continue
                # Skip if this boundary is an exact part edge (already visually clear)
                if any(abs(boundary - edge) < _EPS_BND for edge in _part_x_edges):
                    continue
                if display_rotated:
                    line_y = sheet_y + (layout.stock.width - boundary) * scale
                    line = self.scene.addLine(sheet_x, line_y, sheet_x + sheet_w, line_y, segment_pen)
                    line.setToolTip(f"Granica pionowego segmentu: x={boundary:.0f} mm (widok obrócony)")
                    line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                else:
                    line_x = sheet_x + boundary * scale
                    line = self.scene.addLine(line_x, sheet_y, line_x, sheet_y + sheet_h, segment_pen)
                    line.setToolTip(f"Granica pionowego segmentu: x={boundary:.0f} mm")
                    line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            for part in layout.parts:
                dx, dy, dw, dh = self._map_rect_to_display(layout, (part.x, part.y, part.width, part.height), display_rotated)
                rect_x = sheet_x + dx * scale
                rect_y = sheet_y + dy * scale
                rect_w = dw * scale
                rect_h = dh * scale
                _, _, border = self._part_colors(part.part.width, part.part.height, missing)
                if self.print_mode:
                    border = QColor("#000000")
                rect = QGraphicsRectItem(rect_x, rect_y, rect_w, rect_h)
                is_bonus = getattr(part.part, "is_waste_fill", False)
                rect.setBrush(self._part_brush(rect_x, rect_y, rect_w, rect_h, part.part.width, part.part.height, missing))
                if is_bonus:
                    bonus_pen = QPen(border, 1.2, Qt.PenStyle.DashLine)
                    rect.setPen(bonus_pen)
                else:
                    rect.setPen(QPen(border, 0.9))
                rotation_note = "\nObrócona: tak" if part.rotated else "\nObrócona: nie"
                bonus_note = "\n⊕ Bonus — cięcie z odpadu dodatkowego materiału" if is_bonus else ""
                rect.setToolTip(f"{part.part.name}\n{part.width:.0f} x {part.height:.0f} mm\nx={part.x:.0f}, y={part.y:.0f}{rotation_note}{bonus_note}")
                rect.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene.addItem(rect)
                # T1-4: register so legend clicks can highlight all matching parts.
                key = _dimension_key(part.part.width, part.part.height)
                self._part_rects_by_key.setdefault(key, []).append(rect)
                # Remember the original pen so we can restore it after highlight clears.
                rect.setData(0, rect.pen())
                symbol = self._part_symbol(part.part.width, part.part.height)
                dimension_text = f"{part.part.width:.0f} × {part.part.height:.0f}"
                self._draw_part_label(rect, symbol, dimension_text, self._part_text_color(part.part.width, part.part.height, missing))

            self._draw_cut_operations_overlay(layout, sheet_rect, scale, display_rotated)
            self._draw_legend(legend_entries, x + 18, sheet_y + sheet_h + dimension_bottom_margin + 12, card_w - 36)

            y += card_h + 24
        return y

    def _draw_linear_layouts(self, layouts: list[LinearLayout]) -> None:
        palette = self._palette()
        y = 40.0
        scale = 0.8
        for layout in layouts:
            x = 50.0
            length = layout.stock.length * scale
            self._draw_label(f"Pręt {layout.bar_index} - {layout.stock.material} {layout.utilization:.1f}% odpad {layout.leftover:.0f} mm", x, y - 28, 10)
            self.scene.addRect(QRectF(x, y, length, 34), QPen(QColor(str(palette["sheet_pen"])), 1.3), QColor(str(palette["sheet"])))
            for placement in layout.placements:
                rect_x = x + placement.start * scale
                rect_w = placement.length * scale
                rect = self.scene.addRect(QRectF(rect_x, y, rect_w, 34), QPen(QColor("#bbf7d0"), 0.8), QColor("#22c55e"))
                rect.setToolTip(f"{placement.part.name}\nstart {placement.start:.0f} mm\nlength {placement.length:.0f} mm")
                rect.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
                self._draw_label(placement.part.label or placement.part.name, rect_x + 4, y + 5, 7)
            y += 72
