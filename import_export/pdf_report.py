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
from algorithms.layout_grouping import group_identical_layouts
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
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#26313d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9aa4af")),
                ("FONTNAME", (0, 0), (-1, -1), _PDF_FONT),
                ("FONTNAME", (0, 0), (-1, 0), _PDF_FONT_BOLD),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
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

    _PART_FILL = colors.HexColor("#dbeafe")
    _PART_STROKE = colors.HexColor("#1e3a5f")
    _STOCK_STROKE = colors.HexColor("#111827")
    _MISSING_FILL = colors.HexColor("#fee2e2")
    _MISSING_STROKE = colors.HexColor("#b91c1c")

    def __init__(self, layout: SheetLayout, count: int, avail_w: float, avail_h: float,
                 caption: str, missing: bool = False) -> None:
        super().__init__()
        self.layout = layout
        self.count = max(1, int(count))
        self.width = avail_w
        self.height = avail_h
        self.caption = caption
        self.missing = missing

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

        sw = float(self.layout.stock.width) or 1.0
        sh = float(self.layout.stock.height) or 1.0
        # Leave a margin for dimension labels around the board.
        pad = 16.0
        scale = min((draw_w - 2 * pad) / sw, (draw_h - 2 * pad) / sh)
        scale = max(scale, 0.0001)
        board_w = sw * scale
        board_h = sh * scale
        ox = (draw_w - board_w) / 2.0
        oy = (draw_h - board_h) / 2.0

        # Board outline.
        canvas.setLineWidth(1.1)
        canvas.setStrokeColor(self._STOCK_STROKE)
        canvas.setFillColor(colors.HexColor("#f8fafc"))
        canvas.rect(ox, oy, board_w, board_h, stroke=1, fill=1)

        part_fill = self._MISSING_FILL if self.missing else self._PART_FILL
        part_stroke = self._MISSING_STROKE if self.missing else self._PART_STROKE

        for placement in self.layout.parts:
            # Board origin is top-left in layout coords; PDF origin is bottom-left.
            px = ox + placement.x * scale
            py = oy + board_h - (placement.y + placement.height) * scale
            pw = placement.width * scale
            ph = placement.height * scale
            canvas.setLineWidth(0.6)
            canvas.setStrokeColor(part_stroke)
            canvas.setFillColor(part_fill)
            canvas.rect(px, py, pw, ph, stroke=1, fill=1)
            self._label_part(canvas, placement, px, py, pw, ph, part_stroke)

        self._draw_cut_operations(canvas, ox, oy, board_w, board_h, scale)

        # ×N badge.
        if self.count > 1:
            self._draw_count_badge(canvas, ox + board_w, oy + board_h)

        canvas.restoreState()

    def _label_part(self, canvas, placement, px, py, pw, ph, color) -> None:
        if pw < 14 or ph < 9:
            return
        name = getattr(placement.part, "name", "") or ""
        dims = f"{placement.width:.0f}×{placement.height:.0f}"
        if placement.rotated:
            dims += " ↻"
        canvas.setFillColor(color)
        canvas.setFont(_PDF_FONT_BOLD, 6.5)
        cx = px + pw / 2.0
        cy = py + ph / 2.0
        if name and ph > 20:
            canvas.drawCentredString(cx, cy + 2, name[:14])
            canvas.setFont(_PDF_FONT, 6)
            canvas.drawCentredString(cx, cy - 6, dims)
        else:
            canvas.drawCentredString(cx, cy - 3, dims)

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
        operations = sorted(getattr(self.layout, "cut_operations", []) or [], key=lambda op: int(getattr(op, "step", 0)))
        if not operations:
            return
        if len(operations) > 70:
            reduced = [op for op in operations if getattr(op, "kind", "") in {"rip", "cross"}]
            operations = (reduced or operations)[:70]
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
            color = colors.HexColor("#2563eb" if kind == "rip" else "#d97706" if kind == "cross" else "#7c3aed")
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
                                 textColor=colors.HexColor("#1f4f78"), fontName=_PDF_FONT_BOLD),
        "heading": ParagraphStyle("PDFHeading2", parent=base["Heading2"], fontName=_PDF_FONT_BOLD),
        "normal": ParagraphStyle("PDFBodyText", parent=base["BodyText"], fontName=_PDF_FONT),
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
        metrics_summary = None
        try:
            metrics_summary = compute_cut_summary(result)
        except Exception as exc:
            _logger.warning("Cut-metric summary failed: %s", exc)
        if metrics_summary and metrics_summary.total_board_area_m2 > 0:
            utilization_text = f"{metrics_summary.total_area_m2 / metrics_summary.total_board_area_m2 * 100.0:.1f}%"
            waste_text = (
                f"{metrics_summary.total_waste_m2:.3f} m² ścinki, "
                f"{metrics_summary.total_reusable_m2:.3f} m² resztki"
            )
        else:
            utilization_text = f"{result.utilization:.1f}%"
            waste_text = f"{result.waste:.1f}"
        story.append(Paragraph("Podsumowanie optymalizacji", styles["heading"]))
        story.append(_table(
            [
                ["Typ zlecenia", result.job_type],
                ["Algorytm", result.algorithm],
                ["Układy", len(result.sheet_layouts) or len(result.linear_layouts)],
                ["Dodatkowe arkusze", len(result.missing_sheet_layouts)],
                ["Nieumieszczone formatki", len(result.unplaced_sheet_parts)],
                ["Wykorzystanie rozliczane", utilization_text],
                ["Odpad / resztki", waste_text],
                ["Szacowany koszt", f"{result.total_cost:.2f}"],
            ],
            [50 * mm, 110 * mm],
        ))
        story.append(Spacer(1, 6 * mm))
        _cut_metrics_section(story, result, styles)

    if project.sheet_parts:
        rows = [["Formatka", "Szer.", "Wys.", "Ilość", "Materiał", "Grubość", "Etykieta"]]
        rows.extend([[p.name, p.width, p.height, p.quantity, p.material, p.thickness, p.label]
                     for p in project.sheet_parts])
        story.append(Paragraph("Formatki płytowe", styles["heading"]))
        story.append(_table(rows))
        story.append(Spacer(1, 5 * mm))

    if result and result.reusable_offcuts:
        rows = [["Typ", "Materiał", "Rozmiar", "Źródło"]]
        for item in result.reusable_offcuts:
            size = (f"{item.get('width')} x {item.get('height')}"
                    if item.get("type") == "sheet" else str(item.get("length")))
            rows.append([item.get("type", ""), item.get("material", ""), size, item.get("source", "")])
        story.append(Paragraph("Użyteczne odpady", styles["heading"]))
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
    story.append(Paragraph("Metryki cięcia", styles["heading"]))
    rows: list[list[object]] = [[
        "Format", "Wymiar [mm]", "Szt.", "Obrót", "Cięć", "mb piły",
        "Netto m²", "Odpad m²", "Resztki m²", "Rozlicz. m²",
    ]]
    for fmt in summary.formats:
        rows.append([
            fmt.name,
            f"{fmt.width:.0f}×{fmt.height:.0f}",
            fmt.pieces,
            fmt.rotated or "—",
            fmt.cuts,
            f"{fmt.saw_m:.2f}",
            f"{fmt.area_m2:.3f}",
            f"{fmt.waste_m2:.3f}",
            f"{fmt.reusable_m2:.3f}",
            f"{fmt.gross_m2:.3f}",
        ])
    rows.append([
        "RAZEM", "—", summary.total_pieces, summary.total_rotated,
        summary.total_cuts, f"{summary.total_saw_m:.2f}", f"{summary.total_area_m2:.3f}",
        f"{summary.total_waste_m2:.3f}", f"{summary.total_reusable_m2:.3f}",
        f"{summary.total_board_area_m2:.3f}",
    ])
    table = _table(rows, [26 * mm, 21 * mm, 11 * mm, 11 * mm, 12 * mm, 17 * mm, 16 * mm, 16 * mm, 17 * mm, 17 * mm])
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
        f"resztki magazynowe: <b>{summary.total_reusable_m2:.3f} m²</b> &nbsp;|&nbsp; "
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


def _append_drawings(story, groups, styles, missing: bool, block_w: float, block_h: float) -> None:
    label = "Brakujący arkusz" if missing else "Arkusz"
    for group in groups:
        layout = group.representative
        sheet_no = getattr(layout, "sheet_index", "?")
        util = f"{layout.utilization:.0f}%"
        cuts = int(getattr(layout, "cut_count", 0))
        t = format_cut_time(getattr(layout, "estimated_cut_time_s", 0.0))
        count_txt = f" (×{group.count})" if group.count > 1 else ""
        caption = (
            f"{label} {sheet_no}{count_txt}  •  {layout.stock.width:.0f}×{layout.stock.height:.0f} mm  "
            f"•  wyk. {util}  •  cięć {cuts}  •  {t}"
        )
        story.append(SheetFlowable(layout, group.count, block_w, block_h, caption, missing=missing))
        story.append(Spacer(1, 6 * mm))


def _build_document(path: Path, project: Project, result: OptimizationResult | None,
                    company_name: str, sheet_groups, missing_groups,
                    continuation_index: int) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        rightMargin=PAGE_MARGIN, leftMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
    )
    story: list = []
    block_w, block_h = _drawing_box()

    if continuation_index == 0:
        _front_matter(story, project, result, company_name, styles)
        if sheet_groups or missing_groups:
            story.append(PageBreak())
            story.append(Paragraph("Rozkroje płyt", styles["heading"]))
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph(
            f"{_safe_str(company_name or 'SIEKACZ 9000')} — Rozkroje płyt (kontynuacja, część {continuation_index + 1})",
            styles["heading"]))
        story.append(Spacer(1, 3 * mm))

    _append_drawings(story, sheet_groups, styles, False, block_w, block_h)
    if missing_groups:
        story.append(Paragraph("Brakujące arkusze", styles["heading"]))
        story.append(Spacer(1, 2 * mm))
        _append_drawings(story, missing_groups, styles, True, block_w, block_h)

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
                 company_name: str = "", sheets_per_pdf: int = SHEETS_PER_PDF) -> list[Path]:
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
        missing_groups = group_identical_layouts(result.missing_sheet_layouts)

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
        _build_document(target, project, result, company_name, s_groups, m_groups, index)
        written.append(target)
    return written
