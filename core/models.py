from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any


class JobType(str, Enum):
    SHEET = "sheet"
    LINEAR = "linear"


class GrainDirection(str, Enum):
    NONE = "none"
    LENGTH = "length"
    WIDTH = "width"


@dataclass
class ProjectMeta:
    client_name: str = ""
    order_number: str = ""
    material: str = ""
    notes: str = ""
    creation_date: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class SheetStock:
    material: str
    thickness: float
    width: float
    height: float
    quantity: int
    price: float = 0.0
    grain_direction: str = GrainDirection.NONE.value
    allow_rotation: bool = True
    min_offcut_width: float = 100.0
    min_offcut_height: float = 100.0
    source: str = "stock"
    nominal_width: float = 0.0
    nominal_height: float = 0.0
    sheet_allowance: float = 0.0
    stack_size: int = 1


def materials_are_compatible(stock_material: str, part_material: str) -> bool:
    """Match named materials exactly, while treating an unassigned one as generic.

    ``standard`` is the legacy internal value created for an empty UI field.
    It has the same meaning as an unassigned material and must not block quick
    thickness-only calculations.
    """
    stock_name = str(stock_material or "").strip().casefold()
    part_name = str(part_material or "").strip().casefold()
    generic_names = {"", "standard"}
    return stock_name in generic_names or part_name in generic_names or stock_name == part_name


@dataclass
class LinearStock:
    material: str
    profile: str
    length: float
    quantity: int
    price: float = 0.0
    kerf: float = 3.0
    min_offcut_length: float = 80.0
    source: str = "stock"


@dataclass
class SheetPart:
    name: str
    width: float
    height: float
    quantity: int
    material: str
    thickness: float
    allow_rotation: bool = True
    grain_direction: str = GrainDirection.NONE.value
    label: str = ""
    priority: int = 0
    notes: str = ""
    is_waste_fill: bool = False  # True for bonus-cut copies placed in missing-sheet waste


@dataclass
class LinearPart:
    name: str
    length: float
    quantity: int
    material: str
    label: str = ""
    priority: int = 0
    notes: str = ""


@dataclass
class PlacedSheetPart:
    part: SheetPart
    x: float
    y: float
    width: float
    height: float
    rotated: bool = False


@dataclass
class CutOperation:
    sheet_index: int
    step: int
    orientation: str
    x: float
    y: float
    length: float
    position: float
    description: str
    kind: str = "cut"


@dataclass
class SheetLayout:
    stock: SheetStock
    sheet_index: int
    parts: list[PlacedSheetPart] = field(default_factory=list)
    offcuts: list[tuple[float, float, float, float]] = field(default_factory=list)
    vertical_segments: list[dict[str, Any]] = field(default_factory=list)
    largest_reusable_offcut_area: float = 0.0
    reusable_offcut_area: float = 0.0
    offcut_quality: float = 0.0
    fragmentation_score: float = 0.0
    total_cut_length: float = 0.0
    cut_count: int = 0
    manufacturing_score: float = 0.0
    cut_tree: dict[str, Any] | None = None
    cut_operations: list[CutOperation] = field(default_factory=list)
    waste_rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    is_guillotine_feasible: bool = False
    cutting_explanation: str = ""
    technology_warning: str = ""
    strip_count: int = 0
    # T2-6: estimated machine cut time in seconds (cut length / speed
    # plus per-cut repositioning overhead).  Tunable constants live in
    # algorithms.layout_scoring (CUT_SPEED_MM_PER_S, REPOSITION_SECONDS).
    estimated_cut_time_s: float = 0.0

    @property
    def area(self) -> float:
        return self.stock.width * self.stock.height

    @property
    def used_area(self) -> float:
        return sum(p.width * p.height for p in self.parts)

    @property
    def used_width(self) -> float:
        return max((p.x + p.width for p in self.parts), default=0.0)

    @property
    def used_height(self) -> float:
        return max((p.y + p.height for p in self.parts), default=0.0)

    @property
    def consumed_area(self) -> float:
        return self.used_width * self.stock.height if self.parts else 0.0

    @property
    def strip_waste(self) -> float:
        return max(0.0, self.consumed_area - self.used_area)

    @property
    def sheet_utilization(self) -> float:
        return (self.used_area / self.area * 100.0) if self.area else 0.0

    @property
    def utilization(self) -> float:
        return (self.used_area / self.consumed_area * 100.0) if self.consumed_area else 0.0


@dataclass
class LinearPlacement:
    part: LinearPart
    start: float
    length: float


@dataclass
class LinearLayout:
    stock: LinearStock
    bar_index: int
    placements: list[LinearPlacement] = field(default_factory=list)
    leftover: float = 0.0

    @property
    def used_length(self) -> float:
        return sum(p.length for p in self.placements)

    @property
    def utilization(self) -> float:
        return (self.used_length / self.stock.length * 100.0) if self.stock.length else 0.0


@dataclass
class OptimizationSettings:
    job_type: str = JobType.SHEET.value
    algorithm: str = "Vertical Segmented Guillotine"
    optimization_mode: str = "comfort"
    mode: str = "minimize_waste"
    kerf: float = 3.0
    kerf_tolerance: float = 0.2
    margin: float = 0.0
    sheet_allowance: float = 0.0
    min_reusable_offcut_size: float = 200.0
    display_orientation: str = "horizontal"
    cutting_mode: str = "hybrid"
    prefer_long_rip_cuts: bool = True
    allow_rotation: bool = True
    show_cut_order: bool = False
    allow_mixed_materials: bool = False
    multi_core: bool = True
    saw_feed_m_per_min: float = 12.0
    animation_mode: str = "economy"


@dataclass
class OptimizationResult:
    job_type: str
    algorithm: str
    sheet_layouts: list[SheetLayout] = field(default_factory=list)
    missing_sheet_layouts: list[SheetLayout] = field(default_factory=list)
    linear_layouts: list[LinearLayout] = field(default_factory=list)
    unplaced_sheet_parts: list[SheetPart] = field(default_factory=list)
    unplaced_linear_parts: list[LinearPart] = field(default_factory=list)
    total_cost: float = 0.0
    waste: float = 0.0
    utilization: float = 0.0
    reusable_offcuts: list[dict[str, Any]] = field(default_factory=list)
    total_reusable_offcut_area: float = 0.0
    largest_reusable_offcut_area: float = 0.0
    offcut_quality: float = 0.0
    fragmentation_score: float = 0.0
    manufacturing_score: float = 0.0
    # T2-6: aggregated estimated cut time across all sheets, in seconds.
    total_estimated_cut_time_s: float = 0.0
    saw_feed_m_per_min: float = 12.0
    messages: list[str] = field(default_factory=list)


@dataclass
class Project:
    meta: ProjectMeta = field(default_factory=ProjectMeta)
    sheet_stock: list[SheetStock] = field(default_factory=list)
    linear_stock: list[LinearStock] = field(default_factory=list)
    sheet_parts: list[SheetPart] = field(default_factory=list)
    linear_parts: list[LinearPart] = field(default_factory=list)
    settings: OptimizationSettings = field(default_factory=OptimizationSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        settings_fields = {item.name for item in fields(OptimizationSettings)}
        settings_data = {key: value for key, value in data.get("settings", {}).items() if key in settings_fields}
        return cls(
            meta=ProjectMeta(**data.get("meta", {})),
            sheet_stock=[SheetStock(**x) for x in data.get("sheet_stock", [])],
            linear_stock=[LinearStock(**x) for x in data.get("linear_stock", [])],
            sheet_parts=[SheetPart(**x) for x in data.get("sheet_parts", [])],
            linear_parts=[LinearPart(**x) for x in data.get("linear_parts", [])],
            settings=OptimizationSettings(**settings_data),
        )
