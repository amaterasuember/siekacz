from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QIODevice, QRectF, QSaveFile, Qt
from PySide6.QtGui import QColor, QFont, QImage, QImageWriter, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene


def export_scene_png(
    scene: QGraphicsScene,
    path: str,
    width: int = 2400,
    background: QColor | int | str = 0xFFF3F7FB,
    header_text: str = "",
    include_cut_time_box: bool = True,
) -> None:
    if isinstance(width, bool) or not isinstance(width, int) or not 320 <= width <= 16000:
        raise ValueError("Szerokość PNG musi wynosić od 320 do 16000 pikseli.")
    bounds = scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
    if bounds.isEmpty():
        bounds = QRectF(0, 0, 1200, 800)
    scale = width / max(bounds.width(), 1.0)
    output_width = width
    # Lay out the header at a readable design width, then scale the entire
    # header together. Fixed-size boxes otherwise clip in smaller exports.
    width = max(2400, width)
    header_scale = output_width / width

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

    header_height = (box_top_px + box_height_px + 22) if include_cut_time_box else (96 if header_text else 0)
    header_pixels = math.ceil(header_height * header_scale)
    content_height = bounds.height() * scale
    if not math.isfinite(content_height) or content_height < 0:
        raise ValueError("Nieprawidłowy obszar podglądu do eksportu.")
    height = max(1, math.ceil(content_height) + header_pixels)
    if output_width * height > 64_000_000:
        raise ValueError("Rozkrój jest za duży dla jednego PNG. Eksportuj wybrane płyty osobno lub wybierz raport PDF.")
    image = QImage(output_width, height, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise RuntimeError("Brak pamięci na obraz PNG. Zmniejsz rozmiar eksportu.")
    fill = background if isinstance(background, QColor) else QColor(background) if isinstance(background, str) else background
    image.fill(fill)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(header_scale, header_scale)

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
            QRectF(44, 20, max(120, width - right_reserved - 44), max(56, header_height - 54)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
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

    painter.resetTransform()
    try:
        scene.render(painter, QRectF(0, header_pixels, output_width, height - header_pixels), bounds)
    finally:
        painter.end()
    output = QSaveFile(path)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(f"Nie można zapisać PNG: {output.errorString()}")
    try:
        writer = QImageWriter(output, QByteArray(b"PNG"))
        if not writer.write(image):
            raise OSError(f"Nie udało się zakodować PNG: {writer.errorString()}")
        if not output.commit():
            raise OSError(f"Nie można zapisać PNG: {output.errorString()}")
    finally:
        if output.isOpen():
            output.cancelWriting()
            output.commit()  # discard the temporary file and close the device
