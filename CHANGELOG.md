# Changelog — SIEKACZ 9000

## 2026-08-10

### Interfejs trybu inteligentnego, CAD i szybki Excel

- Przełącznik inteligentnego doboru formatów otrzymał spójny, kompaktowy wygląd
  w motywie ciemnym i jasnym.
- W podglądzie CAD trzy techniczne przyciski obrotu zastąpiła interaktywna
  kostka widoku. Ściany ustawiają rzuty główne, narożniki — widoki izometryczne.
- PPM obraca model, środkowy przycisk przesuwa, a PPM z rolką obraca widok
  wokół osi kamery; zwykła rolka nadal steruje powiększeniem.
- Szablon masowego zlecenia ma teraz jedną kolumnę `Format płyty` zamiast dwóch
  wymiarów, czytelny arkusz `Katalog` oraz listy materiałów, grubości i formatów.
- Każde otwarcie lub zapisanie kopii szablonu z poziomu aplikacji odświeża jego
  katalog z aktualnie wczytanej bazy produktów, formatów i cen.

### Ekrany obliczeń

- Tymczasowo wycofano transmisje YouTube z pandami i pingwinami.
- Pozostały dwa lokalne tryby: lekka animacja kosmiczna oraz animacja samuraja;
  aplikacja nie wymaga już modułu WebEngine do ekranu obliczeń.

### Podgląd i pomiary CAD

- Przy tabeli formatek oraz w menu edytora technicznego dodano wspólny podgląd
  plików `DXF`, `STEP`/`STP` i `STL` (ASCII oraz binarnych).
- Model można obracać myszą albo skokowo wokół osi X/Y/Z, przesuwać,
  przybliżać i automatycznie dopasować do okna.
- Kliknięcie konkretnej krawędzi pokazuje jej długość, a osobny tryb mierzy
  przestrzenną odległość między dwoma wskazanymi wierzchołkami.
- Wymiary i geometria DXF/STEP są normalizowane do milimetrów. Ponieważ STL
  nie zapisuje jednostek, aplikacja jawnie informuje o przyjęciu milimetrów;
  krzywe STEP bez analitycznego silnika są opisane jako pomiar cięciwy.

### Inteligentny dobór formatów płyt

- W sekcji `Dostępne płyty` dodano przełącznik `Inteligentny dobór formatów`.
- Po włączeniu program pobiera wszystkie wycenione formaty zgodne z materiałem
  i grubością formatek; wpisana ręcznie ilość nie ogranicza wtedy wyszukiwania.
- Dla każdego formatu powstają produkcyjne, uwzględniające rzaz wzorce rozkroju,
  a planer branch-and-bound wybiera ich globalną mieszankę minimalizującą łączną
  powierzchnię kupionych płyt, koszt, liczbę arkuszy i liczbę cięć.
- Wynik pozostaje objęty walidacją gilotynową, pokazuje liczbę użytych płyt
  każdego rozmiaru i zapisuje ustawienie razem z projektem.

### Masowe zlecenia Excel

- Dodano gotowy skoroszyt `SIEKACZ9000_szablon_zlecenia.xlsx` z arkuszami
  `Formatki`, `Płyty`, `Instrukcja` i `Przykład`, tabelami, walidacją danych,
  formułami kontrolnymi i zamrożonymi nagłówkami.
- W sekcji formatek dodano menu `Excel`, z którego można otworzyć szablon,
  zapisać jego kopię albo wczytać kompletne zlecenie XLSX.
- Import rozpoznaje polskie i angielskie warianty nagłówków oraz przenosi ilość,
  materiał, grubość, wymiary, obrót, priorytet, etykietę, notatki, sztapel,
  priorytet boku cięcia i ilość dostępnych płyt.
- Tabele formatek i płyt obsługują wklejanie wielu wierszy bezpośrednio z Excela
  przez `Ctrl+V`; błędne wartości i sztapel większy od liczby płyt są odrzucane
  z komunikatem wskazującym wiersz.

### Ciekawostki podczas obliczeń

- Zastąpiono aktywną talię komunikatów 200 unikalnymi ciekawostkami: po 40 o
  pociągach, statkach, samolotach, II wojnie światowej i piratach.
- Ciekawostki są losowane bez powtórzeń w ramach pełnego cyklu; po przetasowaniu
  pierwsza pozycja nie powtarza ostatnio pokazanej.

### Wydania Windows, macOS i Linux

- Aktualizator wykrywa system i architekturę, a następnie wybiera wyłącznie
  właściwy instalator Windows x64, macOS Intel/Apple Silicon albo AppImage Linux
  x86_64/ARM64.
- Publikator uruchamia jeden workflow GitHub Actions, który buduje natywnie pięć
  paczek i dołącza je do wspólnego wydania.
- Dodano skrypty budowy DMG dla macOS, AppImage dla Linuksa oraz kontrolę zgodności
  Windows Server. Wersja serwerowa wymaga środowiska Desktop Experience.
- Instalator Windows otrzymuje jednoznaczną nazwę platformową
  `SIEKACZ9000_Setup_v<wersja>_windows-x64.exe`.

## 2026-08-03

### Powiązanie formatek z płytami

- Kompletne pary `materiał + grubość` obecne jednocześnie w formatkach i płytach
  otrzymują wspólną, bardzo delikatną kolorową poświatę. Puste wiersze pozostają
  neutralne, a czerwone oznaczenia błędów mają zawsze pierwszeństwo.
- Pierwsza formatka otrzymuje domyślnie rzeczywiście dostępną parę materiał–grubość
  z aktualnego katalogu zamiast pustego materiału i stałej grubości 18 mm.
- Zmiana grubości formatki tworzy brakującą specyfikację płyty, ale nie zmienia
  żadnej istniejącej płyty przypisanej wcześniej do innego wiersza.
- Synchronizacja wymagań materiałowych zachowuje kolejność wierszy i nie zależy
  już od losowej kolejności elementów zbioru.
- Puste wiersze robocze nie tworzą płyt tylko dlatego, że mają domyślną ilość 1.
- Zmiana materiału czeka na wybór zgodnej grubości; nie może już utworzyć płyty
  z nowym materiałem i grubością odziedziczoną z poprzedniego materiału.
- Przy aktywnym katalogu wypełniona formatka bez materiału dostaje jednoznaczny
  kontekst automatycznie albo jest blokowana czytelnym komunikatem przed obliczeniem.

### Wymiarowanie podglądu

- Nad płytą pozostają szczegółowe grupy pasów, np. `4 × 120 mm` i `2 × 33 mm`.
- Pod płytą wyświetlana jest jedna linia od początku arkusza do końca zużytego
  obszaru z podsumowaniem `Zużycie: … mm`; usunięto powtórzone grupy pasów.

## 2026-07-28

### Płyty i kierunek cięcia

- Dla materiału i grubości wybierany jest domyślnie format 1000 × 2000 mm,
  jeżeli ma cenę w bieżącym katalogu; POM-C nie startuje już od 620 × 2000 mm.
- Przy wysokości i szerokości płyty dodano szare/niebieskie kropki wyboru boku,
  wzdłuż którego mają przebiegać długie cięcia pasów.
- Wybrany kierunek trafia do optymalizatora, jest zachowywany przy orientowaniu
  arkusza oraz zapisuje się i odtwarza razem z projektem.
- Przed uruchomieniem optymalizacji program wykrywa formatki większe od każdej
  zgodnej płyty i pokazuje czytelny komunikat z dostępnymi formatami.

### Usprawnienia obsługi

- Dodano czerwony przycisk `SAMOUCZEK` oraz 11-etapowy przewodnik po materiałach,
  formatkach, płytach, priorytecie, kierunku cięcia, sztaplu, podglądzie,
  ustawieniach, projektach, raportach, eksporcie i aktualizacji cennika.
- Samouczek działa jako przyciemniona nakładka na aplikację: aktualna kontrolka
  pozostaje jasna, ma pulsującą ramkę i animowany wskaźnik kliknięcia, a krótki
  dymek automatycznie ustawia się obok niej.
- Po ukończeniu samouczka czerwony przycisk znika z górnego paska. Przewodnik
  można w każdej chwili uruchomić ponownie neutralnym przyciskiem w Ustawieniach.
- Podświetlenie wskazywanego pola dokładnie pokrywa jego obszar i zachowuje
  zaokrąglone rogi.
- Po wybraniu grubości formatki kursor przechodzi automatycznie do wysokości.
- Poszerzono kolumny `GRUBOŚĆ`, `SZEROKOŚĆ` i `FORMAT`, zmniejszając kolumnę
  sztapla bez poszerzania całej tabeli; kolumna sztapla wypełnia pozostałe
  miejsce aż do prawego obramowania.
- Menu materiałów nie powtarza już kodów (`PA6G PA6G`, `PE PEEK`), a znacznik
  szarego PP ma kremowy kolor odpowiadający materiałowi.
- Znacznik materiału w nagłówku podglądu znajduje się przed nazwą płyty i jest
  wycentrowany z nią pionowo.
- Usunięto redundantny przycisk `Fit`; kliknięcie wskaźnika `100%` nadal
  dopasowuje cały rozkrój i resetuje powiększenie.

## 2026-07-26

### Katalog materiałów XLSX

- Obsługa dwóch wariantów macierzy Boral: pierwotnego układu z nagłówkiem
  `Format` oraz uzupełnionego układu z `Kolor / odmiana` i
  `Cena netto [zł/m²] — format płyty (mm)`.
- Materiał, kolor/odmiana, grubość, format płyty i cena za m² są odczytywane
  z dynamicznych wierszy i kolumn; puste pola cenowe oznaczają brak dostępności.
- Po imporcie oraz po zapisaniu obserwowanego XLSX katalog odświeża się
  automatycznie, uwzględniając nowe arkusze, kolory, grubości, formaty i ceny.
- Lista grubości formatek zawiera wyłącznie opłacone pozycje wybranego
  materiału/odmiany. Nie miesza już starych wartości z projektu z katalogiem.
- Presety formatów i płyty tworzone automatycznie z formatek wybierają wyłącznie
  formaty z aktualną ceną w katalogu.
- Koszt płyty wyliczany jest z ceny konkretnego wybranego formatu, a nie z
  pierwszej pasującej ceny materiału.
- Wycena rozkroju korzysta z ceny dokładnie użytej płyty z katalogu, więc nie
  może nadpisać jej stara ręcznie zapisana stawka materiału i grubości.
- Komunikat po imporcie uproszczony do: „Cennik zaktualizowano pomyślnie.”

### Interfejs i limity

- Limit łącznej liczby formatek zwiększony do 15 000; przekroczenie wymaga PIN-u
  administratora `1984`.
- Wprowadzanie własnej grubości działa przy pierwszym wyborze.
- Podgląd raportu PDF: Ctrl + rolka przybliża pod kursorem, a lewy przycisk myszy
  przesuwa dokument.
- Uporządkowano proporcje kolumn tabel oraz widoki wielu płyt/formatów, aby
  ograniczyć ucinanie tekstu.

### Optymalizator i raporty

- Usprawniono grupowanie powtarzalnych pasów i opisów płyt w podglądzie oraz
  raporcie, dzięki czemu długie listy numerów nie zasłaniają danych.
- Zachowano produkcyjne reguły rozkroju: kerf, walidację gilotynowości,
  odzysk odpadów i ocenę minimalizującą realne zużycie materiału.
