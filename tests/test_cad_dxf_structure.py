from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf
from PySide6.QtWidgets import QApplication, QFileDialog

from app.technical_editor import TechnicalEditorDialog
from cad.dxf_io import export_dxf, import_dxf
from cad.model import CadDocument, CadLayer, EntityStyle, LineEntity, Point2D


def _source_dxf(path: Path) -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    cut = document.layers.add("CUT", color=3, linetype="DASHED")
    cut.rgb = (12, 34, 56)
    cut.lock()
    document.layers.add("HIDDEN", color=5).off()
    document.modelspace().add_line(
        (0, 0), (10, 0),
        dxfattribs={
            "layer": "CUT", "color": 1, "true_color": 0x112233,
            "linetype": "CENTER", "lineweight": 35, "invisible": 1,
        },
    )
    document.modelspace().add_text("pomijany", dxfattribs={"layer": "CUT"})
    document.saveas(path)


def test_dxf_layers_entity_style_report_and_native_document_round_trip() -> None:
    root = Path(tempfile.mkdtemp(prefix="siekacz-dxf-structure-"))
    source = root / "source.dxf"
    target = root / "target.dxf"
    _source_dxf(source)

    imported = import_dxf(source)
    assert imported.report.imported == 1 and imported.report.source_entities == 2
    assert imported.report.unsupported == {"TEXT": 1}
    assert imported.report.open_contours == 1 and imported.report.units == "mm"
    assert imported.layers["CUT"] == CadLayer("CUT", 3, 0x0C2238, "DASHED", True, True)
    assert not imported.layers["HIDDEN"].visible
    line = imported.entities[0]
    assert isinstance(line, LineEntity) and not line.visible and line.layer == "CUT"
    assert line.style == EntityStyle(1, 0x112233, "CENTER", 35, line.style.source_handle)

    native = CadDocument.create()
    native.layers = imported.layers
    native.active_sketch._add_entity(line)
    restored = CadDocument.from_dict(native.to_dict())
    assert restored.layers == native.layers
    assert restored.active_sketch.entities[line.id] == line

    export_dxf(target, restored.active_sketch.ordered_entities(), restored.layers.values())
    second = import_dxf(target)
    exported = second.entities[0]
    assert second.layers["CUT"] == imported.layers["CUT"]
    assert exported.style.true_color == 0x112233
    assert exported.style.linetype == "CENTER" and exported.style.lineweight == 35
    assert not exported.visible
    print("[OK] warstwy, styl encji i raport DXF przechodzą przez .siekcad oraz eksport bez utraty")


def test_editor_uses_structured_import_and_effective_layer_visibility() -> None:
    root = Path(tempfile.mkdtemp(prefix="siekacz-dxf-ui-"))
    source = root / "source.dxf"
    _source_dxf(source)
    app = QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "DXF")):
        dialog._import_dxf()
    assert dialog.last_dxf_import_report.unsupported == {"TEXT": 1}
    assert dialog.document.layers["CUT"].true_color == 0x0C2238
    imported_line = next(iter(dialog.document.active_sketch.ordered_entities()))
    assert imported_line.style.true_color == 0x112233
    item = dialog.canvas._entity_items[imported_line.id]
    assert not item.isVisible()

    visible_line = LineEntity(Point2D(0, 1), Point2D(10, 1), layer="HIDDEN")
    dialog.document.active_sketch._add_entity(visible_line)
    dialog.canvas.sync_from_model()
    assert not dialog.canvas._entity_items[visible_line.id].isVisible()
    dialog.close()
    app.processEvents()
    print("[OK] edytor korzysta z raportowanego importu i respektuje widoczność encji oraz warstwy")


if __name__ == "__main__":
    test_dxf_layers_entity_style_report_and_native_document_round_trip()
    test_editor_uses_structured_import_and_effective_layer_visibility()
    print("CAD DXF STRUCTURE TESTS OK")
