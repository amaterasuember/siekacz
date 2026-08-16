"""Parametric CAD domain for SIEKACZ.

The package deliberately has no dependency on Qt.  UI and renderers consume the
domain model, never the other way around.
"""

from cad.model import (
    ArcEntity,
    BSplineEntity,
    CadLayer,
    CadDocument,
    CadValidationError,
    CircleEntity,
    EllipseEntity,
    EllipticalArcEntity,
    EntityStyle,
    LineEntity,
    Point2D,
    PointEntity,
    PolylineEntity,
    RegularPolygonEntity,
    RectangleEntity,
    Sketch,
    SlotEntity,
    arc_from_bulge,
)

__all__ = [
    "ArcEntity",
    "BSplineEntity",
    "CadLayer",
    "CadDocument",
    "CadValidationError",
    "CircleEntity",
    "EllipseEntity",
    "EllipticalArcEntity",
    "EntityStyle",
    "LineEntity",
    "Point2D",
    "PointEntity",
    "PolylineEntity",
    "RegularPolygonEntity",
    "RectangleEntity",
    "Sketch",
    "SlotEntity",
    "arc_from_bulge",
]
