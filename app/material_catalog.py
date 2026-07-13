from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook


_THICKNESS_RE = re.compile(r"\bGR\.?\s*(\d+(?:[.,]\d+)?)\s*MM\b", re.IGNORECASE)
_BOARD_RE = re.compile(r"\bP[ŁL]YTA\b", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_BLACK_MASCULINE_RE = re.compile(r"\bCZARNY\b", re.IGNORECASE)
_BLACK_ANTISTATIC_RE = re.compile(
    r"\bCZARN(?:A|Y)?\s*[-/]?\s*(?:ANTYSTATYCZNA|ANTYSTATYK|ANTYST|ANT|AST)\b",
    re.IGNORECASE,
)
_ANTISTATIC_SHORT_RE = re.compile(r"\b(?:ANTYSTATYK|ANTYST|ANT|AST)\b", re.IGNORECASE)
_BLUE_SHORT_RE = re.compile(r"\bNIEB(?:IESKI)?\b", re.IGNORECASE)
_NATURAL_SHORT_RE = re.compile(r"\bNATUR\b", re.IGNORECASE)
_NATURAL_DEFAULT_MATERIALS = {"PA6", "POM H"}


@dataclass(frozen=True)
class MaterialCatalogEntry:
    material: str
    thickness: float
    net_price_m2: float
    gross_price_m2: float
    product_name: str
    source_sheet: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _header_key(value: object) -> str:
    text = str(value or "").strip().casefold()
    return _SPACE_RE.sub(" ", text)


def _number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _material_name(product_name: str, sheet_name: str) -> str:
    name = _BOARD_RE.sub(" ", product_name)
    name = _THICKNESS_RE.sub(" ", name)
    name = _SPACE_RE.sub(" ", name).strip(" -.,")
    return name or sheet_name.strip() or "Płyta"


def catalog_family_label(entry: MaterialCatalogEntry) -> str:
    """Return the complete board family without the variable thickness.

    Supplier workbooks often switch grammatical gender between ``CZARNY`` and
    ``CZARNA`` for the same board.  A board is displayed consistently as
    ``CZARNA`` so those rows become one searchable family in the selector.
    """
    label = _THICKNESS_RE.sub(" ", entry.product_name)
    label = _BLACK_ANTISTATIC_RE.sub("CZARNA ANTYSTATYCZNA", label)
    label = _BLACK_MASCULINE_RE.sub("CZARNA", label)
    label = _ANTISTATIC_SHORT_RE.sub("ANTYSTATYCZNA", label)
    label = _BLUE_SHORT_RE.sub("NIEBIESKA", label)
    label = _NATURAL_SHORT_RE.sub("NATURALNA", label)
    if entry.material.strip().upper() in _NATURAL_DEFAULT_MATERIALS and "NATURAL" not in label.upper():
        label = f"{label} NATURALNA"
    return _SPACE_RE.sub(" ", label).strip(" -.,")


def read_material_catalog(path: str | Path) -> list[MaterialCatalogEntry]:
    """Read board prices from a supplier workbook.

    Only square-metre board products are accepted. Pipes, rods and profiles
    normally use ``mb`` and are deliberately ignored. Column order and sheet
    names may change; columns are located by their normalized headers.
    """
    workbook = load_workbook(path, data_only=True, read_only=True)
    entries: list[MaterialCatalogEntry] = []
    seen: set[tuple[str, float, float, float]] = set()

    for sheet in workbook.worksheets:
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            continue
        columns = {_header_key(value): index for index, value in enumerate(header)}
        name_col = next((index for key, index in columns.items() if key in {"nazwa", "produkt", "opis"}), None)
        unit_col = next((index for key, index in columns.items() if key in {"j.m.", "jm", "jednostka", "j.m"}), None)
        net_col = next((index for key, index in columns.items() if "netto" in key), None)
        gross_col = next((index for key, index in columns.items() if "brutto" in key), None)
        if name_col is None or unit_col is None:
            continue

        for row in rows:
            product = str(row[name_col] or "").strip() if name_col < len(row) else ""
            unit = _header_key(row[unit_col] if unit_col < len(row) else "")
            match = _THICKNESS_RE.search(product)
            if unit not in {"m2", "m²"} or not match or not _BOARD_RE.search(product):
                continue
            thickness = _number(match.group(1))
            if thickness <= 0:
                continue
            net = _number(row[net_col]) if net_col is not None and net_col < len(row) else 0.0
            gross = _number(row[gross_col]) if gross_col is not None and gross_col < len(row) else 0.0
            material = _material_name(product, sheet.title)
            signature = (material.casefold(), thickness, net, gross)
            if signature in seen:
                continue
            seen.add(signature)
            entries.append(MaterialCatalogEntry(material, thickness, net, gross, product, sheet.title))

    workbook.close()
    return sorted(entries, key=lambda item: (item.material.casefold(), item.thickness, item.net_price_m2))


def catalog_from_dicts(items: Iterable[dict[str, object]]) -> list[MaterialCatalogEntry]:
    result: list[MaterialCatalogEntry] = []
    for item in items:
        try:
            result.append(MaterialCatalogEntry(
                material=str(item.get("material", "")).strip(),
                thickness=float(item.get("thickness", 0) or 0),
                net_price_m2=float(item.get("net_price_m2", 0) or 0),
                gross_price_m2=float(item.get("gross_price_m2", 0) or 0),
                product_name=str(item.get("product_name", "")).strip(),
                source_sheet=str(item.get("source_sheet", "")).strip(),
            ))
        except (TypeError, ValueError):
            continue
    return [item for item in result if item.material and item.thickness > 0]


def catalog_revision(path: str | Path) -> str:
    """Return a stable fingerprint for a bundled price-list file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merge_bundled_catalog(
    local_entries: Iterable[MaterialCatalogEntry],
    bundled_entries: Iterable[MaterialCatalogEntry],
) -> list[MaterialCatalogEntry]:
    """Update catalog positions shipped with the app without dropping local extras.

    A material family and its thickness identify a commercial position.  The
    bundled value wins for that position (new centrally released prices and
    labels), while a local position not present in the bundled catalogue stays
    available to the operator.
    """
    def key(entry: MaterialCatalogEntry) -> tuple[str, float]:
        return catalog_family_label(entry).casefold(), round(float(entry.thickness), 3)

    merged = {key(entry): entry for entry in local_entries}
    merged.update({key(entry): entry for entry in bundled_entries})
    return sorted(
        merged.values(),
        key=lambda entry: (catalog_family_label(entry).casefold(), entry.thickness, entry.product_name.casefold()),
    )
