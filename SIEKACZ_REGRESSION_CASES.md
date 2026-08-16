# SIEKACZ — naprawione przypadki regresyjne z audytu

Ten plik jest kontraktem napraw. Żaden z poniższych przypadków nie może zostać zamknięty wyłącznie przez zmianę widoku lub komunikatu — należy sprawdzić wynik silnika i worker end-to-end.

Stan po remediacji z 2026-08-17: **AUD-001–AUD-006 przechodzą**, a runner obejmuje także AUD-007. Pełny harness: 288 granic, 995 porównań brute-force, 1 000 testów metamorphic, 250 permutacji i 10 000 przypadków fuzz — bez naruszenia inwariantu/oracle.

## Uruchomienie

```powershell
.\.venv\Scripts\python.exe tests\final_audit_harness.py --engine-fuzz 0 --brute-force 0 --metamorphic 0 --permutations 0
```

Polecenie ma zwrócić `0` oraz `still failing=0`; każda inna wartość oznacza regresję.

## AUD-001 — SPORT: legalna płyta 6×5

| Pole | Wartość |
| --- | --- |
| Płyta | `6 × 5`, ilość `1` |
| Rzaz / margines | `0 / 0` |
| Obrót | niedozwolony |
| Tryb | `sport` |
| Formatki | `P0 1×1 ×2`, `P1 5×2 ×1`, `P2 3×2 ×2` |
| Oczekiwany wynik | wszystkie 5 formatek na jednej płycie, gilotynowo |
| Błędny wynik obecny | `P1 5×2` pozostaje unplaced |
| Seed | `80057` |

## AUD-002 — COMFORT: legalna płyta 6×8

| Pole | Wartość |
| --- | --- |
| Płyta | `6 × 8`, ilość `1` |
| Rzaz / margines | `0 / 0` |
| Obrót | niedozwolony |
| Tryb | `comfort` |
| Formatki | `P0 3×6 ×2`, `P1 5×1 ×1` |
| Oczekiwany wynik | komplet na jednej płycie |
| Błędny wynik obecny | `P1 5×1` pozostaje unplaced |
| Seed | `80789` |

## AUD-003 — COMFORT: zachowanie rzazu

| Pole | Wartość |
| --- | --- |
| Płyta | `9 × 10`, ilość `1` |
| Rzaz / margines | `3 / 0` |
| Obrót | niedozwolony |
| Tryb | `comfort` |
| Formatki | `P0 4×2 ×1`, `P1 3×3 ×2` |
| Oczekiwany wynik | komplet na jednej płycie z odstępem rzazu 3 |
| Błędny wynik obecny | jedna `P1 3×3` pozostaje unplaced |
| Seed | `80964` |

## AUD-004 — kompresja nie może zmienić ilości wejściowych

| Pole | Wartość |
| --- | --- |
| Płyta | `2000 × 2000`, ilość `1` |
| Rzaz / margines | `1 / 0` |
| Obrót / tryb | niedozwolony / `sport` |
| Formatki | `P0 1000×1000 ×3`, `P1 1×666 ×2`, `P2 2000×2001 ×2`, `P3 722×624 ×3` |
| Oczekiwany wynik | ilość każdej pozycji = placements + unplaced; `P2` może pozostać unplaced, ale nic nie może zniknąć |
| Błędny wynik obecny | wynik pomija `P1×2` oraz `P3×1` |
| Seed | `10006414` |
| Przyczyna | `_compress_layout` odrzuca zawartość segmentu, gdy `_rebuild_segment` zwróci `False` |

## AUD-005 — brakująca płyta może mieć wyłącznie istniejący profil

| Pole | Wartość |
| --- | --- |
| Dostępne płyty | tylko `1000 × 1000` |
| Formatka | `1500 × 200`, obrót dozwolony |
| Oczekiwany wynik | czytelna informacja o braku pasującej płyty albo profil z zatwierdzonego katalogu |
| Błędny wynik obecny | worker tworzy wirtualne `1000 × 2000`, potem raportuje `2000 × 1000` |
| Przyczyna | fallback profiles w `_missing_stock_for` |

## AUD-006 — uszkodzona historia

| Pole | Wartość |
| --- | --- |
| Warunek | istnieje zapis projektu, potem plik JSON jest nieczytelny |
| Oczekiwany wynik | kopia uszkodzonego pliku + komunikat, bez automatycznego nadpisania |
| Błędny wynik obecny | odczyt daje pustą listę, kolejny zapis zastępuje historię nowym rekordem |

## Obowiązkowy zestaw po naprawie

```powershell
.\.venv\Scripts\python.exe tests\final_audit_harness.py --engine-fuzz 10000 --brute-force 1000 --metamorphic 1000 --permutations 250

$tests = Get-ChildItem tests -Filter 'test_*.py' | Sort-Object Name
foreach ($test in $tests) {
    .\.venv\Scripts\python.exe $test.FullName
    if ($LASTEXITCODE -ne 0) { throw "Test failed: $($test.Name)" }
}
.\.venv\Scripts\python.exe tests\stability_smoke.py
```
