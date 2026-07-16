from __future__ import annotations

"""DXF interchange for rectangular cutting jobs.

The panel optimiser cannot nest arbitrary CAD contours. Import therefore uses
one rectangular blank covering the complete drawing, while keeping the source
path in the part notes for later editing or export.
"""
from pathlib import Path

from core.models import OptimizationResult, SheetPart, SheetLayout


class DxfError(ValueError):
    """An actionable DXF import/export error for the UI."""


def _require_ezdxf():
    try:
        import ezdxf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - covered by packaging checks
        raise DxfError("Brakuje biblioteki DXF. Uruchom aktualizacje programu.") from exc
    return ezdxf


def _bounds_from_points(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 2:
        return None
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _entity_bounds(entity) -> tuple[float, float, float, float] | None:
    entity_type = entity.dxftype()
    if entity_type == "LWPOLYLINE":
        if not bool(getattr(entity, "closed", False)):
            return None
        return _bounds_from_points([(point[0], point[1]) for point in entity.get_points("xy")])
    if entity_type == "POLYLINE":
        if not bool(getattr(entity, "is_closed", False)):
            return None
        return _bounds_from_points([(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices])
    if entity_type == "CIRCLE":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        return center.x - radius, center.y - radius, center.x + radius, center.y + radius
    if entity_type in {"ELLIPSE", "SPLINE", "HATCH", "INSERT"}:
        try:
            from ezdxf import bbox

            extents = bbox.extents([entity], fast=True)
            if not extents.has_data:
                return None
            return extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y
        except Exception:
            return None
    return None


def import_dxf_parts(path: str | Path, material: str = "", thickness: float = 0.0) -> list[SheetPart]:
    """Import the complete DXF drawing as one rectangular cutting blank."""
    ezdxf = _require_ezdxf()
    source = Path(path)
    if not source.is_file():
        raise DxfError(f"Nie znaleziono pliku DXF: {source}")
    try:
        document = ezdxf.readfile(source)
    except Exception as exc:
        raise DxfError(f"Nie udalo sie odczytac DXF: {exc}") from exc

    geometry_types = {
        "LINE",
        "ARC",
        "CIRCLE",
        "ELLIPSE",
        "SPLINE",
        "LWPOLYLINE",
        "POLYLINE",
        "HATCH",
        "INSERT",
        "SOLID",
        "TRACE",
        "3DFACE",
    }
    entities = [entity for entity in document.modelspace() if entity.dxftype() in geometry_types]
    if not entities:
        raise DxfError(
            "DXF nie zawiera geometrii, z której można wyznaczyć obszar rysunku."
        )

    try:
        from ezdxf import bbox

        extents = bbox.extents(entities, fast=False)
    except Exception as exc:
        raise DxfError(f"Nie udało się wyznaczyć obszaru rysunku DXF: {exc}") from exc
    if not extents.has_data:
        raise DxfError("DXF nie zawiera geometrii o mierzalnym obszarze.")

    width = round(abs(float(extents.extmax.x) - float(extents.extmin.x)), 3)
    height = round(abs(float(extents.extmax.y) - float(extents.extmin.y)), 3)
    if width <= 0.01 or height <= 0.01:
        raise DxfError("Obszar rysunku DXF ma zerową szerokość lub wysokość.")

    label = source.stem
    return [
        SheetPart(
            name=label,
            width=width,
            height=height,
            quantity=1,
            material=material,
            thickness=float(thickness or 0.0),
            label=label,
            notes=(
                f"DXF:{source.resolve()}|origin="
                f"{float(extents.extmin.x):g},{float(extents.extmin.y):g}"
            ),
        )
    ]


def _add_rectangle(modelspace, x: float, y: float, width: float, height: float, layer: str) -> None:
    modelspace.add_lwpolyline(
        [(x, y), (x + width, y), (x + width, y + height), (x, y + height)],
        close=True,
        dxfattribs={"layer": layer},
    )


def _layout_title(layout: SheetLayout) -> str:
    material = str(getattr(layout.stock, "material", "") or "standard")
    thickness = float(getattr(layout.stock, "thickness", 0.0) or 0.0)
    return f"{material} | gr. {thickness:g} mm | plyta {layout.sheet_index}"


def export_layout_dxf(path: str | Path, result: OptimizationResult) -> Path:
    """Export board contours and placed rectangular blanks to one DXF drawing."""
    ezdxf = _require_ezdxf()
    target = Path(path)
    if target.suffix.lower() != ".dxf":
        target = target.with_suffix(".dxf")
    target.parent.mkdir(parents=True, exist_ok=True)

    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4  # millimetres
    document.layers.new("PLYTY", dxfattribs={"color": 5})
    document.layers.new("FORMATKI", dxfattribs={"color": 3})
    document.layers.new("OPISY", dxfattribs={"color": 7})
    document.layers.new("BRAKUJACE", dxfattribs={"color": 1})
    modelspace = document.modelspace()

    y_offset = 0.0
    layouts = [*result.sheet_layouts, *result.missing_sheet_layouts]
    for layout in layouts:
        stock = layout.stock
        board_layer = "BRAKUJACE" if str(getattr(stock, "source", "stock")) == "missing" else "PLYTY"
        _add_rectangle(modelspace, 0.0, y_offset, stock.width, stock.height, board_layer)
        modelspace.add_text(
            _layout_title(layout),
            dxfattribs={"height": max(8.0, min(stock.width, stock.height) * 0.018), "layer": "OPISY"},
        ).set_placement((0.0, y_offset + stock.height + max(12.0, stock.height * 0.025)))
        for placement in layout.parts:
            _add_rectangle(modelspace, placement.x, y_offset + placement.y, placement.width, placement.height, "FORMATKI")
            label = str(getattr(placement.part, "name", "") or "DETAL")
            text_height = min(placement.width, placement.height) * 0.16
            if text_height >= 4.0:
                modelspace.add_text(label, dxfattribs={"height": text_height, "layer": "OPISY"}).set_placement(
                    (placement.x + placement.width * 0.08, y_offset + placement.y + placement.height * 0.45)
                )
        y_offset += stock.height + max(120.0, stock.height * 0.12)

    try:
        document.saveas(target)
    except Exception as exc:
        raise DxfError(f"Nie udalo sie zapisac DXF: {exc}") from exc
    return target
