from __future__ import annotations

"""Read-only geometry loaders used by the DXF/STEP/STL inspection window.

All coordinates are normalised to millimetres.  The viewer deliberately keeps
topological edges instead of reducing imported files to a bounding rectangle,
so a selected side can be measured in real 3D space.
"""

import re
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


class CadInspectionError(ValueError):
    """Actionable error shown directly to the operator."""


@dataclass(frozen=True)
class CadEdge:
    start: int
    end: int
    source_id: str = ""
    kind: str = "krawędź"
    approximate: bool = False
    radius: float | None = None
    exact_length: float | None = None
    center: tuple[float, float, float] | None = None


@dataclass
class CadInspectionModel:
    source_path: Path
    source_format: str
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    edges: list[CadEdge] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    source_units: str = "mm"
    warnings: list[str] = field(default_factory=list)

    @property
    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if not self.vertices:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        return (
            (min(p[0] for p in self.vertices), min(p[1] for p in self.vertices), min(p[2] for p in self.vertices)),
            (max(p[0] for p in self.vertices), max(p[1] for p in self.vertices), max(p[2] for p in self.vertices)),
        )

    @property
    def dimensions(self) -> tuple[float, float, float]:
        minimum, maximum = self.bounds
        return (maximum[0] - minimum[0], maximum[1] - minimum[1], maximum[2] - minimum[2])

    def edge_length(self, edge_index: int) -> float:
        edge = self.edges[edge_index]
        if edge.exact_length is not None:
            return edge.exact_length
        first, second = self.vertices[edge.start], self.vertices[edge.end]
        return sum((second[axis] - first[axis]) ** 2 for axis in range(3)) ** 0.5


class _GeometryBuilder:
    def __init__(self, tolerance: float = 1e-7) -> None:
        self.vertices: list[tuple[float, float, float]] = []
        self.edges: list[CadEdge] = []
        self.faces: list[tuple[int, int, int]] = []
        self._tolerance = tolerance
        self._vertex_map: dict[tuple[int, int, int], int] = {}
        self._edge_keys: set[tuple[int, int, str]] = set()

    def vertex(self, point: Iterable[float]) -> int:
        values = list(point)
        xyz = (
            float(values[0]) if len(values) > 0 else 0.0,
            float(values[1]) if len(values) > 1 else 0.0,
            float(values[2]) if len(values) > 2 else 0.0,
        )
        key = (round(xyz[0] / self._tolerance), round(xyz[1] / self._tolerance), round(xyz[2] / self._tolerance))
        existing = self._vertex_map.get(key)
        if existing is not None:
            return existing
        index = len(self.vertices)
        self.vertices.append(xyz)
        self._vertex_map[key] = index
        return index

    def edge(
        self,
        first: Iterable[float] | int,
        second: Iterable[float] | int,
        *,
        source_id: str = "",
        kind: str = "krawędź",
        approximate: bool = False,
        radius: float | None = None,
        exact_length: float | None = None,
        center: tuple[float, float, float] | None = None,
    ) -> None:
        start = first if isinstance(first, int) else self.vertex(first)
        end = second if isinstance(second, int) else self.vertex(second)
        if start == end:
            return
        key = (min(start, end), max(start, end), source_id)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(CadEdge(start, end, source_id, kind, approximate, radius, exact_length, center))

    def triangle(self, points: Iterable[Iterable[float]], *, source_id: str = "") -> None:
        indices = tuple(self.vertex(point) for point in points)
        if len(indices) != 3 or len(set(indices)) < 3:
            return
        self.faces.append(indices)
        # Mesh facets share sides.  Keep each geometric side once and do not
        # group all three sides under the facet id: clicking one side must
        # measure that side, not the whole triangle perimeter.
        self.edge(indices[0], indices[1], kind="krawędź siatki")
        self.edge(indices[1], indices[2], kind="krawędź siatki")
        self.edge(indices[2], indices[0], kind="krawędź siatki")


_DXF_UNITS: dict[int, tuple[str, float]] = {
    0: ("brak jednostki", 1.0),
    1: ("cale", 25.4),
    2: ("stopy", 304.8),
    4: ("mm", 1.0),
    5: ("cm", 10.0),
    6: ("m", 1000.0),
}


def _xyz(point, scale: float) -> tuple[float, float, float]:
    return float(point.x) * scale, float(point.y) * scale, float(getattr(point, "z", 0.0)) * scale


def _dxf_entities(entities):
    for entity in entities:
        if entity.dxftype() == "INSERT":
            try:
                yield from _dxf_entities(entity.virtual_entities())
            except Exception:
                continue
        else:
            yield entity


def load_dxf(path: str | Path) -> CadInspectionModel:
    try:
        from ezdxf import filemanagement
        from ezdxf.path import make_path
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise CadInspectionError("Brakuje biblioteki ezdxf.") from exc

    source = Path(path)
    try:
        document = filemanagement.readfile(source)
    except Exception as exc:
        raise CadInspectionError(f"Nie udało się odczytać DXF: {exc}") from exc
    unit_code = int(document.header.get("$INSUNITS", 0) or 0)
    unit_name, scale = _DXF_UNITS.get(unit_code, (f"kod DXF {unit_code}", 1.0))
    builder = _GeometryBuilder()
    warnings: list[str] = []
    skipped: dict[str, int] = {}
    if unit_code == 0:
        warnings.append("DXF nie deklaruje jednostek — przyjęto milimetry.")

    def segments(entities):
        for index, entity in enumerate(_dxf_entities(entities)):
            handle = str(getattr(entity.dxf, "handle", "") or f"entity-{index}")
            if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
                for part_index, segment in enumerate(entity.virtual_entities()):
                    yield segment, f"{handle}:{part_index}"
            else:
                yield entity, handle

    for entity, source_id in segments(document.modelspace()):
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                builder.edge(_xyz(entity.dxf.start, scale), _xyz(entity.dxf.end, scale), source_id=source_id, kind="linia")
                continue
            if kind == "3DFACE":
                points = [_xyz(getattr(entity.dxf, f"vtx{index}"), scale) for index in range(3)]
                builder.triangle(points, source_id=source_id)
                continue
            path_object = make_path(entity)
            points = list(path_object.flattening(distance=max(0.02 / max(scale, 1e-9), 1e-5), segments=24))
            radius = float(entity.dxf.radius) * scale if kind in {"ARC", "CIRCLE"} else None
            sweep = ((float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360 or 360) if kind == "ARC" else 360
            exact = radius * math.radians(sweep) / (len(points) - 1) if radius is not None and len(points) > 1 else None
            for first, second in zip(points, points[1:]):
                builder.edge(
                    _xyz(first, scale),
                    _xyz(second, scale),
                    source_id=source_id,
                    kind=kind.casefold(),
                    approximate=exact is None and kind not in {"LINE", "LWPOLYLINE", "POLYLINE"},
                    radius=radius, exact_length=exact,
                    center=_xyz(entity.ocs().to_wcs(entity.dxf.center), scale) if radius is not None else None,
                )
        except Exception:
            skipped[kind] = skipped.get(kind, 0) + 1

    if skipped:
        warnings.append("Pominięte adnotacje lub encje: " + ", ".join(f"{kind}: {count}" for kind, count in sorted(skipped.items())))
    if not builder.edges:
        raise CadInspectionError("DXF nie zawiera krawędzi możliwych do pokazania i zmierzenia.")
    return CadInspectionModel(source, "DXF", builder.vertices, builder.edges, builder.faces, unit_name, warnings)


def _load_binary_stl(data: bytes, builder: _GeometryBuilder) -> None:
    count = struct.unpack_from("<I", data, 80)[0]
    if 84 + count * 50 > len(data):
        raise CadInspectionError("Uszkodzony binarny STL: niepełna lista trójkątów.")
    offset = 84
    for index in range(count):
        values = struct.unpack_from("<12fH", data, offset)
        builder.triangle((values[3:6], values[6:9], values[9:12]), source_id=f"facet-{index + 1}")
        offset += 50


def _load_ascii_stl(text: str, builder: _GeometryBuilder) -> None:
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
    matches = re.findall(rf"\bvertex\s+({number})\s+({number})\s+({number})", text, flags=re.IGNORECASE)
    if len(matches) < 3 or len(matches) % 3:
        raise CadInspectionError("ASCII STL nie zawiera kompletnej listy trójkątów.")
    for index in range(0, len(matches), 3):
        builder.triangle((tuple(map(float, point)) for point in matches[index:index + 3]), source_id=f"facet-{index // 3 + 1}")


def load_stl(path: str | Path) -> CadInspectionModel:
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise CadInspectionError(f"Nie udało się odczytać STL: {exc}") from exc
    if len(data) < 15:
        raise CadInspectionError("Plik STL jest pusty lub uszkodzony.")
    builder = _GeometryBuilder(tolerance=1e-6)
    binary = len(data) >= 84 and 84 + struct.unpack_from("<I", data, 80)[0] * 50 == len(data)
    if binary:
        _load_binary_stl(data, builder)
    else:
        try:
            _load_ascii_stl(data.decode("utf-8", errors="replace"), builder)
        except UnicodeError as exc:  # pragma: no cover - decode uses replacement
            raise CadInspectionError("Nie udało się rozpoznać formatu STL.") from exc
    if not builder.faces:
        raise CadInspectionError("STL nie zawiera trójkątów.")
    return CadInspectionModel(
        source,
        "STL",
        builder.vertices,
        builder.edges,
        builder.faces,
        "brak w STL; przyjęto mm",
        ["Format STL nie zapisuje jednostek ani analitycznych krawędzi — pomiary dotyczą siatki i przyjęto milimetry."],
    )


_STEP_RECORD = re.compile(r"#(\d+)\s*=\s*(.*?);", flags=re.DOTALL)
_STEP_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?")


def _step_scale(text: str) -> tuple[str, float, list[str]]:
    compact = re.sub(r"\s+", "", text.upper())
    if re.search(r"SI_UNIT\(\.MILLI\.,\.METRE\.\)", compact):
        return "mm", 1.0, []
    if "CONVERSION_BASED_UNIT('INCH'" in compact or "CONVERSION_BASED_UNIT(\"INCH\"" in compact:
        return "cale", 25.4, []
    if re.search(r"SI_UNIT\((?:\$|\*),\.METRE\.\)", compact):
        return "m", 1000.0, []
    return "nierozpoznane; przyjęto mm", 1.0, ["Nie rozpoznano jednostki STEP — przyjęto milimetry."]


def load_step(path: str | Path) -> CadInspectionModel:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CadInspectionError(f"Nie udało się odczytać STEP: {exc}") from exc
    if "ISO-10303-21" not in text.upper():
        raise CadInspectionError("Plik nie ma nagłówka ISO-10303-21 i nie wygląda jak STEP.")
    unit_name, scale, warnings = _step_scale(text)
    records = {int(identifier): body.strip() for identifier, body in _STEP_RECORD.findall(re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL))}
    points: dict[int, tuple[float, float, float]] = {}
    vertices: dict[int, int] = {}
    builder = _GeometryBuilder(tolerance=1e-7)

    for identifier, body in records.items():
        if not body.upper().startswith("CARTESIAN_POINT"):
            continue
        tail = body[body.rfind("(") + 1: body.rfind(")")]
        values = [float(value.replace("D", "E").replace("d", "e")) * scale for value in _STEP_NUMBER.findall(tail)]
        if len(values) >= 2:
            points[identifier] = (values[0], values[1], values[2] if len(values) > 2 else 0.0)

    for identifier, body in records.items():
        if not body.upper().startswith("VERTEX_POINT"):
            continue
        references = [int(value) for value in re.findall(r"#(\d+)", body)]
        if references and references[-1] in points:
            vertices[identifier] = builder.vertex(points[references[-1]])

    curved = 0
    for identifier, body in records.items():
        if not body.upper().startswith("EDGE_CURVE"):
            continue
        references = [int(value) for value in re.findall(r"#(\d+)", body)]
        if len(references) < 3 or references[0] not in vertices or references[1] not in vertices:
            continue
        curve_body = records.get(references[2], "").lstrip().upper()
        kind = curve_body.split("(", 1)[0] or "EDGE_CURVE"
        approximate = not kind.startswith("LINE")
        curved += int(approximate)
        builder.edge(
            vertices[references[0]],
            vertices[references[1]],
            source_id=f"#{identifier}",
            kind=kind.casefold(),
            approximate=approximate,
        )

    if not builder.edges:
        raise CadInspectionError(
            "STEP nie zawiera czytelnej topologii VERTEX_POINT/EDGE_CURVE. "
            "Wyeksportuj model jako STEP AP203, AP214 lub AP242 z geometrią B-Rep."
        )
    if curved:
        warnings.append(
            f"{curved} krawędzi krzywych STEP pokazano jako cięciwy; ich pomiar oznacza odległość między końcami."
        )
    return CadInspectionModel(source, "STEP", builder.vertices, builder.edges, builder.faces, unit_name, warnings)


def load_cad_inspection(path: str | Path) -> CadInspectionModel:
    source = Path(path)
    if not source.is_file():
        raise CadInspectionError(f"Nie znaleziono pliku: {source}")
    suffix = source.suffix.casefold()
    if suffix == ".dxf":
        return load_dxf(source)
    if suffix in {".step", ".stp"}:
        return load_step(source)
    if suffix == ".stl":
        return load_stl(source)
    raise CadInspectionError("Obsługiwane formaty to DXF, STEP/STP i STL.")
