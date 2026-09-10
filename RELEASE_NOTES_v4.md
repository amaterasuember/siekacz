# SIEKACZ 9000 — 4.0.2

- Enter w polu ilości zapisuje wartość i uruchamia rozkrój bez dodawania wiersza. Tab nadal przechodzi do następnej formatki.
- Usuwanie i czyszczenie formatek zwalnia nieużywane automatyczne płyty; cofanie i ponawianie przywraca ich powiązania.
- Podgląd rozkroju i PNG zachowują kontur DXF zapisany w projekcie. Prostokąt przerywany oznacza półfabrykat do cięcia piłą.
- CAD: pomiar pojedynczych boków, dokładny promień R i długość łuku, przyciąganie pomiaru do narożników i krawędzi.
- CAD: wyodrębnienie wskazanego konturu z arkusza technicznego i dodanie go jako formatki wraz z geometrią wewnętrzną.
- Import DXF uwzględnia jednostki pliku; zbiorczy komunikat o pominiętych adnotacjach nie zasłania narzędzi.

# SIEKACZ 9000 — 4.0.1

- Zmiana materiału lub grubości istniejącej formatki aktualizuje powiązaną płytę, zamiast dodawać kolejne płyty.
- Osobna płyta powstaje wtedy, gdy inna formatka nadal potrzebuje wcześniejszego materiału i grubości. Powrót do wspólnego materiału usuwa zbędną płytę utworzoną automatycznie.
- Powiązania działają przy cofaniu i ponawianiu zmian oraz po zapisaniu i ponownym otwarciu projektu. Ręcznie dodany, niezależny zapas pozostaje zachowany.
- Zmiana materiału zachowuje ilość płyt, sztapel, priorytet i kierunek słojów. Podgląd wcześniejszego rozkroju jest sygnalizowany komunikatem o konieczności ponownego obliczenia.

# SIEKACZ 9000 — 4.0.0

- Odświeżony jasny i ciemny motyw, spokojniejsze cienie, czytelniejsze statystyki i obsługa mniejszych okien. Dotychczasowy układ pracy pozostaje zachowany.
- Krojenie wzdłuż słojów: kliknij gwiazdkę przy wymiarze płyty i wybierz jej bok. Następnie oznacz bok formatki. Złote gwiazdki oznaczają boki, które muszą być równoległe. Menu jest również dostępne pod prawym przyciskiem myszy w tabeli.
- Przypisanie słojów jest twardym ograniczeniem rozkroju. Program wymusza potrzebny obrót o 90°, uwzględnia blokady obrotu i zachowuje kierunek także na brakujących płytach. Jeśli nie oznaczono pasującej płyty, prosi o uzupełnienie przypisania.
- Strzałki słojów na podglądzie i obrazach rozkroju. Ustawienia zapisują się w projekcie i historii, działają z cofaniem zmian oraz importem z Excela. Opcjonalna kolumna „Słoje” przyjmuje „wysokość”, „szerokość” lub „brak”.
- Lekka animacja układu słonecznego w prostym trybie ładowania, z rzeczywistym postępem i możliwością anulowania.
- Wzmocniona kontrola wymiarów, rzazu, kompletności zamówienia i wyniku rozkroju. Poprawione odzyskiwanie odpadów oraz spójność statystyk czasu cięcia.
- Bezpieczny zapis projektów, stabilniejsze anulowanie obliczeń i zamykanie aplikacji, poprawki eksportów oraz sprawdzanie uruchomienia gotowego pakietu Windows podczas budowania.

Starsze projekty bez przypisania słojów zachowują dotychczasowe zachowanie. Do automatycznego obrócenia formatki wymagane jest zezwolenie na obrót w ustawieniach.
