from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, TypedDict

from ezdxf.document import Drawing
from ezdxf.entities.dxfentity import DXFEntity
from ezdxf.entities.spline import Spline
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.polyline import Polyline

from cad.model import (
    ArcEntity, BSplineEntity, CadLayer, CircleEntity, EllipseEntity, EllipticalArcEntity,
    EntityStyle, LineEntity, Point2D, PointEntity, PolylineEntity, RectangleEntity,
    RegularPolygonEntity, SketchEntity, SlotEntity,
)


@dataclass(frozen=True, slots=True)
class DxfImportReport:
    source: str
    imported: int
    source_entities: int
    layers: int
    unsupported: dict[str, int] = field(default_factory=dict)
    open_contours: int = 0
    micro_geometry: int = 0
    duplicate_candidates: int = 0
    units: str = "mm"
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        omitted = sum(self.unsupported.values())
        suffix = f", pominięto {omitted}" if omitted else ""
        return f"Zaimportowano {self.imported}/{self.source_entities} encji z {self.layers} warstw{suffix}."


@dataclass(frozen=True, slots=True)
class DxfImportResult:
    entities: tuple[SketchEntity, ...]
    layers: dict[str, CadLayer]
    report: DxfImportReport


def _style(source: DXFEntity) -> EntityStyle:
    dxf = source.dxf
    return EntityStyle(
        color=int(dxf.get("color", 256)),
        true_color=int(dxf.true_color) if dxf.hasattr("true_color") else None,
        linetype=str(dxf.get("linetype", "BYLAYER")),
        lineweight=int(dxf.get("lineweight", -1)),
        source_handle=str(dxf.get("handle", "")),
    )


class _EntityCommon(TypedDict):
    layer: str
    visible: bool
    style: EntityStyle


def _common(source: DXFEntity) -> _EntityCommon:
    return {
        "layer": str(source.dxf.get("layer", "0"))[:128],
        "visible": not bool(source.dxf.get("invisible", 0)),
        "style": _style(source),
    }


def _read_layers(dxf: Drawing) -> dict[str, CadLayer]:
    result: dict[str, CadLayer] = {}
    for source in dxf.layers:
        true_color = source.rgb
        rgb = None if true_color is None else (true_color[0] << 16) | (true_color[1] << 8) | true_color[2]
        layer = CadLayer(
            name=str(source.dxf.name), color=abs(int(source.color)), true_color=rgb,
            linetype=str(source.dxf.linetype),
            visible=not (source.is_off() or source.is_frozen()), locked=source.is_locked(),
        )
        result[layer.name] = layer
    result.setdefault("0", CadLayer("0"))
    return result


def _entity_from_dxf(source: DXFEntity) -> SketchEntity | None:
    kind = source.dxftype()
    common = _common(source)
    if kind == "LINE":
        start, end = source.dxf.start, source.dxf.end
        return LineEntity(Point2D(start.x, start.y), Point2D(end.x, end.y), **common)
    if kind == "POINT":
        point = source.dxf.location
        return PointEntity(Point2D(point.x, point.y), **common)
    if kind == "CIRCLE":
        center = source.dxf.center
        return CircleEntity(Point2D(center.x, center.y), float(source.dxf.radius), **common)
    if kind == "ARC":
        center = source.dxf.center
        start = float(source.dxf.start_angle)
        sweep = (float(source.dxf.end_angle) - start) % 360.0
        return ArcEntity(Point2D(center.x, center.y), float(source.dxf.radius), start, sweep or 360.0, **common)
    if kind == "ELLIPSE":
        center, axis = source.dxf.center, source.dxf.major_axis
        major = math.hypot(float(axis.x), float(axis.y))
        minor = major * abs(float(source.dxf.ratio))
        rotation = math.degrees(math.atan2(float(axis.y), float(axis.x)))
        start = math.degrees(float(source.dxf.start_param))
        raw_sweep = math.degrees(float(source.dxf.end_param) - float(source.dxf.start_param))
        if abs(abs(raw_sweep) - 360.0) <= 1e-7 or abs(raw_sweep) <= 1e-7:
            return EllipseEntity(Point2D(center.x, center.y), major, minor, rotation, **common)
        return EllipticalArcEntity(Point2D(center.x, center.y), major, minor, rotation, start, raw_sweep % 360.0, **common)
    if isinstance(source, Spline):
        control_points = list(source.control_points)
        knots = tuple(float(value) for value in source.knots)
        weights = tuple(float(value) for value in source.weights)
        degree = int(source.dxf.degree)
        if not control_points:
            fit_points = list(source.fit_points)
            if len(fit_points) < 2:
                return None
            from ezdxf.math import global_bspline_interpolation
            curve = global_bspline_interpolation(fit_points, degree=min(degree, len(fit_points) - 1))
            control_points = list(curve.control_points)
            knots, weights, degree = tuple(curve.knots()), tuple(curve.weights()), int(curve.degree)
        flags = int(source.dxf.flags)
        return BSplineEntity(
            tuple(Point2D(float(point[0]), float(point[1])) for point in control_points),
            degree, knots, weights, bool(flags & 1), bool(flags & 2), **common,
        )
    if isinstance(source, LWPolyline):
        raw = list(source.get_points("xyb"))
        points = tuple(Point2D(float(item[0]), float(item[1])) for item in raw)
        closed = bool(source.closed)
        count = len(points) if closed else max(0, len(points) - 1)
        return PolylineEntity(points, closed, tuple(float(raw[index][2]) for index in range(count)), **common)
    if isinstance(source, Polyline) and source.is_2d_polyline:
        vertices = list(source.vertices)
        points = tuple(Point2D(float(v.dxf.location.x), float(v.dxf.location.y)) for v in vertices)
        closed = bool(source.is_closed)
        count = len(points) if closed else max(0, len(points) - 1)
        return PolylineEntity(points, closed, tuple(float(vertices[i].dxf.get("bulge", 0.0)) for i in range(count)), **common)
    return None


def import_dxf(path: str | Path) -> DxfImportResult:
    from ezdxf import filemanagement

    source_path = Path(path)
    dxf = filemanagement.readfile(source_path)
    layers = _read_layers(dxf)
    entities: list[SketchEntity] = []
    unsupported: Counter[str] = Counter()
    source_count = 0
    for source in dxf.modelspace():
        source_count += 1
        entity = _entity_from_dxf(source)
        if entity is None:
            unsupported[source.dxftype()] += 1
        else:
            entities.append(entity)
    signatures: Counter[tuple[object, ...]] = Counter()
    for entity in entities:
        signatures[(entity.kind, tuple(round(value, 7) for value in entity.bounds()))] += 1
    duplicates = sum(count - 1 for count in signatures.values() if count > 1)
    micro = sum(1 for entity in entities if getattr(entity, "length", 1.0) < 1e-4)
    opened = sum(isinstance(entity, (LineEntity, ArcEntity, EllipticalArcEntity)) or
                 isinstance(entity, PolylineEntity) and not entity.closed for entity in entities)
    unit_code = int(dxf.header.get("$INSUNITS", 0))
    unit_names = {0: "brak", 1: "in", 4: "mm", 5: "cm", 6: "m"}
    warnings = () if unit_code == 4 else (f"Jednostki DXF: {unit_names.get(unit_code, str(unit_code))}; współrzędne pozostawiono bez skalowania.",)
    report = DxfImportReport(
        str(source_path), len(entities), source_count, len(layers), dict(unsupported),
        opened, micro, duplicates, unit_names.get(unit_code, str(unit_code)), warnings,
    )
    return DxfImportResult(tuple(entities), layers, report)


def _attrs(entity: SketchEntity) -> dict[str, object]:
    attrs: dict[str, object] = {"layer": entity.layer, "color": entity.style.color,
                                "linetype": entity.style.linetype, "lineweight": entity.style.lineweight}
    if entity.style.true_color is not None:
        attrs["true_color"] = entity.style.true_color
    if not entity.visible:
        attrs["invisible"] = 1
    return attrs


def export_dxf(path: str | Path, entities: Iterable[SketchEntity], layers: Iterable[CadLayer]) -> Path:
    from ezdxf import filemanagement

    target = Path(path).with_suffix(".dxf")
    dxf = filemanagement.new("R2010")
    dxf.header["$INSUNITS"] = 4
    for layer in layers:
        target_layer = dxf.layers.get(layer.name) if layer.name in dxf.layers else dxf.layers.add(layer.name)
        target_layer.color = -layer.color if not layer.visible else layer.color
        target_layer.dxf.linetype = layer.linetype
        if layer.true_color is not None:
            target_layer.rgb = ((layer.true_color >> 16) & 255, (layer.true_color >> 8) & 255, layer.true_color & 255)
        if layer.locked:
            target_layer.lock()
    modelspace = dxf.modelspace()
    for entity in entities:
        attrs = _attrs(entity)
        if isinstance(entity, PointEntity): modelspace.add_point((entity.point.x, entity.point.y), dxfattribs=attrs)
        elif isinstance(entity, LineEntity): modelspace.add_line((entity.start.x, entity.start.y), (entity.end.x, entity.end.y), dxfattribs=attrs)
        elif isinstance(entity, RectangleEntity): modelspace.add_lwpolyline([(p.x, p.y) for p in entity.corners], close=True, dxfattribs=attrs)
        elif isinstance(entity, CircleEntity): modelspace.add_circle((entity.center.x, entity.center.y), entity.radius, dxfattribs=attrs)
        elif isinstance(entity, ArcEntity):
            start, end = ((entity.start_angle_deg, entity.start_angle_deg + entity.sweep_angle_deg) if entity.sweep_angle_deg > 0 else
                          (entity.start_angle_deg + entity.sweep_angle_deg, entity.start_angle_deg))
            modelspace.add_arc((entity.center.x, entity.center.y), entity.radius, start % 360, end % 360, dxfattribs=attrs)
        elif isinstance(entity, (EllipseEntity, EllipticalArcEntity)):
            rotation = math.radians(entity.rotation_deg)
            axis = (entity.major_radius * math.cos(rotation), entity.major_radius * math.sin(rotation))
            if isinstance(entity, EllipseEntity): start, end = 0.0, math.tau
            elif entity.sweep_parameter_deg > 0: start, end = map(math.radians, (entity.start_parameter_deg, entity.start_parameter_deg + entity.sweep_parameter_deg))
            else: start, end = map(math.radians, (entity.start_parameter_deg + entity.sweep_parameter_deg, entity.start_parameter_deg))
            modelspace.add_ellipse((entity.center.x, entity.center.y), axis, entity.minor_radius / entity.major_radius, start, end, dxfattribs=attrs)
        elif isinstance(entity, BSplineEntity):
            spline = modelspace.add_spline(degree=entity.degree, dxfattribs=attrs)
            spline.control_points = [(p.x, p.y, 0.0) for p in entity.control_points]
            spline.knots = entity.knots
            if entity.rational: spline.weights = entity.weights
            spline.dxf.flags = (1 if entity.closed else 0) | (2 if entity.periodic else 0) | (4 if entity.rational else 0) | 8
        elif isinstance(entity, PolylineEntity):
            modelspace.add_lwpolyline([(p.x, p.y, entity.bulges[i] if i < len(entity.bulges) else 0.0) for i, p in enumerate(entity.points)], format="xyb", close=entity.closed, dxfattribs=attrs)
        elif isinstance(entity, RegularPolygonEntity): modelspace.add_lwpolyline([(p.x, p.y) for p in entity.points], close=True, dxfattribs=attrs)
        elif isinstance(entity, SlotEntity):
            for line in entity.boundary_lines: modelspace.add_line((line.start.x, line.start.y), (line.end.x, line.end.y), dxfattribs=attrs)
            for arc in entity.boundary_arcs:
                start, end = ((arc.start_angle_deg, arc.start_angle_deg + arc.sweep_angle_deg) if arc.sweep_angle_deg > 0 else (arc.start_angle_deg + arc.sweep_angle_deg, arc.start_angle_deg))
                modelspace.add_arc((arc.center.x, arc.center.y), arc.radius, start % 360, end % 360, dxfattribs=attrs)
    dxf.saveas(target)
    return target
