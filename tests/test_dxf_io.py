"""Regression checks for the DXF import/export bridge."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetStock
from import_export.dxf_io import export_layout_dxf, import_dxf_parts


def test_complete_dxf_is_imported_as_one_drawing_blank() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "details.dxf"
        document = ezdxf.new("R2010")
        document.header["$INSUNITS"] = 4
        modelspace = document.modelspace()
        for offset in (0, 150):
            modelspace.add_lwpolyline(
                [(offset, 0), (offset + 100, 0), (offset + 100, 50), (offset, 50)], close=True
            )
        document.saveas(source)

        parts = import_dxf_parts(source, material="PA6", thickness=18)
        assert len(parts) == 1
        assert (parts[0].width, parts[0].height, parts[0].quantity) == (250.0, 50.0, 1)
        assert (parts[0].material, parts[0].thickness) == ("PA6", 18.0)
        assert parts[0].notes.startswith("DXF:")
        print("[OK] DXF import uses one blank covering the complete drawing")


def test_dxf_export_contains_the_board_and_parts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        stock = SheetStock("PA6", 18, 1000, 500, 1)
        from core.models import SheetPart
        source_part = SheetPart("A", 100, 50, 1, "PA6", 18)
        layout = SheetLayout(stock=stock, sheet_index=1, parts=[PlacedSheetPart(source_part, 0, 0, 100, 50)])
        path = export_layout_dxf(Path(directory) / "result", OptimizationResult("sheet", "test", [layout]))
        document = ezdxf.readfile(path)
        assert path.suffix == ".dxf"
        assert len(document.modelspace()) >= 3
        print("[OK] DXF export writes a board, detail and label")


if __name__ == "__main__":
    test_complete_dxf_is_imported_as_one_drawing_blank()
    test_dxf_export_contains_the_board_and_parts()
    print("DXF IO TESTS OK")
