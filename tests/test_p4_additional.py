"""P4 additional tests: PDF smoke, debug-message isolation, XLSX round-trip."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Ensure SIEKACZ_DEBUG_CANDIDATES is NOT set for isolation tests below.
os.environ.pop("SIEKACZ_DEBUG_CANDIDATES", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import (
    OptimizationResult,
    OptimizationSettings,
    Project,
    ProjectMeta,
    SheetPart,
    SheetStock,
)
from import_export.pdf_report import generate_pdf
from import_export.xlsx_io import read_xlsx, write_xlsx
from workers.optimizer_worker import optimize_sheet_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_project() -> Project:
    project = Project()
    project.meta = ProjectMeta(
        client_name="Klient Testowy",
        order_number="ZAM-001",
        material="MDF",
        notes="Test P4",
    )
    project.sheet_stock = [
        SheetStock("standard", 1, 2000, 1000, 5, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)
    ]
    project.sheet_parts = [
        SheetPart("A", 300, 500, 3, "standard", 18, allow_rotation=True),
        SheetPart("B", 200, 150, 4, "standard", 18, allow_rotation=True),
    ]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=4.0,
        margin=0,
        cutting_mode="hybrid",
        optimization_mode="comfort",
        min_reusable_offcut_size=80,
    )
    return project


# ---------------------------------------------------------------------------
# 1. PDF smoke — no crash, valid file produced
# ---------------------------------------------------------------------------

def test_pdf_smoke_no_result() -> None:
    """generate_pdf with result=None must produce a valid PDF file."""
    project = _simple_project()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        path = tmp.name
    try:
        generate_pdf(path, project, None, company_name="SIEKACZ 9000")
        data = Path(path).read_bytes()
        assert data[:4] == b"%PDF", "File does not start with PDF header"
        assert len(data) > 500, f"PDF suspiciously small: {len(data)} bytes"
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] PDF smoke (no result)")


def test_pdf_smoke_with_result() -> None:
    """generate_pdf with a real OptimizationResult must produce a valid PDF."""
    project = _simple_project()
    result = optimize_sheet_project(project)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        path = tmp.name
    try:
        generate_pdf(path, project, result, company_name="Firma Testowa Sp. z o.o.")
        data = Path(path).read_bytes()
        assert data[:4] == b"%PDF", "File does not start with PDF header"
        assert len(data) > 1000, f"PDF suspiciously small: {len(data)} bytes"
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] PDF smoke (with result)")


def test_pdf_smoke_polish_characters() -> None:
    """Polish diacritics in company name and meta must not crash ReportLab."""
    project = _simple_project()
    project.meta.client_name = "Zażółć gęślą jaźń"
    project.meta.notes = "Próba z polskimi znakami: ą ę ó ś ź ż ć ń"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        path = tmp.name
    try:
        generate_pdf(path, project, None, company_name="Stolarnia Świętosław Łukasiewicz")
        data = Path(path).read_bytes()
        assert data[:4] == b"%PDF"
        assert len(data) > 500
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] PDF smoke (Polish diacritics)")


# ---------------------------------------------------------------------------
# 2. Debug-message isolation — messages must be empty without env var
# ---------------------------------------------------------------------------

_DEBUG_KEYWORDS = (
    "Optimizer debug",
    "Winner candidate",
    "repair pass",
    "difficulty cache",
    "candidates generated",
    "candidate rejected",
    "waste-first",
)


def test_no_debug_leak_in_production() -> None:
    """result.messages must not contain debug candidate info when SIEKACZ_DEBUG_CANDIDATES is unset."""
    assert "SIEKACZ_DEBUG_CANDIDATES" not in os.environ, \
        "Test must run without SIEKACZ_DEBUG_CANDIDATES set"
    project = _simple_project()
    result = optimize_sheet_project(project)
    noisy = [m for m in result.messages if any(kw in m for kw in _DEBUG_KEYWORDS)]
    assert not noisy, f"Debug messages leaked into production result:\n" + "\n".join(noisy[:5])
    print(f"[OK] No debug leak ({len(result.messages)} messages, none noisy)")


def test_debug_messages_present_when_flag_set() -> None:
    """When SIEKACZ_DEBUG_CANDIDATES=1, at least some debug info must appear in messages."""
    os.environ["SIEKACZ_DEBUG_CANDIDATES"] = "1"
    try:
        project = _simple_project()
        result = optimize_sheet_project(project)
        noisy = [m for m in result.messages if any(kw in m for kw in _DEBUG_KEYWORDS)]
        assert noisy, "Expected debug messages when SIEKACZ_DEBUG_CANDIDATES=1, got none"
        print(f"[OK] Debug messages present when flag set ({len(noisy)} matching)")
    finally:
        os.environ.pop("SIEKACZ_DEBUG_CANDIDATES", None)


# ---------------------------------------------------------------------------
# 3. XLSX round-trip
# ---------------------------------------------------------------------------

def test_xlsx_roundtrip_single_sheet() -> None:
    """write_xlsx → read_xlsx preserves all rows and values for a single sheet."""
    rows = [
        {"Nazwa": "Blat", "Szerokość": 800, "Wysokość": 600, "Ilość": 2},
        {"Nazwa": "Półka", "Szerokość": 400, "Wysokość": 300, "Ilość": 4},
        {"Nazwa": "Bok", "Szerokość": 200, "Wysokość": 700, "Ilość": 2},
    ]
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    try:
        write_xlsx(path, {"Formatki": rows})
        loaded = read_xlsx(path, sheet_name="Formatki")
        assert len(loaded) == len(rows), f"Row count mismatch: {len(loaded)} vs {len(rows)}"
        for original, read_back in zip(rows, loaded):
            for key, value in original.items():
                assert str(read_back.get(key)) == str(value), \
                    f"Value mismatch for '{key}': {read_back.get(key)!r} != {value!r}"
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] XLSX round-trip (single sheet)")


def test_xlsx_roundtrip_multiple_sheets() -> None:
    """write_xlsx with multiple sheets produces a file readable sheet by sheet."""
    sheets = {
        "Płyty": [{"Materiał": "MDF", "Szerokość": 2000, "Wysokość": 1000, "Ilość": 5}],
        "Formatki płytowe": [{"Nazwa": "A", "Szerokość": 300, "Wysokość": 500, "Ilość": 3}],
        "Odpady użyteczne": [],
    }
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    try:
        write_xlsx(path, sheets)
        for sheet_name, expected_rows in sheets.items():
            loaded = read_xlsx(path, sheet_name=sheet_name)
            assert len(loaded) == len(expected_rows), \
                f"Sheet '{sheet_name}': row count {len(loaded)} != {len(expected_rows)}"
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] XLSX round-trip (multiple sheets, including empty)")


def test_xlsx_empty_project_parts() -> None:
    """write_xlsx with all-empty sheets must not crash."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    try:
        write_xlsx(path, {"Płyty": [], "Formatki": [], "Odpady": []})
        assert Path(path).stat().st_size > 0
    finally:
        Path(path).unlink(missing_ok=True)
    print("[OK] XLSX empty sheets (no crash)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_pdf_smoke_no_result()
    test_pdf_smoke_with_result()
    test_pdf_smoke_polish_characters()
    test_no_debug_leak_in_production()
    test_debug_messages_present_when_flag_set()
    test_xlsx_roundtrip_single_sheet()
    test_xlsx_roundtrip_multiple_sheets()
    test_xlsx_empty_project_parts()
    print("\ntest_p4_additional: OK")
