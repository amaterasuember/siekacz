from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene


def export_scene_png(
    scene: QGraphicsScene,
    path: str,
    width: int = 2400,
    background: QColor | int | str = 0xFFF3F7FB,
    header_text: str = "",
    include_cut_time_box: bool = True,
) -> None:
    bounds = scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
    if bounds.isEmpty():
        bounds = QRectF(0, 0, 1200, 800)
    scale = width / max(bounds.width(), 1.0)

    # ── Cut-time info box: right-aligned, generous height so handwritten
    # dates/times actually fit.  Each entry gets a label row + a drawn
    # underline so there is a clear, wide space to write on.
    content_right_x = max(1.0, width - 30.0 * scale)
    box_top_px = 44   # enough top margin so printer doesn't clip the box
    # Width: ~42% of image, min 820 px, max 1200 px.
    box_width_px = int(max(820, min(1200, width * 0.42)))
    box_left_px = max(80, int(content_right_x) - box_width_px)
    # Height: pad_top(16) + label(32) + write_gap(44) + section_gap(20)
    #       + label(32) + write_gap(44) + pad_bottom(16) = 204
    box_height_px = 204
    box_right_px = box_left_px + box_width_px

    header_height = (box_top_px + box_height_px + 22) if include_cut_time_box else (96 if header_text else 0)
    height = max(1, int(bounds.height() * scale) + header_height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    fill = background if isinstance(background, QColor) else QColor(background) if isinstance(background, str) else background
    image.fill(fill)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    if header_text:
        painter.setPen(QPen(QColor("#111827")))
        font = QFont("Segoe UI")
        font.setPointSize(22)
        font.setBold(True)
        painter.setFont(font)
        # Header text fills the area to the left of the (now wider) cut-time
        # box, with a 32-pixel gutter.
        right_reserved = int(width - box_left_px + 32) if include_cut_time_box else 88
        painter.drawText(
            QRectF(44, 20, max(120, width - right_reserved), 56),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            header_text,
        )
        painter.setPen(QPen(QColor("#d1d5db"), 2))
        painter.drawLine(44, header_height - 14, width - 44, header_height - 14)

    if include_cut_time_box:
        box = QRectF(float(box_left_px), float(box_top_px), float(box_width_px), float(box_height_px))
        painter.setPen(QPen(QColor("#111827"), 2.0))
        painter.drawRoundedRect(box, 14, 14)

        # Layout constants (all in px)
        pad_x = 28          # horizontal inner padding
        pad_top = 16        # top inner padding
        label_h = 32        # height of label text row (bigger font needs more)
        write_gap = 44      # space between label bottom and write-line
        section_gap = 20    # gap between the two sections
        inner_x = box.left() + pad_x
        inner_w = box.width() - 2 * pad_x

        # Label font — clearly legible
        font_label = QFont("Segoe UI")
        font_label.setPointSize(16)
        font_label.setBold(False)
        painter.setFont(font_label)
        painter.setPen(QPen(QColor("#6b7280")))   # muted grey for label

        # ── Section 1: Rozpoczęcie cięcia ──────────────────────────────
        sec1_top = box.top() + pad_top
        painter.drawText(
            QRectF(inner_x, sec1_top, inner_w, label_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Rozpoczęcie cięcia:",
        )
        # Drawn underline — the actual writing line
        line1_y = sec1_top + label_h + write_gap
        painter.setPen(QPen(QColor("#374151"), 2.0))
        painter.drawLine(
            int(inner_x), int(line1_y),
            int(inner_x + inner_w), int(line1_y),
        )

        # ── Section 2: Zakończenie cięcia ──────────────────────────────
        sec2_top = line1_y + section_gap
        painter.setPen(QPen(QColor("#6b7280")))
        painter.drawText(
            QRectF(inner_x, sec2_top, inner_w, label_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Zakończenie cięcia:",
        )
        line2_y = sec2_top + label_h + write_gap
        painter.setPen(QPen(QColor("#374151"), 2.0))
        painter.drawLine(
            int(inner_x), int(line2_y),
            int(inner_x + inner_w), int(line2_y),
        )
        if not header_text:
            painter.setPen(QPen(QColor("#d1d5db"), 2))
            painter.drawLine(44, header_height - 14, width - 44, header_height - 14)

    scene.render(painter, QRectF(0, header_height, width, height - header_height), bounds)
    painter.end()
    image.save(path, "PNG")
