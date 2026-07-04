# SIEKACZ 9000 / SIEKACZ X - dokumentacja dla Claude Code

Ten dokument jest praktycznym przewodnikiem dla agenta kontynuujacego prace nad projektem. Ma pomoc szybko zrozumiec architekture, zalozenia optymalizacji, miejsca ryzyka i sposob bezpiecznej walidacji zmian.

Stan odniesienia: 2026-05-23. Ostatnie prace dotyczyly optymalizatora rozkroju plyt: odzysk odpadow, mieszane orientacje, kerf-aware scoring, minimalizacja realnie zuzytej dlugosci plyty, repair pass dla drobnicy oraz spojne raportowanie wymiarow.

## 1. Czym jest projekt

SIEKACZ 9000 to natywna aplikacja desktopowa Python/PySide6 do optymalizacji rozkroju:

- plyt 2D, np. formatki z plyt meblowych, blach, paneli;
- pretow/profili 1D;
- z uwzglednieniem rzazu/kerf, marginesow, rotacji, materialu, grubosci, kosztow, odpadow i raportow.

Aplikacja nie jest webowa. Nie ma Reacta, Electron/Vite ani lokalnego serwera. UI jest w Qt/PySide6.

Glowne wejscie aplikacji:

- `main.py` - start Qt, inicjalizacja DB, preferencji, motywu, okna glownego.
- `app/simple_window.py` - glowne okno uzytkownika, szybki kalkulator rozkroju.
- `workers/optimizer_worker.py` - walidacja danych projektu i uruchamianie optymalizatora.

## 2. Najwazniejsza zasada projektu

Optymalizacja plyt ma byc realna produkcyjnie, nie tylko ladna wizualnie.

Kolejnosc priorytetow:

1. Umiescic wszystkie formatki, jezeli fizycznie sie da.
2. Minimalizowac liczbe uzytych plyt.
3. Minimalizowac realnie zuzyta dlugosc/obszar plyty, zwlaszcza dlugi bok arkusza.
4. Zachowac legalnosc ciec gilotynowych.
5. Maksymalnie wykorzystac odpady po trudnych/duzych elementach.
6. Zostawic duze, prostokatne, uzyteczne odpady.
7. Dopiero na koncu patrzec na procent wykorzystania pojedynczej plyty i estetyke.

Nie wolno wybierac ukladu tylko dlatego, ze ma wysoki procent wykorzystania, jezeli rozciaga sie niepotrzebnie po dlugosci plyty albo tworzy kolejna plyte/pas, ktorego da sie uniknac.

## 3. Srodowisko i uruchamianie

Minimalne zaleznosci sa w `requirements.txt`:

```text
PySide6>=6.6
reportlab>=4.0
openpyxl>=3.1
```

Typowe uruchomienie na Windows:

```powershell
.\START_SIEKACZ.bat
```

Reczne uruchomienie:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Budowanie wersji przenosnej:

```powershell
.\scripts\BUILD_EXE.bat
```

Budowanie instalatora:

```powershell
.\scripts\BUILD_INSTALLER.bat
```

Uwaga: w repo sa foldery wygenerowane (`build/`, `dist/`, `__pycache__/`). Zwykle nie trzeba ich czytac ani edytowac przy pracy nad kodem zrodlowym.

## 4. Struktura katalogow

```text
SIEKACZ 9000/
  main.py                         start aplikacji
  requirements.txt                zaleznosci runtime
  START_SIEKACZ.bat               najprostszy start
  scripts/RUN_TESTS.bat           smoke test
  scripts/BUILD_EXE.bat           PyInstaller portable exe
  scripts/BUILD_INSTALLER.bat     budowa instalatora

  app/
    simple_window.py              glowne okno UI
    main_window.py                starsze/alternatywne okno
    theme.py                      motywy i style Qt
    version.py                    wersja aplikacji
    logging_setup.py              logi uzytkownika
    project_history.py            historia projektow

  ui/
    layout_view.py                podglad rozkroju, eksport/rysowanie PNG
    optimizer_panel.py            ustawienia optymalizacji
    parts_panel.py                tabela formatek
    stock_panel.py                tabela plyt
    settings_panel.py             ustawienia aplikacji
    reports_panel.py              raporty
    leftovers_panel.py            odpady/uzyteczne resztki

  core/
    models.py                     dataclassy domenowe
    validation.py                 walidacja pomocnicza
    units.py                      jednostki

  workers/
    optimizer_worker.py           walidacja projektu, efektywny kerf, dispatch algorytmow, brakujace plyty

  algorithms/
    two_d_vertical_segmented.py   glowny optimizer plyt produkcyjnych
    guillotine_technology.py      walidacja gilotynowosci i cutTree
    layout_scoring.py             odpady, scoring, metryki
    two_d_guillotine.py           prosty algorytm gilotynowy
    two_d_maxrects.py             MaxRects fallback / tryb free
    two_d_skyline.py              Skyline fallback / tryb free
    one_d.py                      rozkroj liniowy

  import_export/
    image_export.py               eksport obrazow
    pdf_report.py                 raport PDF
    csv_io.py, xlsx_io.py         import/export tabel

  database/
    db.py, repositories.py        SQLite ustawien/historii
    schema.sql                    schemat DB

  tests/
    test_*.py                     regresje jednostkowe/skryptowe
    stability_smoke.py            smoke/fuzz/performance
```

## 5. Model danych

Najwazniejsze klasy sa w `core/models.py`.

### `SheetStock`

Opis plyty:

- `width`, `height` - wymiar obliczeniowy plyty;
- `quantity` - dostepna ilosc;
- `material`, `thickness` - dopasowanie do formatek;
- `allow_rotation`, `grain_direction` - ograniczenia obrotu;
- `min_offcut_width`, `min_offcut_height` - minimalny uzyteczny odpad;
- `source` - `"stock"` albo `"missing"`;
- `nominal_width`, `nominal_height`, `sheet_allowance` - rozroznienie wymiaru nominalnego i obliczeniowego po naddatku.

### `SheetPart`

Opis formatki:

- `width`, `height`, `quantity`;
- `material`, `thickness`;
- `allow_rotation`, `grain_direction`;
- `priority`;
- `label`, `notes`.

### `PlacedSheetPart`

Faktyczne polozenie:

- `x`, `y` - lewy gorny rog w ukladzie plyty;
- `width`, `height` - wymiar po ewentualnej rotacji;
- `rotated` - flaga obrotu wzgledem oryginalnej formatki.

### `SheetLayout`

Jeden arkusz/wizualizacja:

- `parts` - lista ulozonych formatek;
- `vertical_segments` - pasy/segmenty potrzebne do walidacji gilotynowej;
- `offcuts`, `waste_rects` - odpady metryczne/rysunkowe;
- `cut_tree`, `cut_operations` - dane technologiczne ciec;
- `is_guillotine_feasible`, `technology_warning` - wynik walidacji;
- `used_width`, `used_height`, `used_area`, `consumed_area`, `utilization` - wlasciwosci wyliczane dynamicznie.

Wazne: `used_width` i `used_height` nie zawieraja "pustego" konca plyty. To bounding box formatek. Dla plyt oszczedzanie dlugiego boku zwykle oznacza minimalizacje `used_width`, jezeli dlugi bok jest osia X.

### `OptimizationResult`

Wynik:

- `sheet_layouts` - plyty z realnego stocku;
- `missing_sheet_layouts` - wirtualne dodatkowe plyty dla brakujacych formatek;
- `unplaced_sheet_parts` - formatki, ktorych nie udalo sie zmiescic;
- globalne metryki odpadow/scoringu;
- `messages` - komunikaty i debug.

## 6. Przeplyw optymalizacji

Glowny przeplyw dla plyt:

1. UI sklada `Project` z tabel.
2. `OptimizerWorker.run()` odpala `optimize_sheet_project(project)`.
3. `workers/optimizer_worker.py`:
   - waliduje dane;
   - dodaje tolerancje do kerfu przez `_effective_kerf`;
   - aplikuje `sheet_allowance`;
   - uwzglednia globalne `allow_rotation`;
   - wybiera algorytm w `_run_sheet_algorithm`.
4. Domyslny/glowny algorytm plyt to `optimize_2d_vertical_segmented(...)`.
5. Po optymalizacji `workers/optimizer_worker.py` buduje `missing_sheet_layouts`, jesli zabraklo stocku.
6. UI rysuje wynik w `ui/layout_view.py`, a eksporty korzystaja z tych samych layoutow.

Wazne: ustawiony przez uzytkownika kerf nie trafia do algorytmow "surowy". Worker dodaje `KERF_TOLERANCE_MM = 0.2`. Czyli ustawienie 5.0 mm w UI daje efektywnie 5.2 mm. Testy niskopoziomowe moga wolac algorytm bezposrednio z kerf=5.

## 7. Glowny optimizer: `two_d_vertical_segmented.py`

To najwazniejszy plik przy pracy nad rozkrojem plyt. Jego rola:

- generuje wielu kandydatow ukladu;
- sortuje formatki wedlug strategii i trudnosci;
- tworzy pasy pionowe/segmenty;
- sprawdza mieszane orientacje tego samego typu formatki;
- odzyskuje odpady (`waste recovery`);
- przenosi drobnice do odpadow (`repair pass`);
- ocenia kandydatow leksykograficznie;
- deleguje walidacje technologiczna do `guillotine_technology.py`.

### Najwazniejsze funkcje i pojecia

- `_part_difficulty(...)` - scoring trudnosci formatki.
- `_is_small_filler_part(...)` - klasyfikacja drobnicy/fillera.
- `_saved_axis(stock)` - os, ktora oszczedzamy; obecnie X dla plyt szerszych niz wyzszych.
- `_used_bounding_box(layout)` - bounding box ulozonych formatek.
- `_saved_used_length(layout)` - realnie zuzyta dlugosc wzdluz osi oszczedzanej.
- `_strategy_set()` - zestaw strategii sortowania i anchorowania.
- `_build_vertical_strip_candidates(...)` - kandydaci pasowi.
- `_build_mixed_orientation_candidates(...)` - kandydaci mieszanych orientacji.
- `_build_orientation_split_candidates(...)` - testuje proporcje orientacji dla jednej grupy formatek.
- `_build_sport_bottom_strip_candidates(...)` - wariant z odzyskiem dolnego pasa.
- `_recover_internal_fillers(...)` - waste-first refill w obszarach klasy 1/2.
- `_recover_layout_right_strip(...)` - odzysk prawego stripu, gdy nic lepszego nie ma.
- `_repair_candidate_fillers(...)` - repair pass: wyjmij fillery i sproboj przeniesc je w odpady po wiekszych elementach.
- `_sport_score(...)`, `_comfort_score(...)` - wybor najlepszego kandydata.

### Aktualna filozofia optymalizacji

Optimizer nie powinien robic jednego greedy pass. Ma tworzyc wiele legalnych kandydatow:

- rozne sortowania;
- rozne anchory;
- stock 0/90 deg, jezeli rotacja stocku jest dozwolona;
- mieszane orientacje formatek;
- rozne proporcje orientacji w jednej grupie;
- waste-first refill;
- repair pass dla drobnicy;
- finalna walidacja gilotynowa.

Kandydat wygrywa wedlug scoringu, nie dlatego, ze zostal znaleziony pierwszy.

## 8. Kerf / rzaz - zasady nie do zlamania

Kerf jest realnym wymiarem geometrycznym.

Musi byc liczony:

- miedzy formatkami w rzedzie;
- miedzy rzedami;
- miedzy kolumnami/pasami;
- przy sprawdzaniu, czy formatka miesci sie w regionie;
- przy dzieleniu wolnych prostokatow;
- przy `usedBoundingBox` i scoringu;
- przy cutTree/walidacji;
- przy raporcie/rysunku, jesli wplywa na geometrie.

Poprawny wzor na zajety wymiar `n` elementow:

```text
n * wymiar + (n - 1) * kerf
```

Nie uzywaj:

```text
n * (wymiar + kerf)
```

bez odjecia ostatniego kerfu.

W kodzie patrz na:

- `_span_size(count, size, kerf)` w `two_d_vertical_segmented.py`;
- `_split_segment_rects(...)`;
- `_try_place_in_segment(...)`;
- `validate_guillotine_feasibility(...)`.

## 9. Waste-first refill i repair pass

Ostatnie krytyczne zalozenie: male formatki nie moga projektowac glownego ukladu, jezeli moga wejsc w odpady po wiekszych/trudniejszych elementach.

### Waste-first refill

Po ulozeniu trudniejszych formatek optimizer:

1. Zbiera realne regiony odpadowe.
2. Klasyfikuje region:
   - klasa 1: wewnatrz obecnego bounding boxa;
   - klasa 2: bez wydluzania osi oszczedzanej;
   - klasa 3: wydluza os oszczedzana;
   - klasa 4: nowa plyta/fragment.
3. Najpierw probuje klasy 1/2.
4. Dla kazdego fillera sprawdza wszystkie orientacje.
5. Po wlozeniu aktualizuje wolne prostokaty i skanuje dalej.

### Dolny band przez wiele pasow

Wazna poprawka: jezeli kilka waskich pasow ma ten sam dolny poziom ulozonych duzych formatek, odpad pod nimi jest traktowany jako jeden szerszy band. Przyklad:

- 9 kolumn `40x900`;
- kerf `5`;
- szerokosc bloku: `9*40 + 8*5 = 400`;
- pod nimi zostaje band ok. `400 x 595`;
- formatki `100x100` maja wejsc w ten band, nie w osobna kolumne.

Kod: `_collect_internal_filler_regions(...)` tworzy `"bottom band across adjacent strips"`.

### Repair pass

Jezeli kandydat juz ma male elementy w osobnym pasie/kolumnie:

1. `_repair_candidate_fillers(...)` kopiuje layout.
2. Usuwa tymczasowo fillery.
3. Przelicza odpady po wiekszych elementach.
4. Probuje wlozyc fillery przez `_recover_internal_fillers(...)`.
5. Waliduje gilotynowosc.
6. Akceptuje tylko jezeli wynik dominuje poprzedni layout wedlug:
   - mniejszy `_saved_used_length`;
   - mniejszy consumed area;
   - mniejszy bounding box;
   - mniej fragmentacji;
   - wiekszy najwiekszy odpad.

Nie obchodz walidacji. Jezeli naprawiony layout nie przechodzi `validate_guillotine_feasibility`, ma zostac odrzucony.

## 10. Walidacja gilotynowa i cutTree

Plik: `algorithms/guillotine_technology.py`.

Najwazniejsze funkcje:

- `validate_guillotine_feasibility(layout, kerf)`;
- `annotate_guillotine_layout(...)`;
- `annotate_guillotine_result(...)`;
- `_build_cut_data(...)`.

Walidacja sprawdza:

- brak wyjscia poza plyte;
- brak nakladania;
- minimalny kerf w tych samych rzedach/stripach;
- przynaleznosc formatek do segmentow;
- brak przecinania pionowych granic pasow;
- logiczna kolejnosc ciec w segmentach.

Pozytywny wynik ustawia:

- `layout.is_guillotine_feasible = True`;
- `layout.cut_tree`;
- `layout.cut_operations`;
- `layout.waste_rects`;
- `layout.cutting_explanation`.

Nigdy nie wybieraj produkcyjnie ukladu, ktory jest ladny geometrycznie, ale nie przechodzi tej walidacji.

## 11. Scoring i odpady

Plik: `algorithms/layout_scoring.py`.

Glowna funkcja: `score_result(result, kerf, min_reusable_size)`.

Metryki:

- `collect_free_rectangles(...)` - szacowanie odpadow;
- `reusable_rectangles(...)` - filtr po minimalnym uzytecznym rozmiarze;
- `reusable_score(...)` - preferuje duze, prostokatne odpady;
- `manufacturing_score(...)` - premiuje uklady czytelne technologicznie;
- `annotate_result_metrics(...)` - wpisuje metryki w layout/result.

W `two_d_vertical_segmented.py` scoring comfort/sport dodaje wczesniej:

- liczbe nieulozonych formatek;
- liczbe plyt;
- infeasible penalty;
- `_total_saved_used_length`;
- `_total_saved_consumed_area`;
- recovery/rotation metrics.

Wazne: procent wykorzystania nie jest glownym celem. Moze byc dalszym tie-breakerem.

## 12. Brakujace plyty

Logika w `workers/optimizer_worker.py`:

- jezeli po optymalizacji sa `unplaced_sheet_parts`, worker tworzy wirtualne stocki przez `_missing_stock_for(...)`;
- potem ponownie uruchamia ten sam algorytm dla brakujacych formatek;
- wynik trafia do `result.missing_sheet_layouts`;
- stock ma `source="missing"`.

W UI takie plyty sa rysowane jako brakujace/dodatkowe.

Wazna zasada raportowania: opis wymiarow musi zgadzac sie z tym, co jest narysowane. Jezeli rysowany jest pelny arkusz, opis i linie wymiarowe pokazuja pelny arkusz. Jezeli rysowany jest tylko zuzyty fragment, oba elementy pokazuja fragment.

Kod UI:

- `ui/layout_view.py::_display_sheet_geometry(...)`;
- `ui/layout_view.py::_draw_sheet_layouts(...)`.

## 13. UI i raportowanie

Najwazniejszy plik wizualizacji: `ui/layout_view.py`.

Odpowiada za:

- rysowanie plyt;
- rysowanie formatek;
- rysowanie odpadow;
- segment boundaries;
- wymiarowanie;
- legende;
- tryb print/export;
- missing sheets.

Eksport PNG idzie przez ten sam widok, wiec bledy w `layout_view.py` zwykle widac i w UI, i w eksporcie.

Raport PDF:

- `import_export/pdf_report.py`.

Eksport obrazu:

- `import_export/image_export.py`.

## 14. Debug optymalizatora

Ustaw zmienna srodowiskowa:

```powershell
$env:SIEKACZ_DEBUG_CANDIDATES = "1"
```

Wtedy `result.messages` zawiera m.in.:

- liczbe wygenerowanych kandydatow;
- podsumowanie odrzucen przez kolizje, kerf, gilotynowosc;
- informacje o waste-first refill;
- informacje o repair pass;
- trudnosc formatek;
- zwycieski bounding box;
- orientacje formatek w zwyciezcy;
- lokalizacje fillerow;
- liste kandydatow i powodow odrzucenia/dominacji.

Przy szybkim debugowaniu uzyj skryptu inline:

```powershell
@'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
os.environ["SIEKACZ_DEBUG_CANDIDATES"] = "1"

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented, _saved_used_length
from core.models import SheetStock, SheetPart
from collections import Counter

stock = [SheetStock("standard", 1, 3000, 1500, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
parts = [
    SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True),
    SheetPart("B", 100, 100, 3, "standard", 1, allow_rotation=True),
    SheetPart("C", 20, 30, 55, "standard", 1, allow_rotation=True),
    SheetPart("D", 10, 10, 20, "standard", 1, allow_rotation=True),
]

result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, min_reusable_size=80, optimization_mode="comfort")
layout = result.sheet_layouts[0]
print("saved", _saved_used_length(layout), "used", layout.used_width, layout.used_height, "feasible", layout.is_guillotine_feasible)
print(Counter((p.part.name, p.width, p.height, p.rotated) for p in layout.parts))
for message in result.messages:
    if message.startswith("Winner") or "Optimizer debug" in message or "repair pass" in message:
        print(message)
'@ | .\.venv\Scripts\python.exe -
```

## 15. Testy

Testy sa zwyklymi skryptami Python, nie wymagaja pytest.

Najczestsze komendy:

```powershell
.\.venv\Scripts\python.exe tests\test_waste_recovery_optimizer.py
.\.venv\Scripts\python.exe tests\test_final_qa_guardrails.py
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

Pelny zestaw `test_*.py`:

```powershell
$ErrorActionPreference = 'Stop'
$tests = Get-ChildItem -Path tests -Filter 'test_*.py' | Sort-Object Name
foreach ($test in $tests) {
    Write-Output "RUN $($test.Name)"
    .\.venv\Scripts\python.exe $test.FullName
    if ($LASTEXITCODE -ne 0) { throw "Test failed: $($test.Name)" }
}
Write-Output 'ALL TEST SCRIPTS OK'
```

Smoke/stability/fuzz:

```powershell
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

Compile check:

```powershell
.\.venv\Scripts\python.exe -m py_compile algorithms\two_d_vertical_segmented.py algorithms\guillotine_technology.py ui\layout_view.py tests\test_waste_recovery_optimizer.py
```

Testy Qt ustawaja zwykle:

```python
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

Nie usuwaj tego w testach UI.

## 16. Najwazniejsze regresje

### `tests/test_waste_recovery_optimizer.py`

Pilnuje:

- odzysku dolnego pasa przez obrot dlugich formatek;
- drobnicy jako fillerow, ktore nie psuja glownego ukladu;
- braku zwiekszania liczby missing sheets przez drobnice;
- profili strategicznych 1000x2000, 1500x3000, 2050x3050, 1300x1400;
- spojnego rysowania brakujacych plyt;
- kerf-aware splitow 3000x1500;
- waste-first refill przenoszacego `100x100` pod `40x900`.

### `tests/test_final_qa_guardrails.py`

Szeroki zestaw guardraili przed regresjami:

- powtarzalnosc stock orientation;
- missing sheets;
- mixed orientation compaction;
- UI/preview sanity.

### `tests/test_guillotine_technology.py`

Walidacja ciec gilotynowych i cutTree.

### `tests/test_remnant_scoring.py`

Scoring odpadow i preferencja uzytecznych resztek.

### `tests/stability_smoke.py`

Szybkie przypadki, fuzz random inputs i performance do 500 formatek.

Po zmianach w optimizerze zawsze uruchom:

```powershell
.\.venv\Scripts\python.exe tests\test_waste_recovery_optimizer.py
.\.venv\Scripts\python.exe tests\test_final_qa_guardrails.py
.\.venv\Scripts\python.exe tests\test_guillotine_technology.py
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

Przed oddaniem wiekszej zmiany uruchom caly loop `test_*.py` oraz `stability_smoke.py`.

## 17. Zasady bezpiecznej pracy nad optymalizatorem

Nie rob:

- hardcode pod wymiar `40x900`, `10x10`, `100x100` albo konkretny screenshot;
- liczenia layoutu bez kerfu;
- zmiany tylko w podgladzie PNG, jezeli geometria optimizer nadal liczy inaczej;
- wybierania kandydata bez walidacji gilotynowej;
- usuwania `cutTree`, `validate_guillotine_feasibility` lub kar za infeasible;
- wybierania ukladu tylko przez procent wykorzystania;
- tworzenia nowej plyty, jezeli formatki mieszcza sie w realnych odpadach;
- zakladania, ze odpad jest zawsze na dole;
- zakladania, ze plyta zawsze ma 2000x1000 albo 3000x1500.

Rob:

- licz kerf w kazdym regionie i spacingu;
- najpierw ukladaj trudne/ograniczajace formatki;
- drobnice traktuj jako filler;
- po ulozeniu wiekszych elementow zbieraj odpady;
- przed nowym pasem/plyta probuj waste-first refill;
- po naprawie uruchamiaj walidacje gilotynowa;
- scoring traktuj leksykograficznie: plyty, dlugosc, realny material, odpady, dopiero procent;
- dodawaj regresje na zachowanie, nie na jeden screenshot.

## 18. Jak rozpoznawac "trudna" formatke

Formatka jest trudniejsza, gdy:

- ma wymiar bliski strategicznemu wymiarowi plyty;
- ma wysoki stosunek dlugiego boku do krotkiego;
- pasuje w niewielu orientacjach;
- pasuje w niewielu regionach;
- moze determinowac kierunek glownego ciecia.

Formatka jest fillerem, gdy:

- jest bardzo mala wzgledem plyty;
- miesci sie w wielu regionach;
- nie ma wymiaru strategicznie ograniczajacego.

Wazna poprawka: `40x900` nie moze byc fillerem tylko dlatego, ze ma mala powierzchnie. Dlugosc 900 przy plytach 1000/1500 mm jest geometrycznie ograniczajaca.

## 19. Presety strategiczne

W `two_d_vertical_segmented.py::_strategic_dimension`:

- 1000x2000 -> 1000;
- 1500x3000 -> 1500;
- 2050x3050 -> 2050;
- 1300x1400 -> 1300;
- inne -> krotszy bok.

Strategiczny wymiar sluzy do oceny trudnosci i fillerow. Nie mylic go z osia oszczedzana. Osią oszczedzana jest zwykle dlugi bok plyty, czyli w orientacji poziomej X.

## 20. Comfort vs Sport

`optimization_mode`:

- `comfort` - preferuje technologicznie czytelne pasy i stabilny wynik;
- `sport` - agresywniej walczy o mniejsza liczbe plyt/materialu.

Oba tryby musza zachowac gilotynowosc i kerf.

`cutting_mode`:

- `hybrid` / produkcyjny - wybiera kandydatow gilotynowych;
- `free` - moze dopuscic MaxRects/Guillotine fallback jako kandydatow, ale UI powinno ostrzegac, ze taki uklad moze byc trudny lub nierealny produkcyjnie.

## 21. Import, eksport, baza

Import/export:

- `import_export/csv_io.py`;
- `import_export/xlsx_io.py`;
- `import_export/pdf_report.py`;
- `import_export/image_export.py`.

Baza lokalna:

- `database/db.py`;
- `database/repositories.py`;
- `database/schema.sql`.

Log aplikacji:

```text
%USERPROFILE%\.cut_optimizer_desktop\siekacz9000.log
```

## 22. Typowe zadania i gdzie zaczac

### Problem z rozkladem formatek

Zacznij od:

1. `algorithms/two_d_vertical_segmented.py`;
2. `algorithms/guillotine_technology.py`;
3. `algorithms/layout_scoring.py`;
4. test regresyjny w `tests/test_waste_recovery_optimizer.py` albo `tests/test_final_qa_guardrails.py`.

### Problem z opisem wymiarow/PNG

Zacznij od:

1. `ui/layout_view.py`;
2. `import_export/image_export.py`;
3. test UI w `tests/test_waste_recovery_optimizer.py` lub `tests/test_remnant_scoring.py`.

### Problem z missing sheets

Zacznij od:

1. `workers/optimizer_worker.py::_attach_missing_sheet_layouts`;
2. `workers/optimizer_worker.py::_missing_stock_for`;
3. `ui/layout_view.py::_display_sheet_geometry`.

### Problem z ustawieniami UI

Zacznij od:

1. `app/simple_window.py`;
2. `ui/optimizer_panel.py`;
3. `database/repositories.py`.

### Problem z buildem

Zacznij od:

1. `scripts/BUILD_EXE.bat`;
2. `SIEKACZ9000.spec`;
3. `scripts/BUILD_INSTALLER.bat`;
4. `installer_src/SIEKACZ9000.iss`.

## 23. Znane pulapki

- `git status` moze nie dzialac, bo katalog nie musi byc repozytorium git.
- Testy sa skryptami Python, nie pytest.
- W PowerShell uzywaj `.\\.venv\\Scripts\\python.exe` albo `.\.venv\Scripts\python.exe`.
- Wiele plikow ma teksty UI po polsku; nie zmieniaj ich masowo przy pracy nad algorytmem.
- Nie edytuj artefaktow `build/`, `dist/`, chyba ze pracujesz nad dystrybucja.
- `SheetLayout.consumed_area` uzywa `used_width * stock.height`, co jest celowe dla odcinania fragmentu po dlugosci plyty.
- Brakujace plyty moga byc rysowane jako zuzyty fragment albo pelny arkusz; opis i linie wymiarowe musza byc spojne.
- `validate_guillotine_feasibility` dopuszcza wiersze o nakladajacych sie zakresach Y tylko wtedy, gdy ich zakresy X sa rozdzielone co najmniej kerfem.
- Repair pass moze poprawic layout bez zmiany `usedLength`, jezeli zwieksza jakosc odpadu albo zmniejsza bounding area; dlatego testuj metryki, nie tylko jeden wymiar.

## 24. Minimalny checklist przed oddaniem zmian

1. Czy kerf jest liczony w geometrii, nie tylko w rysunku?
2. Czy layout przechodzi `validate_guillotine_feasibility`?
3. Czy nie dodano hardcode'u pod konkretne wymiary?
4. Czy drobnica nie wydluza plyty, jezeli miesci sie w odpadzie?
5. Czy scoring nadal preferuje mniej plyt i mniejsza realna dlugosc?
6. Czy raport/PNG pokazuje wymiar zgodny z tym, co narysowano?
7. Czy dodano regresje na zachowanie?
8. Czy przeszly testy `test_*.py` i `stability_smoke.py`?

## 25. Ostatnio zweryfikowane komendy

Na stanie z 2026-05-23 przechodzily:

```powershell
.\.venv\Scripts\python.exe tests\test_waste_recovery_optimizer.py
```

```powershell
$ErrorActionPreference = 'Stop'
$tests = Get-ChildItem -Path tests -Filter 'test_*.py' | Sort-Object Name
foreach ($test in $tests) {
    Write-Output "RUN $($test.Name)"
    .\.venv\Scripts\python.exe $test.FullName
    if ($LASTEXITCODE -ne 0) { throw "Test failed: $($test.Name)" }
}
Write-Output 'ALL TEST SCRIPTS OK'
```

```powershell
.\.venv\Scripts\python.exe tests\stability_smoke.py
```

```powershell
.\.venv\Scripts\python.exe -m py_compile algorithms\two_d_vertical_segmented.py algorithms\guillotine_technology.py ui\layout_view.py tests\test_waste_recovery_optimizer.py
```

## 26. Krotka mentalna mapa dla Claude Code

Jezeli masz naprawic optimizer:

1. Przeczytaj `core/models.py`, zeby wiedziec, czym jest `SheetLayout`.
2. Przeczytaj `workers/optimizer_worker.py`, zeby wiedziec, jaki kerf i stock faktycznie trafiaja do algorytmu.
3. Pracuj glownie w `algorithms/two_d_vertical_segmented.py`.
4. Po kazdej zmianie odpal `annotate_guillotine_result` albo pozwol glownej funkcji to zrobic.
5. Nie ufaj layoutowi, dopoki `layout.is_guillotine_feasible` nie jest `True`.
6. Dodaj test w `tests/test_waste_recovery_optimizer.py`.
7. Uruchom pelny loop testow.

Najkrotsze podsumowanie filozofii:

```text
Najpierw elementy ograniczajace.
Potem odpady.
Potem drobnica.
Potem kompresja/repair.
Na koncu scoring globalny.
Nigdy bez kerfu.
Nigdy bez gilotynowosci.
```
