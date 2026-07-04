# QA audit - SIEKACZ 9000

Data: 2026-05-15

## Technologia

- Aplikacja desktopowa: Python + PySide6.
- Start developerski: `START_SIEKACZ.bat` lub `.venv\Scripts\python.exe main.py`.
- Build przenosny: PyInstaller przez `BUILD_EXE.bat` albo bezpośrednio `python -m PyInstaller`.
- Zależności projektu: `PySide6`, `reportlab`, `openpyxl`; `pytest` nie jest zainstalowany w `.venv`.

## Główne pliki

- UI szybkiej aplikacji: `app/simple_window.py`.
- Motyw i kursory przycisków: `app/theme.py`.
- Podgląd i render płyt: `ui/layout_view.py`.
- Loader panelu preview: `ui/optimization_progress.py`.
- Przełącznik Comfort/Sport: `ui/cutting_mode_switch.py`.
- Eksport PNG: `import_export/image_export.py`.
- Modele danych: `core/models.py`.
- Uruchamianie optymalizacji z projektu: `workers/optimizer_worker.py`.
- Główny algorytm produkcyjny Comfort/Sport: `algorithms/two_d_vertical_segmented.py`.
- Metryki/scoring odpadu i warsztatowości: `algorithms/layout_scoring.py`.
- Walidacja/gilotyna/cut tree: `algorithms/guillotine_technology.py`.

## Istniejące testy

- `tests/test_vertical_segmented.py`
- `tests/test_comfort_sport_modes.py`
- `tests/test_guillotine_technology.py`
- `tests/test_remnant_scoring.py`

Testy są napisane w stylu pytest, ale projekt nie ma zainstalowanego `pytest`, dlatego audyt uruchamia je także własnym prostym runnerem importującym funkcje `test_*`.

## Dodany test audytowy

- `tests/stability_smoke.py`

Zakres:
- regresje znanych przypadków,
- losowe fuzz testy,
- podstawowa wydajność do 500 formatek,
- walidacja UI dla `NaN`, `Infinity`, ilości ułamkowych i zbyt dużej liczby formatek,
- loader w panelu preview,
- eksport PNG w trybie drukowania.

## Dostępne komendy

- `.venv\Scripts\python.exe -m py_compile ...` - działa.
- `.venv\Scripts\python.exe tests\stability_smoke.py` - działa.
- `RUN_TESTS.bat` - dziala jako szybki wrapper na `tests\stability_smoke.py`.
- `.venv\Scripts\python.exe -m pytest tests` - nie działa, brak modułu `pytest`.
- `python -m pytest tests` - nie działa, systemowy Python nie ma `pytest`.
- `python tests\stability_smoke.py` - nie działa, systemowy Python nie ma `PySide6`.
- `BUILD_EXE.bat` - dostępny, ale kończy się `pause`, więc do automatycznego audytu wygodniejszy jest bezpośredni PyInstaller.
- `git` - niedostępny w PATH na tej maszynie.

## Uwagi startowe

Największe ryzyka przed testami:
- dane tekstowe w tabeli formatek mogą omijać typową walidację spinboxów,
- bardzo duże ilości formatek mogą przeciążyć optymalizator,
- eksport PNG musi być testowany przez bezpośredni render sceny, bo normalna akcja otwiera dialog pliku,
- testy pytest są obecne, ale bez zależności nie da się ich uruchomić standardową komendą.
