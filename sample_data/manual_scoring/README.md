# Manualne zestawy do oceny scoringu

Te pliki sa gotowymi projektami `.json` do recznych testow w aplikacji.
Otworz projekt, kliknij `Oblicz rozkroj`, obejrzyj podglad i zanotuj, co Ci sie nie podoba.

## Jak testowac

1. Uruchom `START_SIEKACZ.bat`.
2. Otworz jeden z plikow z tego katalogu jako projekt.
3. Policz w trybie `Komfort`.
4. Dla wybranych scenariuszy przelacz na `Sport` i porownaj wynik.
5. Zapisz obserwacje: liczba plyt, zuzyta dlugosc, czytelnosc pasow, wielkosc odpadu, dziwne rotacje, brakujace plyty.

## Scenariusze

| Plik | Co sprawdza | Na co patrzec |
| --- | --- | --- |
| `01_korpusy_18mm_realne.json` | typowe formatki meblowe 18 mm | czy uklad jest czytelny i nie rozciaga niepotrzebnie dlugiego boku |
| `02_fronty_sloje_bez_rotacji.json` | fronty z kierunkiem sloja, rotacja wylaczona | czy algorytm respektuje brak rotacji i nie robi dziwnych odpadow |
| `03_wiele_grubosci_i_materialow.json` | 18 mm, 3 mm i 10 mm w jednym projekcie | czy material/grubosc sa rozdzielane na osobne plyty |
| `04_drobnica_w_odpadach.json` | duze elementy + duzo malych fillerow | czy drobnica wchodzi w odpady zamiast tworzyc osobny pas |
| `05_mieszane_orientacje.json` | te same formatki w dwoch orientacjach | czy mieszanie rotacji skraca realna dlugosc plyty |
| `06_brakujace_plyty.json` | celowo za malo stocku | czy missing sheet jest sensowny i opis wymiarow zgadza sie z rysunkiem |
| `07_duzy_odpad_uzyteczny.json` | preferencja duzego prostokatnego odpadu | czy zostaje jeden ladny odpad zamiast wielu waskich paskow |
| `08_kerf_na_granicy.json` | elementy blisko limitu z rzazem 5 mm | czy kerf jest liczony konsekwentnie i nie ma kolizji |
| `09_sport_vs_komfort.json` | porownanie gestosci i technologii | policz w Komfort i Sport, porownaj czy Sport faktycznie oszczedza material |
| `10_maly_zaklad_mix.json` | zlecenie mieszane: formatki duze, srednie i male | ogolny test "czy ten wynik kupujesz produkcyjnie" |
| `11_arkusz_1000x620_oszczedzaj_620.json` | niestandardowy arkusz 1000x620 | czy algorytm wypelnia 1000 i minimalizuje zuzycie 620 |

Najbardziej przydatne komentarze do dalszego szlifowania scoringu:

- "Wolalbym mniej plyt kosztem gorszego odpadu".
- "Wolalbym dluzsza plyte, ale wiekszy prostokatny odpad".
- "Ten maly element nie powinien projektowac calego ukladu".
- "Tu rotacja wyglada nielogicznie produkcyjnie".
- "Komfort jest zbyt zachowawczy / Sport jest zbyt chaotyczny".
