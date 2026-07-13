from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

from core.models import (
    CutOperation,
    LinearLayout,
    LinearPlacement,
    LinearPart,
    LinearStock,
    OptimizationResult,
    PlacedSheetPart,
    Project,
    SheetLayout,
    SheetPart,
    SheetStock,
)
from database.db import APP_DIR


HISTORY_PATH = APP_DIR / "siekacz_project_history.json"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_project_id() -> str:
    return uuid.uuid4().hex


def _read_file() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_file(items: list[dict[str, Any]]) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp file first, then rename — prevents corrupting the
        # history if the process is killed or the disk fills up mid-write.
        tmp = HISTORY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(HISTORY_PATH)
    except OSError as exc:
        _logger.error("Cannot write project history to %s: %s", HISTORY_PATH, exc)
        raise RuntimeError(
            f"Nie można zapisać historii projektów.\n"
            f"Sprawdź czy dysk nie jest pełny i czy program ma uprawnienia do zapisu.\n"
            f"Ścieżka: {HISTORY_PATH}\nSzczegóły: {exc}"
        ) from exc


def list_projects() -> list[dict[str, Any]]:
    items = _read_file()
    return sorted(items, key=lambda item: item.get("modified_at") or item.get("created_at") or "", reverse=True)


def get_project(project_id: str) -> dict[str, Any] | None:
    for item in _read_file():
        if item.get("id") == project_id:
            return item
    return None


def save_project_record(record: dict[str, Any]) -> dict[str, Any]:
    items = _read_file()
    record = dict(record)
    if not record.get("id"):
        record["id"] = new_project_id()
    existing_index = next((index for index, item in enumerate(items) if item.get("id") == record["id"]), None)
    if existing_index is None:
        record.setdefault("created_at", now_iso())
        items.append(record)
    else:
        record.setdefault("created_at", items[existing_index].get("created_at") or now_iso())
        items[existing_index] = record
    record["modified_at"] = now_iso()
    if existing_index is None:
        items[-1] = record
    else:
        items[existing_index] = record
    _write_file(items)
    return record


def delete_project(project_id: str) -> None:
    _write_file([item for item in _read_file() if item.get("id") != project_id])


def duplicate_project(project_id: str) -> dict[str, Any] | None:
    item = get_project(project_id)
    if not item:
        return None
    clone = json.loads(json.dumps(item, ensure_ascii=False))
    clone["id"] = new_project_id()
    clone["name"] = f"{clone.get('name') or 'Projekt'} kopia"
    clone["created_at"] = now_iso()
    clone["modified_at"] = clone["created_at"]
    return save_project_record(clone)


def result_to_dict(result: OptimizationResult | None) -> dict[str, Any] | None:
    return asdict(result) if result is not None else None


def _sheet_part(data: dict[str, Any]) -> SheetPart:
    return SheetPart(**data)


def _linear_part(data: dict[str, Any]) -> LinearPart:
    return LinearPart(**data)


def _sheet_stock(data: dict[str, Any]) -> SheetStock:
    return SheetStock(**data)


def _linear_stock(data: dict[str, Any]) -> LinearStock:
    return LinearStock(**data)


def _placed_sheet_part(data: dict[str, Any]) -> PlacedSheetPart:
    return PlacedSheetPart(
        part=_sheet_part(data["part"]),
        x=float(data.get("x", 0)),
        y=float(data.get("y", 0)),
        width=float(data.get("width", 0)),
        height=float(data.get("height", 0)),
        rotated=bool(data.get("rotated", False)),
    )


def _cut_operation(data: dict[str, Any]) -> CutOperation:
    return CutOperation(**data)


def _sheet_layout(data: dict[str, Any]) -> SheetLayout:
    layout = SheetLayout(
        stock=_sheet_stock(data["stock"]),
        sheet_index=int(data.get("sheet_index", 1)),
        parts=[_placed_sheet_part(item) for item in data.get("parts", [])],
        offcuts=[tuple(item) for item in data.get("offcuts", [])],
        vertical_segments=list(data.get("vertical_segments", [])),
        largest_reusable_offcut_area=float(data.get("largest_reusable_offcut_area", 0.0)),
        reusable_offcut_area=float(data.get("reusable_offcut_area", 0.0)),
        offcut_quality=float(data.get("offcut_quality", 0.0)),
        fragmentation_score=float(data.get("fragmentation_score", 0.0)),
        total_cut_length=float(data.get("total_cut_length", 0.0)),
        cut_count=int(data.get("cut_count", 0)),
        manufacturing_score=float(data.get("manufacturing_score", 0.0)),
        cut_tree=data.get("cut_tree"),
        cut_operations=[_cut_operation(item) for item in data.get("cut_operations", [])],
        waste_rects=[tuple(item) for item in data.get("waste_rects", [])],
        is_guillotine_feasible=bool(data.get("is_guillotine_feasible", False)),
        cutting_explanation=str(data.get("cutting_explanation", "")),
        technology_warning=str(data.get("technology_warning", "")),
        strip_count=int(data.get("strip_count", 0)),
        # T2-6: cut-time estimate (defaults to 0.0 for projects saved before this field existed).
        estimated_cut_time_s=float(data.get("estimated_cut_time_s", 0.0)),
    )
    return layout


def _linear_layout(data: dict[str, Any]) -> LinearLayout:
    return LinearLayout(
        stock=_linear_stock(data["stock"]),
        bar_index=int(data.get("bar_index", 1)),
        placements=[
            LinearPlacement(part=_linear_part(item["part"]), start=float(item.get("start", 0)), length=float(item.get("length", 0)))
            for item in data.get("placements", [])
        ],
        leftover=float(data.get("leftover", 0.0)),
    )


def result_from_dict(data: dict[str, Any] | None) -> OptimizationResult | None:
    if not data:
        return None
    return OptimizationResult(
        job_type=str(data.get("job_type", "sheet")),
        algorithm=str(data.get("algorithm", "")),
        sheet_layouts=[_sheet_layout(item) for item in data.get("sheet_layouts", [])],
        missing_sheet_layouts=[_sheet_layout(item) for item in data.get("missing_sheet_layouts", [])],
        linear_layouts=[_linear_layout(item) for item in data.get("linear_layouts", [])],
        unplaced_sheet_parts=[_sheet_part(item) for item in data.get("unplaced_sheet_parts", [])],
        unplaced_linear_parts=[_linear_part(item) for item in data.get("unplaced_linear_parts", [])],
        total_cost=float(data.get("total_cost", 0.0)),
        waste=float(data.get("waste", 0.0)),
        utilization=float(data.get("utilization", 0.0)),
        reusable_offcuts=list(data.get("reusable_offcuts", [])),
        total_reusable_offcut_area=float(data.get("total_reusable_offcut_area", 0.0)),
        largest_reusable_offcut_area=float(data.get("largest_reusable_offcut_area", 0.0)),
        offcut_quality=float(data.get("offcut_quality", 0.0)),
        fragmentation_score=float(data.get("fragmentation_score", 0.0)),
        manufacturing_score=float(data.get("manufacturing_score", 0.0)),
        total_estimated_cut_time_s=float(data.get("total_estimated_cut_time_s", 0.0)),
        saw_feed_m_per_min=float(data.get("saw_feed_m_per_min", 12.0)),
        messages=list(data.get("messages", [])),
    )


def record_summary(project: Project, result: OptimizationResult | None) -> dict[str, Any]:
    stock = project.sheet_stock[0] if project.sheet_stock else None
    sheet_formats = [
        f"{(item.source if item.source not in {'stock', 'missing'} else 'Płyta')}: "
        f"{item.nominal_width or item.width:.0f} x {item.nominal_height or item.height:.0f} mm ({int(item.quantity)} szt.)"
        for item in project.sheet_stock
    ]
    used_sheets = len(result.sheet_layouts) if result else 0
    cut_count = sum(getattr(layout, "cut_count", 0) for layout in result.sheet_layouts) if result else 0
    return {
        "material": stock.material if stock else "",
        "thickness": stock.thickness if stock else 0,
        "sheet_format": "; ".join(sheet_formats),
        "sheet_formats": sheet_formats,
        "kerf": project.settings.kerf,
        "mode": project.settings.optimization_mode,
        "used_sheets": used_sheets,
        "utilization": result.utilization if result else 0.0,
        "waste": result.waste if result else 0.0,
        "cut_count": cut_count,
    }
