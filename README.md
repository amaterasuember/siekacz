# SIEKACZ 9000

A native Python/PySide6 desktop application for sheet and linear cutting optimization. It runs as a normal desktop program and does not use Electron, React, Vite, browser UI, or a local server.

## Najprostsze uruchomienie

Na Windows uruchamiaj aplikację tylko tym jednym plikiem z katalogu głównego projektu:

```text
START_SIEKACZ.bat
```

To jest launcher developerski: zawsze odpala aktualny kod z `main.py` w tym folderze, przez lokalne `.venv`. Nie uruchamia starego `.exe` z `dist\portable` ani żadnej zbudowanej paczki.

Skrypt sam uruchomi `scripts\SETUP_ENV.bat`, utworzy lokalny folder `.venv`, zainstaluje biblioteki z `requirements.txt` i odpali aplikację. Przy pierwszym uruchomieniu potrzebny jest internet do pobrania bibliotek. Kolejne uruchomienia korzystają już z lokalnego środowiska.

Jeżeli testujesz zmiany w kodzie, zamknij stare okno aplikacji i uruchom ponownie `START_SIEKACZ.bat`.

Pliki w `scripts\` są techniczne. Używaj ich tylko do testów, builda albo instalatora.

Jeśli chcesz przenieść program na komputer bez instalowania bibliotek Pythona, zbuduj wersję `.exe`:

```text
scripts\BUILD_EXE.bat
```

Po zakonczeniu przenies na drugi komputer caly folder:

```text
dist\portable\SIEKACZ9000
```

Na drugim komputerze uruchom:

```text
SIEKACZ9000.exe
```

Aktualny build zostawia jeden gotowy folder `dist\portable\SIEKACZ9000` w katalogu projektu. To caly folder nalezy przeniesc na inny komputer.

## Instalator dla klienta

Zeby wyslac jeden plik instalacyjny, uruchom:

```text
scripts\BUILD_INSTALLER.bat
```

Skrypt buduje aplikacje i tworzy:

```text
dist\installer\SIEKACZ9000_Setup.exe
```

Ten jeden plik mozna wyslac dalej. Instalator umieszcza aplikacje w jednym folderze uzytkownika, dodaje skrot na pulpicie oraz wpis w menu Start.

## Aktualizacje z GitHub

Aplikacja sprawdza najnowszy release w repozytorium:

```text
https://github.com/amaterasuember/siekacz/releases
```

Mechanizm aktualizacji pobiera asset instalatora z release'a, najlepiej:

```text
SIEKACZ9000_Setup.exe
```

Publikacja nowej wersji:

1. Zmien wersje w `app/version.py`, np. `2.0.1`.
2. Commituj zmiany do repozytorium.
3. Utworz tag zgodny z wersja, np. `v2.0.1`.
4. Wypchnij tag na GitHub.

Workflow `.github/workflows/release.yml` zbuduje instalator na Windows i opublikuje go w GitHub Release. Po uruchomieniu starszej wersji programu przycisk/okno aktualizacji pobierze ten instalator i uruchomi go.

## Test stabilnosci

Przed pokazem lub wysylka uruchom:

```text
scripts\RUN_TESTS.bat
```

Skrypt odpala testy stabilnosci z lokalnego `.venv`.

## Wersja i logi

- Aktualna wersja aplikacji jest zdefiniowana w `app/version.py`.
- Log bledow aplikacji zapisuje sie lokalnie w katalogu uzytkownika:
  `%USERPROFILE%\.cut_optimizer_desktop\siekacz9000.log`

Po starcie otwiera się szybki kalkulator rozkroju: wymiary płyty, dostępna ilość płyt, formatki i przycisk `Oblicz rozkrój`. Motyw jasny/ciemny i naddatek płyty do obliczeń są w zakładce `Ustawienia`.

Interfejs startuje od razu jako szybki kalkulator rozkroju, bez dodatkowego wybierania projektu.

## Reczne uruchomienie

```powershell
cd "SIEKACZ 9000"
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Reczny start

```powershell
python main.py
```

The app opens a native Qt window and loads the sample project automatically.

## Features

- Project JSON files with metadata, recent projects, and autosave.
- Editable stock and parts tables with paste support from Excel.
- 1D cutting with First Fit Decreasing and Best Fit Decreasing.
- 2D cutting with Guillotine, MaxRects, and Skyline algorithms.
- Kerf, margin, rotation, material, thickness, stock quantity, cost, waste, utilization, and reusable offcut calculations.
- Background optimization via `QThread` so the UI remains responsive.
- Interactive `QGraphicsView` layout preview with zoom, pan, tooltips, labels, dimensions, offcut areas, missing sheets highlighted in red, and PNG export.
- SQLite project history, settings, and reusable leftovers.
- CSV/XLSX import, XLSX export, JSON project save, PDF production reports, and PNG layout export.
- Dark industrial theme by default, plus a light theme option.

## Project Structure

```text
SIEKACZ 9000/
  main.py
  requirements.txt
  README.md
  START_SIEKACZ.bat
  scripts/
    SETUP_ENV.bat
    RUN_TESTS.bat
    BUILD_EXE.bat
    BUILD_INSTALLER.bat
  dist/              generated builds and installers
  tools/             local debug helpers and offline installers
  installer_src/
  app/
  ui/
  core/
  algorithms/
  database/
  import_export/
  workers/
  sample_data/
```

## Import CSV/XLSX

Use `Import` from the main menu and choose the target table: sheet stock, linear stock, sheet parts, or linear parts. Column names should match the visible table headers. Excel clipboard paste is also supported directly in the tables.

## Generate PDF

Run an optimization, then choose `Export > PDF report` or open the `Reports` panel and click `Export PDF report`. The report includes project information, parts, statistics, layouts, cost estimate, waste, and reusable offcuts.

## Budowanie przez PyInstaller

Najprosciej uzyc `scripts\BUILD_EXE.bat`. Recznie mozna wykonac:

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name SIEKACZ9000 --add-data "database\schema.sql;database" --add-data "sample_data;sample_data" main.py
```
