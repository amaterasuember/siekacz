from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from algorithms.cut_metrics import compute_cut_summary
from algorithms.layout_grouping import format_sheet_number_ranges, group_identical_layouts
from algorithms.layout_scoring import format_cut_time
from core.models import OptimizationResult, Project, SheetLayout

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
# Bigger margins than before — consumer/office printers clip ~8–12 mm at the
# edges, which was eating the dimension labels and the page footer.
PAGE_MARGIN = 18 * mm
SHEETS_PER_PAGE = 2          # max board drawings per A4 page (user requirement)
SHEETS_PER_PDF = 12          # split into continuation files beyond this many boards

# ---------------------------------------------------------------------------
# Font registration — Polish characters (ą ć ę ł ń ó ś ź ż) are not present
# in the PDF built-in Helvetica Type1 font.
# ---------------------------------------------------------------------------
_PDF_FONT = "Helvetica"
_PDF_FONT_BOLD = "Helvetica-Bold"


def _register_unicode_font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"), "ArialPL", "ArialPL-Bold"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
         "DejaVuPL", "DejaVuPL-Bold"),
        (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf"), "ArialPL", "ArialPL-Bold"),
    ]
    for reg_path, bold_path, name, bold_name in candidates:
        if reg_path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(reg_path)))
                if bold_path.exists():
                    pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
                else:
                    bold_name = name
                return name, bold_name
            except Exception as exc:
                _logger.warning("Could not register PDF font %s: %s", reg_path, exc)
    _logger.warning("No Unicode TTF font found for PDF export — Polish characters may appear as '?'.")
    return "Helvetica", "Helvetica-Bold"


_PDF_FONT, _PDF_FONT_BOLD = _register_unicode_font()


def _safe_str(value: object) -> str:
    if value is None or str(value).strip() == "":
        return "—"
    return str(value)


def _table(data: list[list[object]], widths: list[float] | None = None) -> Table:
    safe_data = [[_safe_str(cell) for cell in row] for row in data]
    table = Table(safe_data, colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153e63")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6dee8")),
                ("FONTNAME", (0, 0), (-1, -1), _PDF_FONT),
                ("FONTNAME", (0, 0), (-1, 0), _PDF_FONT_BOLD),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
            ]
        )
    )
    return table


# ---------------------------------------------------------------------------
# Visual sheet drawing flowable
# ---------------------------------------------------------------------------
class SheetFlowable(Flowable):
    """Draws one board layout (stock + placed parts) scaled to fit a fixed box.

    A repeated layout is marked with an ``×N`` badge in the top-right corner so
    the operator cuts one drawing N times instead of flipping through N copies.
    """

    _BOARD_FILL = colors.HexColor("#e8f1fb")
    _BOARD_GRID = colors.HexColor("#d4e2f1")
    _PART_FILL = colors.HexColor("#fafdff")
    _PART_STROKE = colors.HexColor("#25486b")
    _STOCK_STROKE = colors.HexColor("#17324d")
    _MISSING_FILL = colors.HexColor("#fff7ed")
    _MISSING_STROKE = colors.HexColor("#9a3412")

    def __init__(self, layout: SheetLayout, count: int, avail_w: float, avail_h: float,
                 caption: str, missing: bool = False, display_orientation: str = "horizontal", symbol_map: dict | None = None) -> None:
        super().__init__()
        self.layout = layout
        self.count = max(1, int(count))
        self.width = avail_w
        self.height = avail_h
        self.caption = caption
        self.missing = missing
        self.symbol_map = symbol_map

    def _map_rect(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        if getattr(self, "display_rotated", False):
            return y, self.layout.stock.width - (x + w), h, w
        return x, y, w, h

    def wrap(self, avail_w: float, avail_h: float) -> tuple[float, float]:
        self.width = avail_w
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()

        # ── Caption line above the drawing ──────────────────────────────────
        caption_h = 12
        canvas.setFont(_PDF_FONT_BOLD, 9)
        canvas.setFillColor(self._MISSING_STROKE if self.missing else self._STOCK_STROKE)
        canvas.drawString(0, self.height - 9, self.caption)

        draw_top = self.height - caption_h - 4
        draw_h = max(10.0, draw_top)
        draw_w = self.width

        stock_w = float(self.layout.stock.width) or 1.0
        stock_h = float(self.layout.stock.height) or 1.0

        if getattr(self, "display_orientation", "horizontal") == "vertical":
            self.display_rotated = stock_w > stock_h
        else:
            self.display_rotated = stock_h > stock_w

        if self.display_rotated:
            self.display_sw = stock_h
            self.display_sh = stock_w
        else:
            self.display_sw = stock_w
            self.display_sh = stock_h

        # Leave a margin for dimension labels around the board.
        pad = 16.0
        scale = min((draw_w - 2 * pad) / self.display_sw, (draw_h - 2 * pad) / self.display_sh)
        scale = max(scale, 0.0001)
        board_w = self.display_sw * scale
        board_h = self.display_sh * scale
        ox = pad
        oy = (draw_h - board_h) / 2.0

        # A restrained technical-board treatment keeps the drawing readable on
        # screen and in colour print without disguising the actual geometry.
        canvas.setFillColor(colors.HexColor("#dce6f1"))
        canvas.roundRect(ox + 1.5, oy - 1.5, board_w, board_h, 4, stroke=0, fill=1)
        canvas.setLineWidth(1.1)
        canvas.setStrokeColor(self._STOCK_STROKE)
        canvas.setFillColor(self._BOARD_FILL)
        canvas.roundRect(ox, oy, board_w, board_h, 4, stroke=1, fill=1)

        canvas.saveState()
        clip = canvas.beginPath()
        clip.rect(ox, oy, board_w, board_h)
        canvas.clipPath(clip, stroke=0, fill=0)
        canvas.setStrokeColor(self._BOARD_GRID)
        canvas.setLineWidth(0.22)
        grid_step = max(16.0, min(34.0, min(board_w, board_h) / 9.0))
        x = ox + grid_step
        while x < ox + board_w:
            canvas.line(x, oy, x, oy + board_h)
            x += grid_step
        y = oy + grid_step
        while y < oy + board_h:
            canvas.line(ox, y, ox + board_w, y)
            y += grid_step
        canvas.restoreState()

        part_fill = self._MISSING_FILL if self.missing else self._PART_FILL
        part_stroke = self._MISSING_STROKE if self.missing else self._PART_STROKE

        for placement in self.layout.parts:
            lx, ly, lw, lh = self._map_rect(placement.x, placement.y, placement.width, placement.height)
            px = ox + lx * scale
            py = oy + board_h - (ly + lh) * scale
            pw = lw * scale
            ph = lh * scale
            canvas.setLineWidth(0.65)
            canvas.setStrokeColor(part_stroke)
            canvas.setFillColor(part_fill)
            canvas.rect(px, py, pw, ph, stroke=1, fill=1)
            self._label_part(canvas, placement, px, py, pw, ph, part_stroke)

        # Draw dimensions along the sides
        vbands = self._compute_bands("x")
        hbands = self._compute_bands("y")
        
        canvas.setFont(_PDF_FONT, 7)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.setStrokeColor(colors.HexColor("#94a3b8"))
        canvas.setLineWidth(0.5)

        tick = 2.5
        
        def _band_label(group) -> str:
            if bool(group["uniform"]) and int(group["n"]) > 1:
                return f"{int(group['n'])} x {group['w']:.0f}"
            return f"{group['total']:.0f}"

        top_y = oy + board_h + 8
        left_x = ox - 12

        if self.display_rotated:
            # Rotated: layout X goes to left edge, layout Y goes to top edge
            sw = self.layout.stock.width
            for g in vbands:
                y2 = oy + board_h - (sw - g["end"]) * scale
                y1 = oy + board_h - (sw - g["start"]) * scale
                if y2 - y1 < 10: continue
                canvas.line(left_x, y1, left_x, y2)
                canvas.line(left_x - tick, y1, left_x + tick, y1)
                canvas.line(left_x - tick, y2, left_x + tick, y2)
                lbl = _band_label(g)
                canvas.saveState()
                canvas.translate(left_x - 3, (y1+y2)/2)
                canvas.rotate(90)
                canvas.drawCentredString(0, 0, lbl)
                canvas.restoreState()
            for g in hbands:
                x1 = ox + g["start"] * scale
                x2 = ox + g["end"] * scale
                if x2 - x1 < 10: continue
                canvas.line(x1, top_y, x2, top_y)
                canvas.line(x1, top_y - tick, x1, top_y + tick)
                canvas.line(x2, top_y - tick, x2, top_y + tick)
                lbl = _band_label(g)
                canvas.drawCentredString((x1+x2)/2, top_y + 3, lbl)
        else:
            # Normal: layout X goes to top edge, layout Y goes to left edge
            for g in vbands:
                x1 = ox + g["start"] * scale
                x2 = ox + g["end"] * scale
                if x2 - x1 < 10: continue
                canvas.line(x1, top_y, x2, top_y)
                canvas.line(x1, top_y - tick, x1, top_y + tick)
                canvas.line(x2, top_y - tick, x2, top_y + tick)
                lbl = _band_label(g)
                canvas.drawCentredString((x1+x2)/2, top_y + 3, lbl)
            for g in hbands:
                y2 = oy + board_h - g["start"] * scale
                y1 = oy + board_h - g["end"] * scale
                if y2 - y1 < 10: continue
                canvas.line(left_x, y1, left_x, y2)
                canvas.line(left_x - tick, y1, left_x + tick, y1)
                canvas.line(left_x - tick, y2, left_x + tick, y2)
                lbl = _band_label(g)
                canvas.saveState()
                canvas.translate(left_x - 3, (y1+y2)/2)
                canvas.rotate(90)
                canvas.drawCentredString(0, 0, lbl)
                canvas.restoreState()

        canvas.restoreState()

    def _compute_bands(self, axis: str) -> list[dict]:
        parts = list(getattr(self.layout, "parts", []) or [])
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

        merge_gap = 8.0
        blocks: list[dict] = []
        width_tol = 3.0
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

    def _label_part(self, canvas, placement, px, py, pw, ph, color) -> None:
        name = getattr(placement.part, "name", "") or ""
        sym = ""
        if hasattr(self, "symbol_map") and self.symbol_map:
            key = (max(placement.part.width, placement.part.height), min(placement.part.width, placement.part.height))
            sym = self.symbol_map.get(key, "")

        # The dimension is the primary label.  It is anchored to the lower
        # right end of the longer edge and is retained even when a small part
        # cannot also carry its symbol.
        dim_text = f"{placement.part.width:.0f}x{placement.part.height:.0f}"
        portrait = ph > pw
        available = ph if portrait else pw
        dim_fs = max(3.0, min(7.0, available / max(3.0, len(dim_text) * 0.62)))
        margin = max(1.2, min(3.0, min(pw, ph) * 0.08))
        canvas.setFillColor(color)
        canvas.setFont(_PDF_FONT, dim_fs)
        if portrait:
            canvas.saveState()
            canvas.translate(px + pw - margin, py + margin)
            canvas.rotate(90)
            canvas.drawRightString(ph - 2 * margin, 0, dim_text)
            canvas.restoreState()
        else:
            canvas.drawRightString(px + pw - margin, py + margin, dim_text)

        text = sym or name[:14]
        if not text:
            return

        # A symbol is only useful when it has its own area.  On dense layouts
        # this deliberately leaves just the dimension instead of overlapping
        # the two labels.  Rotating portrait symbols makes their baseline run
        # along the longer side, so a turned 40x41 part remains obvious.
        long_edge = ph if portrait else pw
        short_edge = pw if portrait else ph
        max_fs_long = long_edge * 0.62 / max(1.0, len(text) * 0.60)
        max_fs_short = short_edge * 0.40
        symbol_fs = min(14.0, max_fs_long, max_fs_short)
        required_cross = dim_fs * 1.75 + symbol_fs * 1.45 + margin * 3.0
        if symbol_fs < 4.0 or short_edge < required_cross:
            return

        cx = px + pw / 2.0
        cy = py + ph / 2.0
        canvas.setFont(_PDF_FONT_BOLD, symbol_fs)
        if portrait:
            canvas.saveState()
            canvas.translate(cx, cy)
            canvas.rotate(90)
            canvas.drawCentredString(0, -symbol_fs * 0.35, text)
            canvas.restoreState()
        else:
            canvas.drawCentredString(cx, cy - symbol_fs * 0.35, text)

    def _draw_count_badge(self, canvas, right_x, top_y) -> None:
        label = f"×{self.count}"
        canvas.setFont(_PDF_FONT_BOLD, 9)
        text_w = canvas.stringWidth(label, _PDF_FONT_BOLD, 9)
        r = max(9.0, text_w / 2.0 + 5.0)
        cx = right_x - r + 2
        cy = top_y - r + 2
        canvas.setFillColor(colors.HexColor("#f59e0b"))
        canvas.setStrokeColor(colors.HexColor("#7c2d12"))
        canvas.setLineWidth(0.8)
        canvas.circle(cx, cy, r, stroke=1, fill=1)
        canvas.setFillColor(colors.HexColor("#1f2937"))
        canvas.drawCentredString(cx, cy - 3, label)

    def _draw_cut_operations(self, canvas, ox, oy, board_w, board_h, scale) -> None:
        return  # CUT SEQUENCES DISABLED TEMPORARILY
        operations = sorted(getattr(self.layout, "cut_operations", []) or [], key=lambda op: int(getattr(op, "step", 0)))
        if not operations:
            return
        for op in operations:
            orientation = str(getattr(op, "orientation", ""))
            x = float(getattr(op, "x", 0.0) or 0.0)
            y = float(getattr(op, "y", 0.0) or 0.0)
            length = float(getattr(op, "length", 0.0) or 0.0)
            if length <= 0.0:
                continue
            if orientation == "vertical":
                x1 = ox + x * scale
                y1 = oy + board_h - y * scale
                x2 = x1
                y2 = oy + board_h - (y + length) * scale
            else:
                x1 = ox + x * scale
                y1 = oy + board_h - y * scale
                x2 = ox + (x + length) * scale
                y2 = y1
            kind = str(getattr(op, "kind", "cut"))
            color = colors.HexColor("#000000")
            canvas.setStrokeColor(color)
            canvas.setLineWidth(0.45)
            canvas.line(x1, y1, x2, y2)
            step = str(int(getattr(op, "step", 0) or 0))
            mx = (x1 + x2) / 2.0
            my = (y1 + y2) / 2.0
            r = max(3.2, 2.4 + len(step) * 1.1)
            canvas.setFillColor(colors.HexColor("#ffffff"))
            canvas.setStrokeColor(color)
            canvas.circle(mx, my, r, stroke=1, fill=1)
            canvas.setFillColor(colors.HexColor("#111827"))
            canvas.setFont(_PDF_FONT_BOLD, 4.6)
            canvas.drawCentredString(mx, my - 1.6, step)


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------
def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("IndustrialTitle", parent=base["Title"],
                                 textColor=colors.HexColor("#0f3d66"), fontName=_PDF_FONT_BOLD,
                                 fontSize=22, leading=26, spaceAfter=4),
        "heading": ParagraphStyle("PDFHeading2", parent=base["Heading2"], fontName=_PDF_FONT_BOLD,
                                   textColor=colors.HexColor("#153e63"), fontSize=15, leading=19,
                                   spaceBefore=8, spaceAfter=5),
        "normal": ParagraphStyle("PDFBodyText", parent=base["BodyText"], fontName=_PDF_FONT,
                                  textColor=colors.HexColor("#334155"), leading=14),
    }


def _front_matter(story, project: Project, result: OptimizationResult | None, company_name: str, styles) -> None:
    story.append(Paragraph(_safe_str(company_name or "SIEKACZ 9000"), styles["title"]))
    story.append(Paragraph("Raport rozkroju", styles["heading"]))
    story.append(Spacer(1, 5 * mm))
    meta = project.meta
    story.append(_table(
        [
            ["Klient", meta.client_name],
            ["Numer zamówienia", meta.order_number],
            ["Materiał", meta.material],
            ["Utworzono", meta.creation_date],
            ["Data raportu", datetime.now().strftime("%Y-%m-%d %H:%M")],
            ["Uwagi", meta.notes],
        ],
        [40 * mm, 120 * mm],
    ))
    story.append(Spacer(1, 6 * mm))

    if result:
        _cut_metrics_section(story, result, styles)

    if project.sheet_parts:
        rows = [["Grubość", "Formatka", "Szer.", "Wys.", "Ilość", "Materiał", "Etykieta"]]
        rows.extend([[p.thickness, p.name, p.width, p.height, p.quantity, p.material, p.label]
                     for p in project.sheet_parts])
        story.append(Paragraph("Formatki płytowe", styles["heading"]))
        story.append(_table(rows))
        story.append(Spacer(1, 5 * mm))

def _cut_metrics_section(story, result: OptimizationResult, styles) -> None:
    try:
        summary = compute_cut_summary(result)
    except Exception as exc:  # never let metrics crash the whole report
        _logger.warning("Cut-metric summary failed: %s", exc)
        return
    if not summary.formats:
        return
    story.append(Paragraph("Parametry realizacji", styles["heading"]))
    rows: list[list[object]] = [[
        "Format", "Szt.", "Cięć", "mb piły",
        "Netto m²", "Odpad m²", "Rozlicz. m²",
    ]]
    for fmt in summary.formats:
        rows.append([
            f"{fmt.width:.0f}×{fmt.height:.0f}",
            fmt.pieces,
            fmt.cuts,
            f"{fmt.saw_m:.2f}",
            f"{fmt.area_m2:.3f}",
            f"{fmt.waste_m2:.3f}",
            f"{fmt.gross_m2:.3f}",
        ])
    rows.append([
        "RAZEM", summary.total_pieces, summary.total_cuts,
        f"{summary.total_saw_m:.2f}", f"{summary.total_area_m2:.3f}",
        f"{summary.total_waste_m2:.3f}",
        f"{summary.total_board_area_m2:.3f}",
    ])
    table = _table(rows, [44 * mm, 14 * mm, 15 * mm, 24 * mm, 25 * mm, 25 * mm, 28 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, -1), (-1, -1), _PDF_FONT_BOLD),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e5edf5")),
    ]))
    story.append(table)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Łączna trasa piły: <b>{summary.total_saw_m:.2f} mb</b> &nbsp;|&nbsp; "
        f"cięć: <b>{summary.total_cuts}</b> &nbsp;|&nbsp; "
        f"obrotów: <b>{summary.total_rotated}</b> &nbsp;|&nbsp; "
        f"netto: <b>{summary.total_area_m2:.3f} m²</b> &nbsp;|&nbsp; "
        f"odpad produkcyjny: <b>{summary.total_waste_m2:.3f} m²</b> &nbsp;|&nbsp; "
        f"szacowany czas: <b>{format_cut_time(summary.total_time_s)}</b>",
        styles["normal"],
    ))
    story.append(Spacer(1, 6 * mm))


def _drawing_box() -> tuple[float, float]:
    avail_w = A4[0] - 2 * PAGE_MARGIN
    avail_h = A4[1] - 2 * PAGE_MARGIN
    # Reserve space for the section heading on the first drawing page.
    block_h = (avail_h - 8 * mm) / SHEETS_PER_PAGE - 6 * mm
    return avail_w, block_h


def _append_drawings(story, groups, styles, missing: bool, block_w: float, block_h: float, display_orientation: str = "horizontal", symbol_map: dict | None = None) -> None:
    for group in groups:
        layout = group.representative
        sheet_no = getattr(layout, "sheet_index", "?")
        util = f"{layout.utilization:.0f}%"
        cuts = int(getattr(layout, "cut_count", 0))
        t = format_cut_time(getattr(layout, "estimated_cut_time_s", 0.0))
        sheet_numbers = list(getattr(group, "display_sheet_indices", [])) or [getattr(layout, "display_sheet_index", sheet_no)]
        number_text = f"nr {format_sheet_number_ranges(sheet_numbers)}"
        thickness = float(getattr(layout.stock, "thickness", 0.0) or 0.0)
        missing_text = "brakująca, " if missing else ""
        material = str(getattr(layout.stock, "material", "") or "Materiał").strip()
        caption = (
            f"{material} · gr. {thickness:g} mm — {number_text}  •  "
            f"{layout.stock.width:.0f}×{layout.stock.height:.0f} mm  "
            f"•  wyk. {util}  •  cięć {cuts}  •  {t}"
        )
        story.append(SheetFlowable(layout, group.count, block_w, block_h, caption, missing=missing, display_orientation=display_orientation, symbol_map=symbol_map))
        story.append(Spacer(1, 2 * mm))
        
        # Build legend for this sheet
        legend_parts = {}
        for placement in layout.parts:
            pw, ph = float(placement.part.width), float(placement.part.height)
            key = (max(pw, ph), min(pw, ph))
            if key not in legend_parts:
                sym = symbol_map.get(key, "") if symbol_map else ""
                legend_parts[key] = {
                    "symbol": sym,
                    "width": pw,
                    "height": ph,
                    "count": 0
                }
            legend_parts[key]["count"] += 1
            
        if legend_parts:
            items = []
            for k, data in sorted(legend_parts.items(), key=lambda x: x[1]["symbol"]):
                items.append(f"<b>{data['symbol']}</b> — {data['width']:.0f}×{data['height']:.0f} mm ({data['count']} szt.)")
            legend_text = "<b>Legenda formatek:</b> &nbsp;&nbsp;" + " &nbsp;|&nbsp; ".join(items)
            
            # Use a slightly smaller font for the legend
            legend_style = ParagraphStyle(
                'Legend',
                parent=styles["normal"],
                fontSize=7,
                leading=10,
                textColor=colors.HexColor("#334155")
            )
            story.append(Paragraph(legend_text, legend_style))
            story.append(Spacer(1, 3 * mm))

        # The legend belongs directly to the drawing.  The operator's blank
        # time fields are an administrative note and therefore come afterwards.
        time_data = [
            ["Rozpoczęcie cięcia:", ""],
            ["Zakończenie cięcia:", ""],
        ]
        time_table = Table(time_data, colWidths=[35*mm, 60*mm])
        time_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _PDF_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#475569")),
            ("LINEBELOW", (1, 0), (1, 0), 0.5, colors.HexColor("#94a3b8")),
            ("LINEBELOW", (1, 1), (1, 1), 0.5, colors.HexColor("#94a3b8")),
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(time_table)
        story.append(Spacer(1, 6 * mm))


def _build_document(path: Path, project: Project, result: OptimizationResult | None,
                    company_name: str, sheet_groups, missing_groups,
                    continuation_index: int, skip_summary: bool = False,
                    display_orientation: str = "horizontal", symbol_map: dict | None = None) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        rightMargin=PAGE_MARGIN, leftMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
    )
    story: list = []
    block_w, block_h = _drawing_box()

    if continuation_index == 0:
        if not skip_summary:
            _front_matter(story, project, result, company_name, styles)
            if sheet_groups or missing_groups:
                story.append(PageBreak())
        if sheet_groups or missing_groups:
            story.append(Paragraph("Rozkroje płyt", styles["heading"]))
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph(
            f"{_safe_str(company_name or 'SIEKACZ 9000')} — Rozkroje płyt (kontynuacja, część {continuation_index + 1})",
            styles["heading"]))
        story.append(Spacer(1, 3 * mm))

    _append_drawings(story, sheet_groups, styles, False, block_w, block_h, display_orientation, symbol_map)
    if missing_groups:
        story.append(Paragraph("Brakujące płyty", styles["heading"]))
        story.append(Spacer(1, 2 * mm))
        _append_drawings(story, missing_groups, styles, True, block_w, block_h, display_orientation, symbol_map)

    if not story:
        story.append(Paragraph("Brak danych do raportu.", styles["normal"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(_PDF_FONT, 8)
        canvas.setFillColor(colors.HexColor("#5f6b77"))
        canvas.drawRightString(A4[0] - PAGE_MARGIN, PAGE_MARGIN - 6 * mm, f"Strona {document.page}")
        canvas.drawString(PAGE_MARGIN, PAGE_MARGIN - 6 * mm, "SIEKACZ 9000")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def generate_pdf(path: str | Path, project: Project, result: OptimizationResult | None,
                 company_name: str = "", sheets_per_pdf: int = SHEETS_PER_PDF,
                 skip_summary: bool = False, display_orientation: str = "horizontal") -> list[Path]:
    """Render a visual cutting report.

    Returns the list of written file paths.  ``path`` is always the first file;
    if there are more boards than ``sheets_per_pdf`` the rest go into
    continuation files named ``<stem>_cz2.pdf``, ``<stem>_cz3.pdf`` …
    """
    base = Path(path)
    sheet_groups: list = []
    missing_groups: list = []
    if result:
        sheet_groups = group_identical_layouts(result.sheet_layouts)
        # We do not generate missing sheets in the PDF report anymore per user request.

    symbol_map = {}
    if project:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for part in project.sheet_parts:
            w, h = float(part.width), float(part.height)
            key = (max(w, h), min(w, h))
            if key not in symbol_map:
                idx = len(symbol_map)
                if idx < 26:
                    sym = alphabet[idx]
                else:
                    sym = alphabet[(idx // 26) - 1] + alphabet[idx % 26]
                symbol_map[key] = sym

    # Split the *board groups* into chunks for continuation files.  Front matter
    # always lives in the first file.
    all_groups = [(g, False) for g in sheet_groups] + [(g, True) for g in missing_groups]
    chunk = max(1, int(sheets_per_pdf))
    if not all_groups:
        chunks = [[]]
    else:
        chunks = [all_groups[i:i + chunk] for i in range(0, len(all_groups), chunk)]

    written: list[Path] = []
    for index, group_chunk in enumerate(chunks):
        if index == 0:
            target = base
        else:
            target = base.with_name(f"{base.stem}_cz{index + 1}{base.suffix}")
        s_groups = [g for g, is_missing in group_chunk if not is_missing]
        m_groups = [g for g, is_missing in group_chunk if is_missing]
        _build_document(target, project, result, company_name, s_groups, m_groups, index, skip_summary, display_orientation, symbol_map)
        written.append(target)
    return written
