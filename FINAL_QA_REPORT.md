# FINAL QA REPORT — SIEKACZ 9000

## 1. Status końcowy

- GOTOWE DO PREZENTACJI

## 2. Zakres audytu

- Algorytm: sprawdzono Comfort/Sport, orientację płyt, brakujące czerwone płyty, kerf z tolerancją, rotację pojedynczych sztuk, bottom strip, wieloformatowe płyty, remnant scoring, vertical segmented i walidację guillotine.
- UI: sprawdzono offscreen główne okno, walidację tabeli formatek, loader, LayoutView, eksport ze sceny i brak zawieszenia po podwójnym kliknięciu obliczania.
- Eksport PNG: sprawdzono generowanie pliku, tryb jasny/print mode, białe tło, nagłówek bez pola „Firma:” i czytelny output.
- Historia projektów: sprawdzono serializację wyniku, odtwarzanie wyniku, wiele formatów płyt i notatki/metadane projektu.
- Ustawienia: sprawdzono domyślne ścieżki kodu dla Comfort/dark oraz brak wpływu UI na geometrię wyniku w testach offscreen.
- Build/installer: zbudowano folder przenośny EXE oraz instalator Inno Setup.
- Testy wydajności: uruchomiono przypadki 10, 50, 100, 250, 500 i 1000 formatek.
- Fuzz/determinizm: uruchomiono 80 losowych przypadków i 10 powtórzeń deterministycznych dla krytycznego case’u 40 x 900.

## 3. Uruchomione komendy

- `Get-ChildItem`, `rg --files`, `rg -n`, `Get-Content` — analiza struktury i plików projektu, OK.
- `.\.venv\Scripts\python.exe -m pytest tests -q` — FAIL, w venv nie ma modułu `pytest`.
- `Get-ChildItem tests -Filter *.py | Sort-Object Name | ForEach-Object { ... }` — PASS, wszystkie testy skryptowe przeszły.
- `.\.venv\Scripts\python.exe -m py_compile .\workers\optimizer_worker.py .\tests\test_final_qa_guardrails.py` — PASS.
- `.\.venv\Scripts\python.exe .\tests\test_final_qa_guardrails.py` — PASS.
- `.\.venv\Scripts\python.exe -m compileall -q app algorithms core database import_export ui workers tests main.py` — PASS.
- `.\.venv\Scripts\python.exe -m PyInstaller --version` — PASS, PyInstaller 6.20.0.
- `.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name SIEKACZ9000 ... main.py` — PASS, EXE zbudowany.
- `& "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe" ".\installer_src\SIEKACZ9000.iss"` — PASS, instalator zbudowany.
- `Start-Process .\SIEKACZ9000\SIEKACZ9000.exe -WindowStyle Hidden -PassThru` — PASS, EXE wystartował i został zamknięty po 5 sekundach.
- Test wydajności 1000 części przez inline Python — PASS, 21.739 s, 2 płyty, 0 braków.
- `git status --short` — FAIL, `git` nie jest dostępny w środowisku PowerShell.

## 4. Lista uruchomionych testów

- `tests/stability_smoke.py`
- `tests/test_comfort_sport_modes.py`
- `tests/test_final_qa_guardrails.py`
- `tests/test_guillotine_technology.py`
- `tests/test_mixed_orientation_compaction.py`
- `tests/test_remnant_scoring.py`
- `tests/test_sport_bottom_strip_rotation.py`
- `tests/test_stock_orientation_repeatability.py`
- `tests/test_vertical_segmented.py`
- Smoke EXE z paczki `SIEKACZ9000\SIEKACZ9000.exe`
- Inline performance sanity dla 1000 formatek

## 5. Wyniki testów

- Testy skryptowe: PASS.
- Py compile / compileall: PASS.
- Fuzz: PASS, 80 losowych przypadków.
- Performance: PASS, do 1000 formatek.
- Build EXE: PASS.
- Build instalatora: PASS.
- Smoke start EXE: PASS.
- `pytest`: FAIL tylko z powodu braku zainstalowanego modułu `pytest`; testy uruchomione bezpośrednio jako skrypty przeszły.

Najważniejsze obserwacje:
- Krytyczny przypadek `2000 x 1000` vs `1000 x 2000`, `40 x 900 x 55`, kerf 5 daje identyczny podpis layoutu po kanonizacji: 48 zmieszczonych, 7 braków i 1 brakująca płyta.
- Brakujące czerwone płyty są liczone przez normalną ścieżkę algorytmu i mają `source="missing"`.
- Wyniki są deterministyczne w 10 powtórzeniach tego samego wejścia.
- Testy invariantów nie wykryły overlapów, wyjścia poza stock, NaN, Infinity, ujemnego odpadu ani wykorzystania powyżej 100%.

## 6. Znalezione błędy

| Priorytet | Obszar | Problem | Status |
| --- | --- | --- | --- |
| High | Worker optymalizacji | Bezpośrednia ścieżka `optimize_sheet_project()` mogła przyjąć ujemny kerf i go cicho skorygować zamiast odrzucić. | Naprawione |
| High | Worker optymalizacji | Bezpośrednia ścieżka workera nie miała pełnej walidacji NaN/Infinity/zerowych ilości poza UI. | Naprawione |
| Medium | Test runner | `pytest` nie jest zainstalowany w `.venv`, więc standardowa komenda `python -m pytest tests -q` nie działa. | Pozostałe ryzyko narzędziowe; testy działają jako skrypty |
| Low | Narzędzia repo | `git` nie jest dostępny w PowerShell, więc nie dało się użyć `git status`. | Pozostałe ryzyko środowiska |

## 7. Naprawione błędy

- Dodano walidację projektu przed startem optymalizacji w `workers/optimizer_worker.py`.
- Worker odrzuca teraz:
  - ujemny kerf,
  - NaN,
  - Infinity,
  - zerowe i ujemne wymiary,
  - zerowe, ujemne i niecałkowite ilości,
  - brak płyt,
  - brak formatek.
- Dodano regresje w `tests/test_final_qa_guardrails.py`, które pokrywają:
  - powtarzalność `2000 x 1000` vs `1000 x 2000`,
  - deterministyczne missing sheets,
  - walidację błędnych danych bezpośrednio na workerze,
  - mieszaną orientację formatek,
  - wiele formatów płyt w historii/serializacji,
  - nagłówek PNG bez pola „Firma:”.

## 8. Pliki zmienione

- `workers/optimizer_worker.py`
- `tests/test_final_qa_guardrails.py`
- `FINAL_QA_REPORT.md`

Artefakty zbudowane:
- `SIEKACZ9000\SIEKACZ9000.exe`
- `INSTALLER\SIEKACZ9000_Setup.exe`

## 9. Ryzyka pozostałe

- `pytest` nie jest zainstalowany w środowisku. Testy przeszły jako skrypty, ale dla standardowego CI warto dodać `pytest` do `requirements-dev.txt` albo do instrukcji developerskiej.
- `git` nie jest dostępny z tej powłoki, więc nie mogłem pokazać finalnego diffu przez `git status`.
- Nie ma matematycznego dowodu optymalności dla każdego możliwego zestawu formatek. Testy pokrywają regresje, fuzz, determinizm i krytyczne przypadki użytkownika.
- Pełny manualny test widocznego UI wykonano przez smoke/offscreen i start EXE; nie wykonywałem długiej interakcji ręcznej w oknie, żeby nie blokować środowiska.

## 10. Build

- EXE: PASS.
- Lokalizacja EXE: `C:\Users\Naprawa\Desktop\SIEKACZ 9000\SIEKACZ9000\SIEKACZ9000.exe`
- Rozmiar EXE: 2 380 394 bajty.
- Instalator: PASS.
- Lokalizacja instalatora: `C:\Users\Naprawa\Desktop\SIEKACZ 9000\INSTALLER\SIEKACZ9000_Setup.exe`
- Rozmiar instalatora: 39 054 036 bajtów.

## 11. Instrukcja szybkiego testu przed pokazem

1. Uruchom aplikację.
2. Sprawdź, czy domyślnie jest Comfort + ciemny motyw.
3. Dodaj płytę 2000 x 1000.
4. Dodaj formatkę 40 x 900 x 55.
5. Policz Comfort.
6. Zanotuj liczbę wyciętych i braków.
7. Zamień stock na 1000 x 2000.
8. Policz Comfort.
9. Porównaj liczbę wyciętych i braków.
10. Sprawdź, czy nie ma overlapów ani wyjścia poza płytę.
11. Eksportuj PNG.
12. Sprawdź, czy PNG ma białe tło i czytelną legendę.
13. Zapisz projekt.
14. Otwórz projekt z historii.
15. Sprawdź, czy stocki, formatki, notatka i wynik wróciły poprawnie.
16. Przełącz Sport i policz ponownie.
17. Sprawdź, czy loader znika po zakończeniu.
