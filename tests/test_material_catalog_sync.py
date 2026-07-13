"""Regression checks for the bundled material catalogue update path."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.material_catalog import (
    MaterialCatalogEntry,
    catalog_revision,
    merge_bundled_catalog,
)


def _entry(material: str, thickness: float, price: float, product: str) -> MaterialCatalogEntry:
    return MaterialCatalogEntry(material, thickness, price / 1.23, price, product)


def test_bundled_catalog_updates_matching_positions_and_keeps_local_extras() -> None:
    local = [
        _entry("PA6", 18, 100, "PA6 PŁYTA GR. 18 MM"),
        _entry("WŁASNY", 12, 250, "WŁASNY MATERIAŁ GR. 12 MM"),
    ]
    bundled = [
        _entry("PA6", 18, 125, "PA6 PŁYTA GR. 18 MM"),
        _entry("POM C", 20, 200, "POM C PŁYTA GR. 20 MM NATURALNA"),
    ]
    merged = merge_bundled_catalog(local, bundled)
    by_product = {entry.product_name: entry for entry in merged}
    assert len(merged) == 3
    assert by_product["PA6 PŁYTA GR. 18 MM"].gross_price_m2 == 125
    assert "WŁASNY MATERIAŁ GR. 12 MM" in by_product
    print("[OK] bundled catalogue updates shared positions and keeps local extras")


def test_catalog_revision_changes_when_the_shipped_file_changes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalog.xlsx"
        path.write_bytes(b"first release")
        first = catalog_revision(path)
        path.write_bytes(b"second release")
        assert catalog_revision(path) != first
    print("[OK] catalogue revision follows bundled file contents")


if __name__ == "__main__":
    test_bundled_catalog_updates_matching_positions_and_keeps_local_extras()
    test_catalog_revision_changes_when_the_shipped_file_changes()
    print("test_material_catalog_sync: OK")
