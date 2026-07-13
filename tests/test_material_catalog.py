from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from app.material_catalog import catalog_family_label, read_material_catalog


def test_supplier_catalog_filters_non_boards() -> None:
    path = Path(__file__).resolve().parents[1] / "sample_data" / "material_catalog.xlsx"
    items = read_material_catalog(path)
    assert len(items) >= 100
    assert any(item.material == "PA6" and item.thickness == 1 for item in items)
    pa6 = next(item for item in items if item.material == "PA6" and item.thickness == 1)
    assert catalog_family_label(pa6) == "PA6 PŁYTA NATURALNA"
    assert all(item.gross_price_m2 > 0 for item in items)
    assert not any(
        marker in item.product_name.upper()
        for item in items
        for marker in ("RURA", "WAŁEK", "WALEK", "PRĘT", "PRET")
    )


def test_catalog_finds_columns_and_thickness_without_fixed_sheet_name() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Nowy dostawca"
    sheet.append(["Produkt", "Jednostka", "Cena netto", "Cena brutto"])
    sheet.append(["PE 500 PŁYTA GR. 12,5 MM NATUR.", "m2", 100, 123])
    sheet.append(["PE 500 WAŁEK FI 20 MM", "mb", 50, 61.5])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cennik.xlsx"
        workbook.save(path)
        items = read_material_catalog(path)
    assert len(items) == 1
    assert items[0].material == "PE 500 NATUR"
    assert items[0].thickness == 12.5
    assert items[0].gross_price_m2 == 123


def test_catalog_family_removes_thickness_and_normalizes_black_board_name() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Produkt", "Jednostka", "Cena brutto"])
    sheet.append(["PA6 PŁYTA GR. 8 MM CZARNY", "m2", 120])
    sheet.append(["PA6 PŁYTA GR. 12 MM CZARNA", "m2", 130])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cennik.xlsx"
        workbook.save(path)
        items = read_material_catalog(path)
    labels = {catalog_family_label(item) for item in items}
    assert labels == {"PA6 PŁYTA CZARNA"}
    assert all("GR." not in label and "CZARNY" not in label for label in labels)


def test_catalog_family_merges_antistatic_abbreviations() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Produkt", "Jednostka", "Cena brutto"])
    sheet.append(["PŁYTA PE HD1000 GR. 8 MM CZARN/ANTYSTATYK", "m2", 120])
    sheet.append(["PŁYTA PE HD1000 GR. 12 MM CZARNA ANT", "m2", 130])
    sheet.append(["PŁYTA PE HD1000 GR. 15 MM CZARNA - ANTYSTATYCZNA", "m2", 140])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cennik.xlsx"
        workbook.save(path)
        items = read_material_catalog(path)
    assert {catalog_family_label(item) for item in items} == {
        "PŁYTA PE HD1000 CZARNA ANTYSTATYCZNA",
    }


def test_catalog_defaults_uncoloured_pa6_and_pom_h_to_natural_board() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Produkt", "Jednostka", "Cena brutto"])
    sheet.append(["PA6 PŁYTA GR. 8 MM", "m2", 120])
    sheet.append(["POM H PŁYTA GR. 20 MM", "m2", 130])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cennik.xlsx"
        workbook.save(path)
        items = read_material_catalog(path)
    assert {catalog_family_label(item) for item in items} == {
        "PA6 PŁYTA NATURALNA",
        "POM H PŁYTA NATURALNA",
    }


if __name__ == "__main__":
    test_supplier_catalog_filters_non_boards()
    test_catalog_finds_columns_and_thickness_without_fixed_sheet_name()
    test_catalog_family_removes_thickness_and_normalizes_black_board_name()
    test_catalog_family_merges_antistatic_abbreviations()
    test_catalog_defaults_uncoloured_pa6_and_pom_h_to_natural_board()
    print("test_material_catalog: OK")
