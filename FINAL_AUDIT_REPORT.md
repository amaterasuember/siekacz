# SIEKACZ 9000 — FINAL AUDIT REPORT

Data audytu: 2026-08-16  
Zakres: aktualny kod źródłowy w katalogu projektu. Nie zmieniano kodu produkcyjnego w ramach tego audytu.

## Stan po remediacji — 2026-08-17

**Status: naprawy AUD-001–AUD-007 zostały wdrożone i zweryfikowane.**

Najważniejsze zmiany:

- kompresja układu nie może już usuwać poprawnie rozmieszczonych formatek;
- wirtualna „brakująca płyta” powstaje wyłącznie z rzeczywistego, dostępnego profilu, a profil pełnej płyty dominuje mniejszy odpad tylko wtedy, gdy mieści co najmniej ten sam zbiór formatek;
- dla małych zleceń, gdy wszystkie heurystyki zawodzą, silnik uruchamia ograniczone przeszukiwanie legalnych cięć gilotynowych;
- uszkodzona historia projektów jest kopiowana do pliku diagnostycznego i blokuje zapis zamiast zostać nadpisana;
- wszystkie moduły testowe są wykonywane przez standardowy runner.

### Końcowa weryfikacja

| Kontrola | Wynik |
| --- | --- |
| `scripts/RUN_TESTS.bat` (smoke + pełna pętla `test_*.py`) | PASS |
| Kompilacja całego kodu i testów (`compileall`) | PASS |
| Deterministyczne granice | 288/288 PASS |
| Regresje AUD-001–AUD-004 | 4/4 PASS |
| Brute-force dla małych układów | 995 porównań, 0 pominiętych wykonalnych układów |
| Metamorphic ×2 / permutacje | 1 000 / 250, 0 regresji |
| Fuzz silnika | 10 000/10 000, 0 naruszeń inwariantów |
| Czas pełnego harnessu | 973,422 s |

Nie jest to dowód matematyczny dla każdej możliwej kombinacji wymiarów i danych handlowych. W granicach opisanej macierzy testów nie pozostał potwierdzony błąd blokujący, krytyczny ani major z tego audytu.

## Historyczny wynik przed naprawą

Poniższe sekcje dokumentują źródła usterek wykrytych przed remediacją; opisy „błędnego wyniku obecnego” są historyczne. Nie opisują aktualnego stanu kodu.

**Gotowa do produkcji w stanie sprzed napraw: NO.**

Stan projektu jest nierówny: aplikacja ma szerokie testy regresji, działające ścieżki UI/raportu/Excela i poprawnie przechodzi większość sprawdzonych inwariantów geometrii, ale **nie jest gotowa do bezpiecznego użycia produkcyjnego** przed naprawą błędów AUD-001–AUD-005.

## Overall confidence

**58/100.** Ocena oznacza zaufanie do sprawdzonych zachowań technicznych, nie prawdopodobieństwo „braku każdego błędu”. Nie wykonano i nie da się praktycznie wykonać exhaustive search całej przestrzeni wymiarów, ilości, materiałów i decyzji heurystycznych. Wykonano exhaustive/brute-force tylko dla małej dyskretnej domeny oraz szeroki fuzz/property/metamorphic testing.

## Test statistics

| Rodzaj | Wykonano | Wynik |
| --- | ---: | --- |
| Istniejące pliki `test_*.py` | 58 uruchomień | bez błędu procesu; 3 wymagały osobnego wywołania funkcji |
| Smoke/stability | 1 | PASS |
| Ręcznie wywołane funkcje z modułów bez entry pointu | 6 | PASS |
| UI/PDF/historia/katalog/Excel — ponowne smoke | 5 modułów | PASS |
| Kompilacja kodu produkcyjnego | 1 | PASS |
| Deterministyczne granice ±1 | 288 | PASS |
| Jawne regresje | 4 | FAIL — oczekiwane, potwierdzają błędy |
| Fuzz silnika, boundary-biased | 10 000 | 1 FAIL ilościowy |
| Niezależny brute-force, mała domena 5–10 | 995 porównań (5 limit node budget) | 3 FAIL wykonalności |
| Metamorphic: skala ×2 | 1 000 | PASS |
| Permutacje wejścia | 250 seedów | PASS |

Pokryto presetowe płyty `1000×2000`, `2050×3050`, `1500×3000`, `1300×1400`, płyty własne, tryby COMFORT/SPORT, obrót dozwolony/niedozwolony, kerf `0/1/3/5/8/25`, dopasowanie równe oraz ±1, małe i skrajnie wydłużone formatki, ilości do 3 w fuzz oraz większe serie w istniejących stress testach.

## Podsumowanie

Znaleziono 7 problemów:

| Priorytet | Liczba | Najważniejsze ryzyko |
| --- | ---: | --- |
| Blokujący | 1 | Wynik optymalizatora może zgubić zamówione formatki. |
| Krytyczny | 1 | Backend może stworzyć fikcyjny, niedostępny format płyty. |
| Major | 4 | Trzy wykonalne plany są pomijane; historia może zostać utracona. |
| Minor | 1 | Trzy testy nie uruchamiają się w standardowej pętli plików. |
| Cosmetic | 0 | Brak potwierdzonych usterek czysto wizualnych w objętym zakresie. |

Największe ryzyka to nie geometria kolizji (dla 10 000 przypadków fuzz nie wykryto wyjścia poza płytę, nakładania ani błędnej szczeliny rzazu), lecz **zachowanie kompletności danych wejściowych** i **prawdziwość informacji o dostępnych płytach**.

## Zakres audytu

Sprawdzono:

- architekturę aplikacji Qt/PySide6: `main.py`, główne okno, modele, worker, widoki, import/eksport, historię i bazę;
- główny przepływ płyt: `Project` → `OptimizerWorker` → kandydaci algorytmów → walidacja gilotynowa → podgląd/PDF;
- główny optymalizator `algorithms/two_d_vertical_segmented.py`, scoring, walidację gilotynową i tworzenie brakujących płyt;
- pełną pętlę 58 plików `tests/test_*.py`, `tests/stability_smoke.py` oraz dodatkowo trzy moduły testowe bez własnego entry pointu;
- kompilację całego kodu produkcyjnego przez `python -m compileall`;
- UI, PDF, historię, katalog materiałów i arkusz masowego importu przez osobne smoke testy;
- niezależny harness `tests/final_audit_harness.py`: 288 przypadków granicznych, 10 000 przypadków fuzz silnika, 1 000 porównań z niezależnym brute-force dla małych płyt, 1 000 testów skalowania ×2 i 250 testów permutacji wejścia.

Nie zweryfikowano ręcznie na wszystkich wspieranych systemach instalatorów, aktualizatora sieciowego, realnego sprzętu piły ani wydajności na rzeczywistych BOM-ach klientów. Projekt nie posiada endpointów HTTP — jest aplikacją desktopową, więc audyt „endpointów” dotyczył funkcji publicznych workerów/importu/eksportu.

## Mapa projektu i przepływ danych

| Obszar | Odpowiedzialność | Najważniejsze wejścia/wyjścia |
| --- | --- | --- |
| `main.py`, `app/simple_window.py` | Start Qt, formularze projektu, walidacja UI, postęp | dane tabel → `Project` |
| `core/models.py`, `core/validation.py` | Model domenowy i walidacja danych | `SheetStock`, `SheetPart`, `SheetLayout`, `OptimizationResult` |
| `workers/optimizer_worker.py` | Walidacja, efektywny rzaz, dispatch, brakujące płyty | `Project` → `OptimizationResult` |
| `algorithms/two_d_vertical_segmented.py` | Główny planner produkcyjny | stock + formatki → kandydaci/gilotyna |
| `algorithms/guillotine_technology.py` | Sprawdzenie technologii i drzew cięć | `SheetLayout` → `cut_tree`, operacje |
| `algorithms/layout_scoring.py` | Odpady, scoring, metryki | layouty → wynik/scoring |
| `ui/layout_view.py`, `import_export/pdf_report.py` | Podgląd i raporty | wynik → ekran/PNG/PDF |
| `import_export/xlsx_io.py`, batch workbook | Excel i masowy import | arkusz ↔ modele |
| `app/project_history.py` | Lokalna historia JSON | projekt/wynik ↔ plik użytkownika |

## Wyniki kontroli

- Pełna pętla istniejących testów oraz `stability_smoke.py`: bez zgłoszonego błędu po ręcznym uruchomieniu trzech modułów bez `__main__`.
- `compileall`: poprawny.
- Smoke: `test_ui_apply_result.py`, `test_pdf_report_layout.py`, `test_project_history_locking.py`, `test_material_catalog_sync.py`, `test_batch_workbook.py`: poprawne.
- Niezależny harness: 288/288 przypadków granicznych bez błędu; fuzz 10 000 przypadków wykrył 1 naruszenie kompletności ilości; porównanie brute-force wykryło 3 niepełne ułożenia mimo istnienia legalnego planu; testy skali i kolejności wejścia nie wykryły regresji. Pełny przebieg trwał 757,497 s.

## Usterki wykryte przed naprawą

### AUD-004 — kompresja może usuwać formatki z wyniku

- Priorytet: **Blokujący**
- Lokalizacja: `algorithms/two_d_vertical_segmented.py`, `_compress_layout`, linie 783–789; wywołanie z `_build_result`, linie 3948–3952.
- Problem: `_compress_layout` dodaje do `rebuilt_parts` wyłącznie segmenty, dla których `_rebuild_segment(...)` zwróci `True`. Gdy ponowne ułożenie segmentu się nie powiedzie, poprzednie, poprawne rozmieszczenie tego segmentu nie jest zachowywane. Końcowy assignment `layout.parts = ...rebuilt_parts` usuwa jego formatki.
- Faktyczny efekt: część zamówionych formatek znika jednocześnie z `layout.parts` i `unplaced_sheet_parts`; worker nie może ich przenieść na brakującą płytę.
- Oczekiwany efekt: każda instancja wejściowa musi być dokładnie raz: ułożona albo jawnie nieułożona.
- Odtworzenie: `AUD-004` w `tests/final_audit_harness.py` — płyta `2000×2000`, rzaz `1`, bez obrotu; `P0 1000×1000 ×3`, `P1 1×666 ×2`, `P2 2000×2001 ×2`, `P3 722×624 ×3`.
- Dowód: niezależne liczenie ilości w pełnym fuzz (seed `10006414`) wykazało, że końcowy wynik ma `P0×2`, `P2×2`, `P3×2`, natomiast brakuje `P1×2` i `P3×1`. Potwierdzone także przez worker end-to-end.
- Proponowane rozwiązanie: przy nieudanym rebuildzie zachować pierwotne `segment.parts`; po każdej transformacji uruchamiać centralny audyt zachowania ilości. Nie usuwać segmentu w celu „kosmetycznej” kompresji.
- Ryzyko/zakres: wysoki wpływ na każdy wynik wielopasowy; niewielka do średniej zmiana algorytmu, obowiązkowo z regresją ilościową.

### AUD-005 — worker tworzy niedostępny format brakującej płyty

- Priorytet: **Krytyczny**
- Lokalizacja: `workers/optimizer_worker.py`, `_missing_stock_for`, linie 315–392, zwłaszcza fallback 321 oraz wybór 362–370.
- Problem: jeśli żadna dostępna płyta nie mieści części, worker wybiera jeden z hard-code'owanych profili `1000×2000`, `1500×3000`, `2050×3050`, nawet jeżeli taki format nie występuje w katalogu ani danych klienta.
- Faktyczny efekt: dla jedynej dostępnej płyty `1000×1000` i formatki `1500×200` funkcja tworzy brakującą płytę `1000×2000`, a worker raportuje finalnie rozkrój na `2000×1000`, bez pozycji „nieułożone”.
- Oczekiwany efekt: backend może użyć tylko realnie dopuszczonego profilu; przy braku takiego profilu powinien jawnie zwrócić brak możliwości obliczenia/zakupu, a nie fikcyjną płytę.
- Odtworzenie: `run_virtual_stock_probe()` w `tests/final_audit_harness.py`.
- Dowód: wynik probe: `input_stock=[(1000,1000)]`, `missing_layout_stock=[(2000,1000)]`, `unplaced=[]`.
- Proponowane rozwiązanie: usunąć fallback profile z backendowej ścieżki produkcyjnej; generować „missing” wyłącznie przez kopiowanie zatwierdzonych profili katalogu/danych lub zwrócić czytelny błąd.
- Ryzyko/zakres: wysokie ryzyko błędnej wyceny i błędnej produkcji. Zmiana średnia; należy testować import, ręczne wpisywanie i tryb inteligentny.

### AUD-001 — SPORT nie znajduje kompletnego układu 6×5

- Priorytet: **Major**
- Lokalizacja: `algorithms/two_d_vertical_segmented.py`, generowanie/wybór kandydatów 3978–4328; kandydaci pasów pionowych 2726+.
- Problem: dla legalnego, gilotynowego układu heurystyka kończy z nieułożoną częścią.
- Faktyczny efekt: płyta `6×5`, rzaz `0`, bez obrotu; `P0 1×1 ×2`, `P1 5×2 ×1`, `P2 3×2 ×2`; tryb SPORT pozostawia `P1` nieułożoną.
- Oczekiwany efekt: wszystkie części na jednej płycie — niezależny oracle znalazł legalny układ.
- Odtworzenie/dowód: seed `80057`, `AUD-001` i brute-force w harnessie.
- Proponowane rozwiązanie: dodać kandydaty poziomych bandów/pełnego slicing-tree dla małych problemów albo nie odrzucać kandydata, który ma mniej nieułożonych części. Testować przez oracle, nie obrazek.
- Ryzyko/zakres: średnia zmiana heurystyki, duże znaczenie dla poprawności wyniku.

### AUD-002 — COMFORT nie znajduje kompletnego układu 6×8

- Priorytet: **Major**
- Lokalizacja: jak AUD-001.
- Problem/faktyczny efekt: dla `P0 3×6 ×2`, `P1 5×1 ×1`, płyta `6×8`, rzaz `0`, bez obrotu, COMFORT pozostawia `P1` nieułożoną mimo układu z dwoma pasami `3×6` i poziomym pasem `5×1`.
- Odtworzenie/dowód: seed `80789`, `AUD-002`; niezależny brute-force potwierdza wykonalność.
- Rozwiązanie/ryzyko: jak AUD-001.

### AUD-003 — COMFORT gubi wykonalność przy rzazie

- Priorytet: **Major**
- Lokalizacja: jak AUD-001.
- Problem/faktyczny efekt: płyta `9×10`, rzaz `3`, bez obrotu; `P0 4×2 ×1`, `P1 3×3 ×2`. Oracle znajduje legalny układ, a optimizer pozostawia jedną `P1`.
- Odtworzenie/dowód: seed `80964`, `AUD-003`; wykonalność potwierdzona przez niezależny slicing oracle.
- Rozwiązanie/ryzyko: jak AUD-001, z obowiązkowym zachowaniem rzazu w alternatywnych bandach.

### AUD-006 — uszkodzona historia jest cicho kasowana przy następnym zapisie

- Priorytet: **Major**
- Lokalizacja: `app/project_history.py`, `_read_file` 74–83, `save_project_record` 115–133.
- Problem: błąd JSON/OSError w odczycie jest zamieniany na pustą listę. Następny zapis nadpisuje historię nową listą, bez komunikatu i bez kopii uszkodzonego pliku.
- Faktyczny efekt: zapisany projekt `survivor` po celowym uszkodzeniu JSON znika; po następnym zapisie pozostaje tylko `new`.
- Oczekiwany efekt: zachować kopię `.corrupt`, zgłosić błąd i zablokować nadpisanie do decyzji użytkownika.
- Odtworzenie/dowód: `run_history_corruption_probe()`; `original_record_preserved=False`.
- Proponowane rozwiązanie: rozróżnić „brak pliku” od „plik uszkodzony”; zapisać kopię, wyświetlić błąd i nie wykonywać automatycznego zapisu nad uszkodzonym źródłem.
- Ryzyko/zakres: średnie ryzyko utraty historii, mała zmiana kodu + test pliku uszkodzonego.

### AUD-007 — trzy moduły testowe nie uruchamiają się z pętli plików

- Priorytet: **Minor**
- Lokalizacja: `tests/test_comfort_sport_modes.py`, `tests/test_guillotine_technology.py`, `tests/test_remnant_scoring.py`.
- Problem: moduły definiują funkcje `test_*`, ale nie mają bloku `if __name__ == "__main__"`; projekt używa skryptowej pętli `python test_*.py`, a nie pytest.
- Faktyczny efekt: standardowy loop importuje te pliki, lecz nie wykonuje asercji. Ręczne uruchomienie wszystkich 6 funkcji potwierdziło obecnie sukces, ale CI/lokalny skrypt może przeoczyć regresję.
- Proponowane rozwiązanie: przejść na pytest albo wymusić wspólny runner odnajdujący funkcje `test_*`; dodać to do skryptu `RUN_TESTS`.
- Ryzyko/zakres: małe, ale bezpośrednio wpływa na zaufanie do zielonego test suite.

## Problemy wydajnościowe

### Potwierdzone obserwacje

- Kompletny niezależny przebieg `10 000 + 1 000 + 1 000 + 250` trwał 757,497 s. To jest miara audytowego obciążenia, **nie** dowód przekroczenia czasu dla normalnego zlecenia klienta.
- Algorytm generuje duży zbiór kandydatów, a następnie wykonuje walidację/metyki/pruning dla każdego (`two_d_vertical_segmented.py` 3978–4249). To stanowi dominującą pracę w wymagającym fuzz, choć nie zebrano profilu CPU per funkcja.

### Hipotezy wymagające profilowania

- `prune_dominated_candidates` porównuje pary kandydatów zagnieżdżoną pętlą (`algorithms/candidate_pruning.py` 122–131), więc rośnie kwadratowo przed limitem 96 kandydatów.
- Wielokrotne `annotate_guillotine_result` oraz ponowne budowanie metryk po recovery/repair mogą być kosztowne przy tysiącach formatek.
- Przed zmianą wydajności trzeba zebrać `cProfile` na rzeczywistych BOM-ach: liczba kandydatów, czas generowania, walidacji, pruning i renderingu osobno. Nie rekomenduję skracania strategii „na ślepo”, bo zwiększyłoby ryzyko AUD-001–003.

## Braki w testach i jakości

- Brak globalnego inwariantu: każda zamówiona instancja musi wystąpić dokładnie raz jako placement albo unplaced. AUD-004 pokazuje, że walidacja gilotynowa nie wystarcza.
- Brak testu backendowej polityki dostępnych profili; UI blokuje część przypadków, lecz worker nadal może wytworzyć format nieistniejący.
- Brak oracle dla małych układów w standardowym suite. Harness wykrył trzy problemy, których ręcznie dobrane testy nie wykryły.
- Brak bezpiecznej procedury odzyskania historii po uszkodzeniu pliku.
- Test runner jest mieszany: część testów jest skryptowa, część ma styl pytest, co ukrywa brak wykonania.
- Nie oceniano kompatybilności rzeczywistych instalatorów Windows Server/macOS/Linux ani pełnej ścieżki publikacji/aktualizacji.

## Plan naprawy

1. **Szybkie naprawy wysokiego wpływu: AUD-004 oraz AUD-005.**  
   Zależności: brak zewnętrznych. Ryzyko: zmiana wyniku istniejących projektów. Weryfikacja: wszystkie cztery jawne regresje z harnessu, pełny suite, a także worker end-to-end z katalogiem materiałów.

2. **Naprawa kompletności kandydatów i heurystyki: AUD-001–003.**  
   Zależności: po AUD-004, ponieważ najpierw wynik musi zachowywać ilość. Ryzyko: wzrost czasu obliczeń. Weryfikacja: trzy oraclowe układy, 1 000 małych porównań brute-force, zachowanie rzazu i gilotynowości.

3. **Bezpieczeństwo danych historii: AUD-006.**  
   Zależności: decyzja UX o komunikacie i lokalizacji backupu. Ryzyko: nie nadpisywać historii automatycznie. Weryfikacja: test uszkodzonego JSON, brak utraty danych, możliwość ręcznego odzyskania.

4. **Test runner i obserwowalność: AUD-007.**  
   Zależności: wybór pytest albo wspólnego runnera. Weryfikacja: CI musi raportować liczbę faktycznie wykonanych testów; dodanie inwariantu ilości do każdej optymalizacji testowej.

5. **Profilowanie i dopiero potem optymalizacja.**  
   Zależności: poprawna kompletność/wykonalność. Ryzyko: obcięcie strategii może pogorszyć jakość. Weryfikacja: benchmarki na zapisanych BOM-ach, p95 czasu i porównanie liczby płyt/odpadu przed i po.

## Reprodukcja

```powershell
.\.venv\Scripts\python.exe tests\final_audit_harness.py --engine-fuzz 10000 --brute-force 1000 --metamorphic 1000 --permutations 250
```

Artefakt wyniku: `outputs/final_audit_harness_results.json`. Deterministyczne przypadki opisano w `SIEKACZ_REGRESSION_CASES.md`.

## Test matrix

| Obszar | Testowany | Wynik | Uwagi |
| --- | --- | --- | --- |
| Bounds / NaN / Infinity / wymiary dodatnie | TAK | PASS | niezależny checker, fuzz 10 000 |
| Overlap | TAK | PASS | niezależne porównania każdej pary placementów |
| Kerf i kumulacja | TAK | PASS w zbadanym zakresie | kerf 0/1/3/5/8/25, granice ±1 |
| Ilości | TAK | **FAIL** | AUD-004: część pozycji znika |
| Rotacja | TAK | PASS w zbadanym zakresie | granice i fuzz, obrót tak/nie |
| Dokładne dopasowanie i ±1 | TAK | PASS | 288 przypadków presetowych |
| COMFORT | TAK | **FAIL** | AUD-002, AUD-003 — niepełne pakowanie |
| SPORT | TAK | **FAIL** | AUD-001 — niepełne pakowanie |
| Presety | TAK | PASS dla granic | nie usuwa ryzyka heurystyk mieszanych |
| Płyty własne | TAK | PASS geometrycznie | fuzz z nietypowymi wymiarami |
| Permutacje danych | TAK | PASS | 250 seedów; porównano wynik agregowany |
| Determinizm | CZĘŚCIOWO | PASS | powtarzalne seedy/permute; brak exhaustive wszystkich ścieżek równoległych |
| Brute-force | TAK | **FAIL** | 3/995 wykonalnych problemów przegranych przez heurystykę |
| Metamorphic ×2 | TAK | PASS | 1 000 przypadków |
| UI ↔ algorithm | TAK | PASS smoke | widok, braki płyt, bandy i PDF preview; nie pixel-perfect diff |
| PNG/PDF eksport | TAK | PASS smoke | testy eksportu/PDF; brak pełnego porównania pikselowego |
| Excel/katalog | TAK | PASS smoke | import masowy, zaawansowane pola, katalog i ceny |
| Historia/stan | TAK | **FAIL** | blokada współbieżności PASS, korupcja pliku AUD-006 FAIL |
| Fallback brakujących płyt | TAK | **FAIL** | AUD-005 |

## Algorithmic concerns

- Główny algorytm ma szeroką rodzinę kandydatów, ale nie jest solverem kompletnym. AUD-001–003 pokazują, że „dużo strategii” nie gwarantuje znalezienia prostego legalnego slicing layoutu.
- COMFORT filtruje pulę kandydatów pod kątem „czystych pasów” (`two_d_vertical_segmented.py` 4269–4313). To jest uzasadniona preferencja produkcyjna, lecz po naprawie trzeba utrzymać twardą zasadę: preferencja nie może pokonać kompletności ułożenia.
- Obecny scoring odróżnia filler od części strukturalnej. Jest to użyteczne dla odpadu, ale każda transformacja filler/recovery/repair musi podlegać inwariantowi ilości — AUD-004 pokazuje, że samo liczenie `unplaced` w scorerze nie chroni przed utratą obiektów.

## UX inconsistencies

Nie potwierdzono nowej usterki czysto wizualnej w automatycznych smoke testach. Potwierdzono jednak dwie niespójności użytkowe o większym znaczeniu niż wygląd:

- backend może pokazać poprawny wizualnie rozkrój na płycie, której użytkownik nigdy nie udostępnił (AUD-005);
- podgląd/raport może nie ostrzec, że formatki zostały utracone wcześniej w pipeline algorytmu (AUD-004).

## Fuzz testing results

Fuzz generował wymiary blisko krawędzi płyty, połowy, jednej trzeciej, wartości ±1 oraz ekstremalne paski. Na 10 000 przypadków nie znaleziono overlapu, wyjścia poza płytę, NaN/Infinity ani błędów odstępu rzazu. Jeden przypadek ujawnił AUD-004. Każdy przypadek ma deterministyczny seed; minimalny przypadek zapisano jako AUD-004.

## Property-based testing results

- granice dostępności: PASS dla 288 wariantów z wymiarem formatki `-1 / = / +1`;
- zachowanie ilości: FAIL — AUD-004;
- skala geometryczna ×2: PASS dla 1 000 przypadków;
- permutacja kolejności wejścia: PASS dla 250 seedów dla metryk agregowanych;
- brak overlapu/bounds/kerf: PASS dla zbadanego fuzz.

## Brute-force comparison results

Niezależny solver rekursywny sprawdza małe płyty `5..10` z całkowitymi wymiarami, rzazem i rekursyjnym dowodem cięć gilotynowych. Z 1 000 seedów 995 mieściło się w założonym limicie węzłów; w 3 przypadkach solver znalazł pełny układ, którego SIEKACZ nie znalazł. Te trzy przypadki są AUD-001, AUD-002 i AUD-003.

## Remaining risks

- Przestrzeń dużych projektów i kombinacji materiałów jest dużo większa niż wykonany fuzz; nie daje to dowodu pełnej poprawności.
- Brak profilu realnych BOM-ów; nie można jeszcze podać p95 dla produkcji.
- Nie przeprowadzono manualnego testu wszystkich sekwencji drag/drop/undo/cancel w GUI ani instalatorów na docelowych systemach.
- Brute-force używa małej domeny dyskretnej i ograniczonego budżetu; nie jest globalnym certyfikatem optymalności dla dużych zleceń.

## Final verdict

**NO — obecna wersja nie powinna zostać wydana jako stabilna produkcyjnie.**

Warunek minimalny wydania: naprawić AUD-004 i AUD-005, następnie doprowadzić AUD-001–003 do przejścia i ponowić pełny harness oraz cały suite. Po tych naprawach nadal należy raportować ograniczenie heurystycznego optimum, zamiast twierdzić, że algorytm jest w 100% bezbłędny.
