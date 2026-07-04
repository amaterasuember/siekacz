# Stability report - SIEKACZ 9000

Data: 2026-05-15

## 1. Podsumowanie

Aplikacja jest wyraźnie stabilniejsza po audycie i nadaje się do pokazania, z zastrzeżeniem że testy automatyczne należy uruchamiać przez `.venv`, bo systemowy Python nie ma zależności aplikacji.

Najważniejsze ryzyka po audycie:
- w projekcie są testy pytestowe, ale `pytest` nie jest zainstalowany,
- brak `git` w PATH utrudnia kontrolę zmian na tej maszynie,
- testy skalowania Windows 125/150/175% zostały pokryte pośrednio przez test offscreen i resize, ale nie pełnym manualnym testem na kilku monitorach.

## 2. Co zostało przetestowane

- Start aplikacji z kodu PySide6 w trybie offscreen.
- Start zbudowanego `SIEKACZ9000\SIEKACZ9000.exe`.
- Walidacja inputów tabeli: `NaN`, `Infinity`, puste/tekstowe wartości, ułamkowa ilość, zbyt duża suma ilości.
- Algorytm: proste przypadki, za duża formatka, dużo małych formatek, ekstremalny kerf, znane regresje warsztatowe.
- Comfort/Sport: osobne wyniki i walidacja guillotine/cut tree.
- Fuzz: 80 losowych zestawów wymiarów, ilości, kerf i trybu.
- Wydajność: 10, 50, 100, 250, 500 formatek.
- UI: szybkie podwójne kliknięcie obliczania, blokada przycisków podczas loadera, znikanie loadera.
- Loader: widoczność tylko w preview, zakrycie obszaru, zniknięcie po obliczeniu.
- Eksport PNG: bez dialogu, przez bezpośredni render sceny w trybie drukowania.
- Build PyInstaller i krótki start EXE.

## 3. Znalezione błędy

| Priorytet | Obszar | Problem | Status |
|---|---|---|---|
| High | Walidacja danych | Tabela formatek przepuszczała `NaN` / `Infinity` jako wymiar, bo `float()` nie rzuca błędu dla tych wartości. | Naprawione |
| High | Walidacja danych | Ilość formatek mogła być ułamkowa i była obcinana do int, np. `1,5` -> `1`. | Naprawione |
| High | Stabilność | Bardzo duża ilość formatek mogła przeciążyć optymalizator bez jasnego komunikatu. | Naprawione limitem UI `5000` |
| Medium | Algorytm/regresja | Przypadek `200x700 + 90x150 + 50x50` przestał używać średnich formatek jako wypełnienia pod większymi pasami. | Naprawione |
| Medium | Testy regresji | Jeden test oczekiwał starej pozycji `1000x1450`, sprzecznej z później ustalonym układem warsztatowym. | Zaktualizowane |
| Medium | Narzędzia | `pytest` nie jest zainstalowany w `.venv`. | Zanotowane, testy uruchomione custom runnerem |
| Low | Narzędzia | `git` nie jest dostępny w PATH. | Zanotowane |

## 4. Naprawione błędy

- `app/simple_window.py`
  - Dodano ścisłą walidację liczb skończonych dla tabeli formatek.
  - Odrzucono `NaN`, `Infinity`, tekst, puste pola oraz ilości ułamkowe.
  - Dodano limit `MAX_TOTAL_PARTS = 5000` z polskim komunikatem zamiast ryzyka zawieszenia aplikacji.

- `algorithms/two_d_vertical_segmented.py`
  - Poszerzono kryterium formatek wypełniających końcówki pasów.
  - Dla fillerów preferowana jest niższa orientacja, dzięki czemu `90x150` może wejść jako `150x90` pod większym elementem.

- `tests/test_remnant_scoring.py`
  - Zaktualizowano oczekiwanie dla pozycji `1000x1450` zgodnie z aktualnym, preferowanym układem warsztatowym: prawy pas, start od góry.

## 5. Testy dodane do projektu

- `tests/stability_smoke.py`

Zawiera:
- `test_regression_cases`
- `test_previous_customer_regressions`
- `test_fuzz_random_inputs`
- `test_performance_sanity`
- `test_ui_validation_loader_and_export`

Komenda:

```powershell
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

## 6. Uruchomione komendy

```powershell
python -m pytest tests
```

Wynik: nie działa, systemowy Python nie ma `pytest`.

```powershell
python tests\stability_smoke.py
```

Wynik: nie działa, systemowy Python nie ma `PySide6`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

Wynik: nie działa, `.venv` nie ma `pytest`.

```powershell
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

Wynik: PASS.

```powershell
.\.venv\Scripts\python.exe -m py_compile app\simple_window.py tests\stability_smoke.py
```

Wynik: PASS.

```powershell
.\.venv\Scripts\python.exe -m py_compile (Get-ChildItem app,ui,algorithms,core,workers,import_export,database,tests -Recurse -Filter *.py | ForEach-Object { $_.FullName })
```

Wynik: PASS.

Custom runner dla wszystkich funkcji `test_*`:

```powershell
.\.venv\Scripts\python.exe - <<'PY'
import importlib, inspect, os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for name in [
    "tests.test_vertical_segmented",
    "tests.test_comfort_sport_modes",
    "tests.test_guillotine_technology",
    "tests.test_remnant_scoring",
    "tests.stability_smoke",
]:
    module = importlib.import_module(name)
    for fn_name, fn in inspect.getmembers(module, inspect.isfunction):
        if fn_name.startswith("test_"):
            fn()
PY
```

Wynik: PASS.

Build:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name SIEKACZ9000 --icon "assets\app_icon.ico" --add-data "assets;assets" --add-data "database\schema.sql;database" --add-data "sample_data;sample_data" main.py
```

Wynik: PASS. EXE: `SIEKACZ9000\SIEKACZ9000.exe`.

Start EXE:

```powershell
Start-Process SIEKACZ9000\SIEKACZ9000.exe -WindowStyle Hidden
```

Wynik: proces wystartował poprawnie i został zamknięty po krótkim smoke teście.

## 7. Wyniki wydajności

Smoke performance:

- 10 formatek: ok. 0.01 s
- 50 formatek: ok. 0.05-0.08 s
- 100 formatek: ok. 0.10-0.19 s
- 250 formatek: ok. 0.49-0.57 s
- 500 formatek: ok. 1.65-1.91 s

Wszystkie przypadki przeszły walidację: brak overlapów, brak wyjścia poza płytę, poprawne kerf, wykorzystanie <= 100%.

## 8. Instrukcja szybkiego testu przed pokazem

- [ ] Uruchomić `SIEKACZ9000\SIEKACZ9000.exe`.
- [ ] Dodać formatki `500 x 500`, ilość `2`.
- [ ] Kliknąć `Oblicz rozkrój`.
- [ ] Sprawdzić, czy loader znika i wynik jest widoczny.
- [ ] Przełączyć Comfort/Sport i przeliczyć.
- [ ] Wpisać błędną ilość `1,5` i sprawdzić komunikat.
- [ ] Wyeksportować PNG i sprawdzić, czy plik nie jest pusty.
- [ ] Zamknąć i uruchomić ponownie.

## 9. Rekomendacje nieblokujące

- Dodać `pytest` do zależności developerskich albo utworzyć prosty `RUN_TESTS.bat`.
- Dodać `git` do PATH na komputerze deweloperskim.
- Dodać osobny manualny checklist testów na monitorach ze skalowaniem Windows 125%, 150%, 175%.
- Rozważyć widoczny komunikat UI o limicie `5000` formatek przy bardzo dużych zleceniach.
