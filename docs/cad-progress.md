# Postęp modułu SIEKACZ CAD

Ostatnia aktualizacja: 2026-07-18.

## Aktualny kamień milowy

Etap 1 ma działający fundament domenowy, solver więzów, parametryczny szyk ze Scenariuszem A, trwałe nazwane parametry, diagnostykę profili, automatyczne więzy, analityczne łuki kołowe i eliptyczne, natywną mieszaną polilinię line/arc oraz racjonalny B-spline/NURBS, punkt, parametryczny wielokąt foremny, analityczną szczelinę oraz pierwsze operacje zmieniające topologię krzywych. Przepływ pozostaje pionowy: dokument CAD → komendy → solver/analiza → renderer Qt → interakcja użytkownika → zapis i ponowne otwarcie. Nie jest to jeszcze ukończony profesjonalny Sketcher ani moduł 3D.

## Wykonane funkcje

### Fundament domenowy

- `CadDocument` z trwałym UUID, wersją schematu, jednostką wewnętrzną mm, aktywnym szkicem, parametrami i ustawieniami widoku.
- Hierarchiczny `Sketch` z UUID, nazwą techniczną/wyświetlaną, widocznością, blokadą, wejściami/wyjściami, statusem recompute, błędem i diagnostyką.
- Precyzyjne, immutable encje domenowe: `PointEntity`, `LineEntity`, `RectangleEntity`, `CircleEntity`, `ArcEntity`, `EllipseEntity`, `EllipticalArcEntity`, `BSplineEntity`, `PolylineEntity`, `RegularPolygonEntity` i `SlotEntity`.
- Walidacja liczb skończonych, zakresów, niezerowej geometrii, UUID i limitów liczby obiektów.
- Graf zależności szkiców, sortowanie topologiczne, inkrementalne oznaczanie przeliczeń i wykrywanie cykli.

### Komendy i historia

- `AddEntityCommand`, `RemoveEntitiesCommand`, `ReplaceEntityCommand` i `CompositeCommand`.
- `execute`, `undo`, `redo`, `merge_with_previous`, opis operacji i lista zmienionych UUID.
- Transakcje grupujące wiele operacji w jeden krok historii.
- Limitowana historia undo/redo i powiadomienia renderera po zmianie.
- Test 20 mieszanych operacji cofanych i ponawianych bez utraty UUID.

### Solver więzów

- Trwałe `GeometryReference` i `SketchConstraint` z UUID, trybem sterującym/referencyjnym, włączaniem i statusem diagnostycznym.
- Więzy: coincident, point-on-object, horizontal, vertical, parallel, perpendicular, tangent, angle, collinear, concentric, equal length, equal radius, midpoint, długość linii, długość łuku, odległość/odległość X/Y, współrzędna X/Y, promień, średnica i fix.
- Nieliniowy solver iteracyjny z Jacobianem numerycznym i stabilizacją rozwiązania najbliższego bieżącej geometrii.
- Ranga Jacobianu i rzeczywista liczba pozostałych stopni swobody.
- Statusy: niedowiązany, w pełni związany, redundantny, sprzeczny i nierozwiązywalny.
- Identyfikacja dokładnych UUID więzów konfliktowych, redundantnych i wskazujących uszkodzone referencje.
- Więzy referencyjne mierzą geometrię bez odbierania DoF.
- Dodawanie, usuwanie, edycja wartości i włączanie/wyłączanie więzu przechodzi przez komendy undo/redo.
- Drzewo i geometria wyróżniają błędy; pasek stanu pokazuje DoF i stan solvera.

### Parametryczny szyk i scenariusz A

- `SketchPattern` ma własny UUID, źródło, liczbę elementów, wektor kroku, trwałe UUID generowanych encji i więzów.
- Szyk liniowy okręgu generuje prawdziwe okręgi oraz więzy equal-radius i położenia X/Y względem źródła.
- Liczbę elementów i krok X/Y można później zmienić; istniejące UUID są zachowywane, a nadmiarowe elementy są kontrolowanie dodawane/usuwane.
- Utworzenie, edycja i usunięcie szyku są pojedynczymi, odwracalnymi operacjami undo/redo.
- Workspace pokazuje szyki w drzewie, synchronizuje zaznaczenie i pozwala edytować szyk dwuklikiem.
- Użytkownik może związać bieżące wymiary prostokąta, jego środek lub wyśrodkować geometrię w początku układu.
- Automatyczny scenariusz A przechodzi w całości: płytka 180×100, środek (0,0), otwór Ø14 z położeniem, szyk otworów, DoF=0, zmiana szerokości na 200 i ponowne otwarcie bez utraty więzów.

### Profile i naprawa szkicu

- Czysty silnik `cad/profiles.py` analizuje geometrię niekonstrukcyjną niezależnie od Qt i renderera.
- Raport wykrywa otwarte końce, małe szczeliny, dokładne duplikaty, mikroodcinki, nakładanie, samoprzecięcia linii i okręgów oraz nierozmaite punkty rozgałęzienia.
- Zamknięte pętle mają pole ze znakiem, orientację, bounding box i poziom zagnieżdżenia; profil z otworem jest rozpoznawany jako poprawna powierzchnia.
- Tolerancje topologii, łączenia, duplikatów i mikroodcinków są rozdzielone i walidowane zamiast jednej globalnej wartości epsilon.
- „Sprawdź profil” otwiera raport problemów; kliknięcie problemu zaznacza dokładnie powiązane encje w widoku i drzewie.
- Użytkownik osobno zatwierdza dodanie więzów coincident dla małych szczelin, usunięcie duplikatów oraz destrukcyjne usunięcie mikroodcinków.
- Cała naprawa jest jedną komendą undo/redo. Automatyczne parowanie szczelin jest jednoznaczne, a geometria należąca do szyku jest chroniona przed usunięciem.

### Sketcher 2D i renderer

- `QGraphicsScene` nie jest już źródłem prawdy. Elementy Qt są odtwarzane z modelu i nie można ich przesuwać z pominięciem komend.
- Precyzyjna linia podawana przez X/Y początku, długość i kąt.
- Precyzyjny prostokąt podawany przez X/Y lewego dolnego narożnika, szerokość i wysokość.
- Okrąg podawany przez środek i promień, zachowany jako prawdziwy okrąg domenowy.
- Łuk kołowy podawany przez środek, promień, kąt początkowy i rozwarcie; obsługuje kierunek CW/CCW, dokładne końce, środek, długość i bounding box.
- Łuk jest renderowany jako krzywa `QPainterPath`, a nie zapisywany jako zbiór odcinków. Dwuklik otwiera jego precyzyjną edycję, a skrót `A` aktywuje narzędzie łuku.
- `arc_from_three_points` wyznacza jednoznaczny okrąg i kierunek łuku przechodzącego przez trzy wskazane punkty; przypadek współliniowy jest odrzucany czytelnym błędem.
- `PolylineEntity` przechowuje trwałą sekwencję wierzchołków, flagę zamknięcia i jedną wartość DXF bulge dla każdego segmentu. Bulge=0 materializuje `LineEntity`, a wartość niezerowa dokładny `ArcEntity` przez `tan(sweep/4)`; długość i bounding box wynikają z prawdziwych segmentów.
- Segmenty polilinii mają deterministyczne UUID zależne od UUID rodzica i stabilne referencje `segment-N`, a wierzchołki `vertex-N`. Proste segmenty mogą uczestniczyć w więzach H/V/kąta; łuk bulge jest chroniony przed błędnym potraktowaniem jak cięciwa. Coincident, midpoint, point-on-object i DoF działają na rzeczywistej geometrii.
- Snapping polilinii obejmuje wszystkie wierzchołki, analityczne środki łuków, nearest oraz przecięcia z liniami, okręgami, łukami, elipsami i innymi poliliniami.
- Narzędzie polilinii pozostaje aktywne podczas kolejnych kliknięć. Enter lub prawy przycisk zatwierdza otwartą polilinię, klik pierwszego punktu ją zamyka, a Escape anuluje bieżący wieloetapowy gest.
- Narzędzie „Łuk 3P” pokazuje podgląd krzywej po wskazaniu dwóch punktów i automatycznie przechodzi do precyzyjnego zatwierdzenia po trzecim.
- Precyzyjny dialog polilinii pozwala wpisać X/Y każdego wierzchołka z jednostkami, wyrażeniami i nazwanymi parametrami oraz edytować bulge wychodzącego segmentu bez utraty UUID.
- Auto-więzy są tworzone osobno dla wierzchołków i segmentów polilinii, a cała geometria wraz z więzami jest jednym krokiem undo/redo.
- Punkt jest pełnoprawną encją solvera z 2 DoF, snappingiem, współrzędnymi X/Y, serializacją, edycją i natywnym DXF `POINT`.
- Prostokąt od środka zachowuje dokładny środek oraz wpisywane parametry X/Y, szerokości i wysokości; zwykły prostokąt i wariant od środka korzystają z jednego modelu domenowego.
- `RegularPolygonEntity` zachowuje środek, promień opisany, liczbę boków i obrót. Solver modyfikuje te parametry bez utraty foremności, a boki/wierzchołki mają stabilne referencje.
- `SlotEntity` przechowuje oś i promień zakończeń. Jej granica jest dokładnie złożona z dwóch linii i dwóch analitycznych półokręgów; pole, obwód, snapping, solver i profil nie opierają się na renderowanej siatce.
- Narzędzia punktu, prostokąta od środka, wielokąta i szczeliny mają podgląd, precyzyjne dialogi z jednostkami/parametrami, pozostają aktywne i zapisują gest wraz z auto-więzami jako jeden krok undo/redo.
- `EllipseEntity` przechowuje środek, promień główny/pomocniczy i obrót. Ma analityczne punkty osi, dokładny bounding box, pole, numerycznie całkowany obwód oraz stabilne nearest bez zmiany typu geometrii.
- `EllipticalArcEntity` zachowuje parametry początku i rozwarcia CW/CCW, dokładne końce/środek, długość łuku i ekstremalne punkty bounding boxa.
- Solver traktuje elipsę jako 5 DoF, a łuk elipsy jako 7 DoF. Obsługuje środek, oba promienie, orientację osi głównej, point-on-object, długość łuku i pełne związanie elipsy do DoF=0. Wzajemna prostopadłość osi jest niezmiennikiem parametryzacji encji.
- Snapping elips obejmuje środek, cztery punkty osi, końce i środek łuku, nearest oraz przecięcia line–ellipse, circle/arc–ellipse i ellipse–ellipse z numerycznym doprecyzowaniem pierwiastków.
- Narzędzia elipsy i łuku elipsy są wieloetapowe, pokazują podgląd przed zatwierdzeniem, mają precyzyjne pola jednostek/parametrów i atomowe auto-więzy/undo/redo.
- `BSplineEntity` przechowuje pełną definicję NURBS: stopień 1–10, punkty kontrolne, niemalejący wektor węzłów, dodatnie wagi oraz flagi closed/periodic. Punkt krzywej jest obliczany w czystym modelu przez jednorodny algorytm de Boora, bez zależności od Qt lub DXF.
- B-spline ma stabilne referencje początku, końca, środka i każdego punktu kontrolnego. Solver parametryzuje współrzędne punktów kontrolnych, raportuje prawdziwe DoF, obsługuje fix/coincident/point-on-object i zachowuje definicję w `.siekcad`.
- Snapping B-spline obejmuje końce, środek, punkty kontrolne, nearest oraz numerycznie doprecyzowane przecięcia z liniami, okręgami, łukami i elipsami. Próbkowanie służy wyłącznie do bracketingu pierwiastków i diagnostyki/renderingu, nie zastępuje encji modelowej.
- Narzędzie „Krzywa B-spline” pozostaje aktywne, przyjmuje wieloklikowy wielobok kontrolny, kończy gest przez Enter/prawy przycisk, pozwala ustawić stopień i współrzędne z jednostkami/parametrami oraz zapisuje gest z auto-więzami końców jako jeden krok undo/redo.
- Czysty silnik `cad/editing.py` oblicza analityczne przecięcia line-line, line-circle/arc i circle/arc-circle/arc oraz realizuje split, trim i extend bez zależności od Qt.
- Split dzieli linie i łuki oraz zamienia przecięty okrąg na prawdziwe łuki. Fragment pochodzący od początku krzywej zachowuje UUID, a kolejne fragmenty otrzymują nowe stabilne UUID.
- Trim usuwa fragment najbliższy jawnemu punktowi wskazania; extend przedłuża wskazany koniec linii do najbliższej poprawnej granicy.
- Operacja topologiczna usuwa wyłącznie więzy zależne od zmienianej krzywej, raportuje ich liczbę i dodaje więzy coincident utrzymujące ciągłość nowych fragmentów.
- Cała zmiana, czyszczenie więzów, nowe fragmenty i więzy ciągłości są jedną komendą undo/redo. Błąd recompute przywraca dokładny stan sprzed operacji.
- Workspace udostępnia operacje w menu „Edycja” i menu kontekstowym. Preselection lub ostatnia pozycja kursora wybiera fragment/koniec, zamiast arbitralnej decyzji algorytmu.
- Lekki podgląd podczas gestu, a commit do modelu dopiero po zatwierdzeniu wartości.
- Kartezjański układ modelu z osią Y skierowaną w górę, niezależny od osi sceny Qt.
- Preselection, zaznaczenie pojedyncze/wielokrotne i prostokątne Qt, wspólny `SelectionModel` dla drzewa i widoku.
- Drzewo dokument → szkic → encje i panel podstawowych właściwości.
- Delete jako jedna komenda, Ctrl+Z/Ctrl+Y, Ctrl+S/Ctrl+O, L/R/C oraz fit view.
- Skróty literowe nie przechwytują pisania w polach tekstowych.

### Snapping i wartości

- Snap endpoint, midpoint, center, origin, grid, intersection, nearest, extension, osie X/Y, horizontal, vertical, parallel, perpendicular, tangent i przyrost kąta.
- Promień snapu jest liczony w pikselach ekranu.
- Przecięcia line-line, line-circle i circle-circle są liczone tylko w lokalnym obszarze kursora, bez globalnego skanowania wszystkich par.
- Wynik zawiera typ, UUID źródła, pod-element, relacje kontekstowe i deterministycznie uporządkowanych pobliskich kandydatów.
- Widoczne są znaczniki wielu kandydatów, glif aktywnego snapu i podpowiedź przyszłego więzu; Tab przełącza kandydata przed zatwierdzeniem.
- Czysty generator `cad/auto_constraints.py` zamienia zaakceptowany snap na rzeczywiste więzy solvera. Geometria i wszystkie wywnioskowane więzy trafiają do historii jako jedna atomowa transakcja.
- Auto-więzy można wyłączyć. Generator ponownie sprawdza finalną geometrię po dialogu precyzyjnym, aby nie dodawać więzu niezgodnego z wartością podaną przez użytkownika.
- Punkt-na-obiekcie, równoległość, prostopadłość, styczność oraz kąt są również dostępne jako ręczne polecenia dla zaznaczonej geometrii.
- Bezpieczny parser długości obsługuje mm, cm, m, cale, przecinek/kropkę, proste wyrażenia i nazwane parametry.
- Parser używa kontrolowanego AST i nie wykonuje arbitralnego kodu.

### Nazwane parametry i wyrażenia

- `CadParameter` ma trwały UUID, nazwę, wyrażenie źródłowe, wartość, rodzaj jednostki, zakres globalny/lokalny i bindingi zależności po UUID.
- Parametry tworzą osobny DAG. Ewaluacja topologiczna wykrywa cykle i brakujące referencje przed częściową mutacją dokumentu.
- Parametr lokalny może przesłonić parametr globalny o tej samej nazwie; wiązanie zawsze zachowuje jednoznaczny UUID.
- Wyrażenia obsługują jednostki, bezpieczną arytmetykę i wcześniej zdefiniowane parametry. Analiza wymiarowa odrzuca m.in. iloczyn dwóch długości użyty jako długość.
- Więzy wymiarowe przechowują tekst wyrażenia i `ExpressionBinding`; zmiana parametru przelicza wartość więzu, uruchamia solver i aktualizuje geometrię.
- Rename parametru zachowuje UUID, aktualizuje zależne wyrażenia, a przy kolizji z lokalnym aliasem zachowuje bezpieczne jednoznaczne wiązanie.
- Dodanie, edycja, rename i usunięcie parametru przechodzą przez atomowe komendy undo/redo. Usunięcie używanego parametru jest blokowane z listą użyć.
- Workspace ma menedżer parametrów, podgląd wartości/błędu, autouzupełnianie nazw, zakres globalny/lokalny oraz parametry w drzewie i panelu właściwości.

### Zapis i pliki

- Natywny, wersjonowany format `.siekcad` schematu v4 z więzami, szykami i definicjami parametrów oraz migracjami dokumentów v1/v2/v3.
- Walidowany odczyt, limit 50 MB, kontrolowane typy i UUID.
- Atomowy zapis przez plik tymczasowy, `fsync` i `os.replace`.
- Fixture `tests/fixtures/cad/minimal-v1.siekcad`.
- Podstawowy import DXF POINT/LINE/CIRCLE/ARC/LWPOLYLINE/POLYLINE do encji modelu i eksport zachowujący typ punktu, linii, okręgu, łuku, prostokąta, wielokąta, polilinii oraz warstwę.
- DXF `ARC` jest importowany i eksportowany jako prawdziwy łuk z zachowaniem warstwy, promienia i kątów; round-trip nie tesselluje go do polilinii.
- DXF `LWPOLYLINE` i starszy 2D `POLYLINE` zachowują bulge jako jedną strukturalną polilinię z mieszanymi segmentami line/arc. Import, edycja, `.siekcad` i eksport utrzymują znaki oraz wartości bulge; round-trip nie tworzy osobnych `LINE`/`ARC` ani tessellacji.
- Szczelina jest eksportowana do DXF jako dwie dokładne encje `LINE` i dwa `ARC`, a nie jako wiele krótkich odcinków. Wielokąt pozostaje jedną zamkniętą `LWPOLYLINE`.
- DXF `ELLIPSE` jest importowany i eksportowany jako natywna elipsa lub łuk elipsy z zachowaniem warstwy, środka, wektora osi głównej, ratio oraz parametrów początku/końca; round-trip nie tworzy `LWPOLYLINE`.
- DXF `SPLINE` jest importowany i eksportowany jako natywny `BSplineEntity` z zachowaniem warstwy, stopnia, punktów kontrolnych, węzłów, wag i flag. Wariant DXF z samymi fit points jest jawnie interpolowany do równoważnej definicji kontrolnej przez API matematyczne ezdxf, a eksport nadal tworzy `SPLINE`, nigdy `LWPOLYLINE`.
- Obszar CAD jest dostępny z głównego okna bez eksperymentalnego PIN-u.

## Zmienione i dodane pliki

- `docs/cad-audit.md`
- `docs/cad-progress.md`
- `cad/__init__.py`
- `cad/model.py`
- `cad/commands.py`
- `cad/selection.py`
- `cad/snapping.py`
- `cad/auto_constraints.py`
- `cad/editing.py`
- `cad/constraints.py`
- `cad/solver.py`
- `cad/patterns.py`
- `cad/profiles.py`
- `cad/parameter_data.py`
- `cad/parameter_engine.py`
- `cad/units.py`
- `cad/io.py`
- `app/technical_editor.py`
- `app/simple_window.py`
- `tests/test_cad_foundation.py`
- `tests/test_cad_constraints.py`
- `tests/test_cad_patterns.py`
- `tests/test_cad_profiles.py`
- `tests/test_cad_parameters.py`
- `tests/test_cad_auto_constraints.py`
- `tests/test_cad_arcs.py`
- `tests/test_cad_editing.py`
- `tests/test_cad_polyline.py`
- `tests/test_cad_shapes.py`
- `tests/test_cad_ellipses.py`
- `tests/test_cad_bspline.py`
- `tests/test_cad_bulge.py`
- `tests/test_cad_dxf_structure.py`
- `tests/test_technical_editor.py`
- `tests/fixtures/cad/*`

## Decyzje

- Pierwszy etap pozostaje w Python/PySide6; nie wprowadzono niepasującego stosu webowego.
- Qt jest rendererem i warstwą interakcji, nie modelem geometrii.
- Wewnętrzną jednostką dokumentu jest mm. Wyświetlanie innych jednostek będzie osobną preferencją.
- Encje są immutable; edycja tworzy replacement o tym samym UUID i przechodzi przez komendę.
- `.siekcad` jest JSON-em z jawną wersją i walidacją. Nie zawiera ani nie wykonuje kodu.
- NumPy 2.x jest bezpośrednią zależnością solvera (BSD-3-Clause), zamiast przypadkowej zależności przechodniej ezdxf.
- Nie dodano kernela ani zależności B-Rep bez wcześniejszego prototypu dystrybucji i analizy licencji.

## Testy tego kamienia milowego

- geometria i zachowanie UUID po serializacji;
- komendy, transakcje, undo/redo i przywracanie kolejności;
- 20 mieszanych operacji undo/redo;
- snapping i promień ekranowy;
- model zaznaczenia;
- jednostki, wyrażenia i odrzucanie niebezpiecznego wejścia;
- round-trip `.siekcad`, fixture v1 i odrzucenie obcej wersji;
- DAG, recompute i wykrywanie cyklu;
- zgodność model → renderer Qt;
- brak mutacji modelu przez przesunięcie elementu renderera;
- spójność modelu, renderera i zaznaczenia po undo/redo.
- pełne związanie linii i płytki z otworem, DoF=0 oraz zmianę szerokości 180→200 mm bez utraty pozostałych więzów;
- dokładną identyfikację więzów sprzecznych, redundantnych, referencyjnych i uszkodzonych;
- włączanie/wyłączanie więzu i undo/redo stanu solvera;
- migracje `.siekcad` v1→v2→v3→v4 i round-trip dokumentu z więzami oraz parametrami.
- poprawny profil z otworem, orientację i zagnieżdżenie pętli;
- otwarte końce, małe szczeliny, duplikaty, mikroodcinki, samoprzecięcia i punkty rozgałęzienia;
- naprawę małej szczeliny więzem coincident oraz usuwanie geometrii jako jeden odwracalny krok;
- synchronizację problemu profilu z zaznaczeniem w workspace Qt.
- deterministyczne przecięcia, nearest, extension, osie i przełączanie kandydatów snapu;
- podpowiedzi H/V, równoległości, prostopadłości, styczności i kąta przyrostowego;
- rzeczywiste działanie więzów point-on-object, tangent i angle w solverze;
- atomowe utworzenie geometrii wraz z auto-więzami oraz pojedyncze undo;
- znaczniki wielu kandydatów, glif, Tab i ręczne polecenia rozszerzonych więzów w workspace Qt.
- geometrię analityczną łuku, natywną serializację, solver promienia/długości łuku, przecięcia i nearest snapping;
- zamknięty profil łuk + cięciwa, renderer Qt, undo/redo oraz DXF ARC round-trip bez tessellacji.
- analityczne split/trim/extend linii, łuków i okręgów, regułę zachowania UUID oraz wskazanie właściwego fragmentu kursorem;
- automatyczne więzy ciągłości, konserwatywne usuwanie nieaktualnych więzów, dokładne undo/redo i pełny rollback po wymuszonym błędzie recompute;
- pionowy przepływ operacji split w workspace Qt.
- model/solver polilinii, stabilne referencje segmentów i wierzchołków, snapping, przecięcia oraz diagnostykę poprawnego i samoprzecinającego profilu;
- rzeczywiste wieloklikowe zdarzenia Qt dla polilinii i łuku 3P, podgląd, Enter i atomowe undo/redo;
- DXF LWPOLYLINE round-trip zachowujący jedną encję, warstwę i prawdziwy typ bez zamiany na LINE.
- natywny round-trip punktu, wielokąta foremnego i analitycznej szczeliny w `.siekcad`;
- solver środka/promienia/obrotu wielokąta bez utraty liczby boków i foremności;
- solver, nearest, przecięcia, auto-więzy oraz poprawny zamknięty profil szczeliny;
- rzeczywiste gesty Qt tworzące punkt, prostokąt od środka, wielokąt i szczelinę z atomowym undo/redo;
- eksport DXF zachowujący `POINT`, zamkniętą `LWPOLYLINE` wielokąta oraz dokładne `LINE`/`ARC` szczeliny.
- analityczną geometrię, bounds, nearest, długość i round-trip `.siekcad` elipsy oraz łuku elipsy;
- pełne związanie elipsy do DoF=0, point-on-object i referencyjny pomiar długości łuku elipsy;
- przecięcia line/circle/ellipse, snapping, poprawny pełny profil elipsy i zamknięty profil łuk+cięciwa;
- rzeczywiste wieloetapowe kliknięcia Qt, renderer modelowy i atomowe undo/redo elips;
- natywny DXF `ELLIPSE` round-trip zachowujący warstwę, osie, ratio i parametry łuku bez tessellacji.
- dokładną ewaluację racjonalnego B-spline/NURBS przez de Boora, bounds/nearest/długość oraz round-trip `.siekcad`;
- DoF punktów kontrolnych, stabilne referencje, fix, auto-więzy końców, snapping nearest i przecięcia B-spline z linią;
- diagnostykę otwartego i zamkniętego profilu B-spline oraz rzeczywisty wieloklikowy gest Qt z atomowym undo/redo;
- natywny DXF `SPLINE` round-trip zachowujący warstwę, stopień, punkty kontrolne, węzły i racjonalne wagi, w tym bezpieczną konwersję fit points.
- konwersję dodatniego, ujemnego i ponadpółkolistego bulge do dokładnego `ArcEntity`, stabilne UUID segmentów, bounds/długość oraz round-trip `.siekcad`;
- solver point-on-object, odrzucenie więzu kierunkowego na łuku, analityczny midpoint/nearest/intersection i poprawny profil mieszanej polilinii;
- renderer Qt i precyzyjną edycję bulge z zachowaniem UUID oraz natywny round-trip DXF `LWPOLYLINE`/`POLYLINE` bez flatteningu.
- trwały model warstw CAD/DXF z widocznością, blokadą, kolorem ACI/RGB i typem linii oraz indywidualny styl encji z kolorem, RGB, typem linii, lineweight i uchwytem źródłowym;
- round-trip warstw i stylów przez `.siekcad` i DXF, efektywną widoczność warstwa+encja, panel warstw oraz raport importu z nieobsługiwanymi typami, otwartymi konturami, duplikatami i mikrogeometrią.

Wyniki walidacji z 2026-07-18:

- wszystkie skrypty `tests/test_*.py`: **OK** (48 plików, w tym warstwy/styl/raport DXF, natywne mieszane polilinie DXF bulge, racjonalny B-spline/DXF SPLINE, analityczne elipsy i łuki eliptyczne, punkt, prostokąt od środka, natywny wielokąt, analityczna szczelina, polilinie i łuki 3P, operacje split/trim/extend, solver, parametry, szyki, profile i auto-więzy CAD, Qt oraz pełne regresje optymalizatora; 589,4 s);
- `tests/stability_smoke.py`: **OK** (80 przypadków fuzz i performance do 500 formatek; 500 formatek 8,768 s; cały przebieg 225,5 s);
- `python -m compileall` dla `app`, `cad`, `core`, `database`, `import_export`, `ui`, `workers`, `algorithms` i `tests`: **OK**;
- `scripts/BUILD_EXE.bat`: **OK**, build przenośny bez obfuskacji;
- test startu gotowego EXE: **OK**, proces utrzymał działanie przez 5 s i został kontrolowanie zatrzymany;
- inspekcja archiwum PyInstaller: obecne m.in. `app.technical_editor`, `cad.dxf_io`, `cad.editing`, `cad.auto_constraints`, `cad.snapping`, `cad.parameter_data`, `cad.parameter_engine`, `cad.profiles`, `cad.patterns`, `cad.constraints`, `cad.solver`, `cad.commands`, `cad.io`, `cad.model` oraz NumPy;
- kontrola etykiet UTF-8 w `app/technical_editor.py`: **OK**, bez markerów mojibake;
- SHA-256 zbudowanego EXE: `29671555CA8CB6E8B98AB3D4F4B37EBC8D7D4C51468387BCDF51F66DBBF5C287` (13 107 145 bajtów).

Repozytorium nie ma skonfigurowanego `ruff`, `mypy`, `pyright` ani innego lintera/typecheckera; moduły nie są zainstalowane w `.venv`. Nie raportujemy więc fikcyjnego lint/typecheck. Kontrolę składni i importów wykonał `compileall`. Oficjalny build próbował uruchomić PyArmor przez istniejące `python -m pyarmor`, czego PyArmor 9.2.5 nie udostępnia; zgodnie z dotychczasową logiką skryptu wykonano poprawny fallback do zwykłego PyInstaller.

## Znane ograniczenia — funkcje nieukończone

- Solver nie obsługuje jeszcze symmetric ani wewnętrznego wyrównania/tangent handles B-spline. Wewnętrzne wyrównanie osi elipsy wynika obecnie z jej trwałej parametryzacji, a styczność nie obejmuje jeszcze elips ani B-spline. Styczność dwóch okręgów obejmuje styczność zewnętrzną.
- Duże układy więzów są obecnie rozwiązywane synchronicznie; worker z anulowaniem i debounce jest wymagany przed dużymi szkicami.
- Prostokąt jest jedną parametryczną encją, ale jego narożniki i centrum mogą już uczestniczyć w więzach wymiarowych.
- Nie ma jeszcze krzywej Béziera jako osobnego typu, offset, join ani szyku kołowego. Diagnostyka profilu łuków i B-spline używa wyłącznie roboczej tessellacji do analizy topologii; encja, solver, zapis i DXF pozostają analityczne/NURBS. Diagnostyka przecięcia pełnej elipsy z okręgiem lub inną elipsą nie jest jeszcze kompletna, podobnie jak przecięcia B-spline–B-spline.
- Split/trim/extend nie operują jeszcze bezpośrednio na podsegmentach mieszanej polilinii; obecnie działają na samodzielnych liniach, okręgach i łukach. Więzy kierunkowe H/V/parallel/perpendicular/angle są świadomie ograniczone do prostych segmentów polilinii.
- Split/trim/extend świadomie usuwają więzy odwołujące się do zmienianej krzywej, ponieważ automatyczne przepinanie wymiaru lub `FIX` na arbitralny fragment byłoby niebezpieczne. Pasek stanu informuje o liczbie usuniętych więzów, a undo przywraca je dokładnie.
- Nearest obejmuje linie, polilinie, B-spline, elipsy, wielokąty, okręgi, łuki i szczeliny; extension pozostaje operacją liniową.
- Panel właściwości jest informacyjny; edycja odbywa się przez dwuklik i dialog precyzyjny.
- Workspace nadal jest dialogiem uruchamianym z głównego modułu, a nie pełnym systemem kart/docków.
- Wyrażenia nie obsługują jeszcze odwołań do właściwości innych obiektów (`Body.Length`), kątów jako osobnego wymiaru ani funkcji warunkowych; bieżący silnik obejmuje bezpieczne wyrażenia długości.
- Import DXF zachowuje już warstwy, ACI/RGB, widoczność, blokadę, linetype, lineweight i źródłowy handle oraz tworzy raport diagnostyczny. Nie obsługuje jeszcze bloków/INSERT, pełnej listy encji, skalowania jednostek innych niż mm ani automatycznej naprawy wskazanych problemów.
- Nie istnieje `cad/kernel`, B-Rep, renderer 3D, STEP ani Part Design. Żadna siatka nie jest przedstawiana jako dokładna bryła.
- Brak autosave/recovery `.siekcad` i miniatury; istnieją migracje schematu v1→v2→v3→v4.
- Scenariusz akceptacyjny A jest spełniony na poziomie parametrycznego Sketchera 2D. Scenariusze B–D nie są jeszcze spełnione; scenariusz E jest pokryty tylko na poziomie fundamentu dokumentu, bez pełnego drzewa funkcji 3D.

## Następny krok

Scenariusz A, diagnostyka profilu, trwałe nazwane parametry, automatyczne więzy, łuki kołowe i eliptyczne, natywna elipsa/mieszana polilinia/B-spline, punkt, prostokąt od środka, parametryczny wielokąt, analityczna szczelina, split/trim/extend oraz strukturalne warstwy/styl/raport DXF są wykonane. Następny fragment to natywne definicje bloków i instancje `INSERT` z opcją zachowania lub explode, a potem przygotowanie adaptera B-Rep/STEP.
