from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ui.transport_history_facts import ALL_FACTS


# ---------------------------------------------------------------------------
# Shared state data-class (kept for compatibility)
# ---------------------------------------------------------------------------
@dataclass
class OptimizationProgressState:
    title: str = "Liczenie..."
    subtitle: str = ""
    checkedVariantsCount: int = 0
    globalProgressPercent: int = 0
    currentStageLabel: str = "Liczenie..."
    progressBars: list[dict[str, object]] = field(default_factory=list)
    footerText: str = ""
    activeBoardLayouts: list[object] = field(default_factory=list)
    currentLayoutSnapshot: object | None = None
    scannerPosition: float = 0.0
    glowPhase: float = 0.0
    animationEnabled: bool = True
    reducedMotion: bool = False
    visible: bool = False


class OptimizationProgressAdapter:
    @staticmethod
    def metrics_from_project(project: object | None) -> dict[str, object]:
        return {}


def _ease_out_quint(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1.0 - pow(1.0 - value, 5)


def _resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


# ---------------------------------------------------------------------------
# Sims-style funny messages
# ---------------------------------------------------------------------------
_SIMS_MESSAGES: tuple[str, ...] = (
    "Miód nigdy się nie psuje — w egipskich grobowcach znaleziono 3000-letni miód, wciąż jadalny.",
    "Woda może wrzeć i zamarzać jednocześnie — to zjawisko nazywa się punktem potrójnym.",
    "Mrówki nie mają płuc — oddychają przez małe otwory w pancerzu zwane spiralami.",
    "Gdybyś usunął całą pustą przestrzeń z atomów ludzkości, zmieścilibyśmy się w cukierku.",
    "Flamingi są różowe, bo jedzą różowe krewetki — bez nich byłyby białe.",
    "Octopus vulgaris ma trzy serca i niebieską krew z miedzi zamiast żelaza.",
    "Na Marsie dzień trwa 24 godziny i 37 minut — prawie identycznie jak na Ziemi.",
    "Pszczoła miodna przez całe życie produkuje tylko 1/12 łyżeczki miodu.",
    "Żółwie mogą oddychać przez tylną część ciała — przydatne podczas hibernacji.",
    "Piorun uderza w Ziemię około 100 razy na sekundę — 8 milionów razy dziennie.",
    "Najgłębszy punkt oceanu — Rów Mariański — jest głębszy niż Mount Everest jest wysoki.",
    "Złoto jest tak ciągliwe, że z 1 grama można wyciągnąć drut długości 3 km.",
    "Szympansy mają pamięć krótkotrwałą lepszą niż ludzie — potwierdziły to testy liczbowe.",
    "Kałamarnica olbrzymia ma oczy wielkości talerza — największe w królestwie zwierząt.",
    "Księżyc oddala się od Ziemi o 3,8 cm rocznie.",
    "Rdzeń Ziemi jest gorętszy niż powierzchnia Słońca — osiąga 6000°C.",
    "Drzewo sekwoi może żyć ponad 3000 lat i rosnąć wyżej niż 115 metrów.",
    "Pingwiny wydają odgłos ze swoim partnerem unikalny jak odcisk palca — rozpoznają się w tłumie.",
    "Przeciętna chmura waży tyle co 500 słoni — to ok. 500 000 kg wody.",
    "Ośmiornice mają neurony nie tylko w mózgu, ale i w każdym z ramion.",
    "Lodowce pokrywają 10% lądu Ziemi i przechowują 75% słodkiej wody świata.",
    "Motyl monarcha pokonuje 4500 km w jedną stronę podczas migracji z Kanady do Meksyku.",
    "Pluton jest mniejszy niż Stany Zjednoczone — ma średnicę 2376 km.",
    "W kosmosie nie możesz płakać — łzy nie spływają w stanie nieważkości.",
    "Krowa Bessie w ciągu życia produkuje tyle mleka, że wystarczyłoby na 200 000 szklanek.",
    "Mózg człowieka generuje tyle energii elektrycznej co 10-watowa żarówka.",
    "Woda mórz świata pochłania 93% nadmiaru ciepła z efektu cieplarnianego.",
    "Stonka Manta ma rozpiętość skrzydeł do 9 metrów — jest największą rybą płaską.",
    "Najszybszy wiatr zmierzony na Ziemi wynosił 484 km/h — w czasie tornada w USA.",
    "Mrówki ogniowe potrafią budować tratwy z własnych ciał i przetrwać powodzie.",
    "Słoń jest jedynym ssakiem, który nie potrafi skakać.",
    "Na Wenus dzień jest dłuższy niż rok — obrót trwa 243 dni ziemskich.",
    "Tkanina pająka jest 5 razy mocniejsza od stali o tej samej grubości.",
    "Ludzie i żyrafy mają tyle samo kości w szyi — po siedem kręgów szyjnych.",
    "W całkowitym mroku jaskini wzrok całkowicie zanika po kilku dniach.",
    "Delfiny nadają sobie imiona — każdy ma unikalny wzorzec gwizdania.",
    "Morze Kaspijskie jest technicznie największym jeziorem na świecie, nie morzem.",
    "Latawce zostały wynalezione w Chinach ponad 2000 lat temu.",
    "Mrowisko Lasius niger może liczyć ponad milion osobników i żyć 20 lat.",
    "Pierwsze okulary korekcyjne wynaleziono we Włoszech w XIII wieku.",
    "Ziemia nie jest idealną kulą — jest spłaszczona na biegunach o ok. 21 km.",
    "Bobry budują tamy, które zmieniają bieg rzek — potrafią przebudować ekosystem.",
    "Tygrys syberyjski potrafi skakać na odległość 10 metrów w poziomie.",
    "Wątroba regeneruje się całkowicie — przeszczep 25% wątroby wystarczy, by odrosła.",
    "Dźwięk nie rozchodzi się w kosmosie — między gwiazdami panuje absolutna cisza.",
    "Pingwin cesarski może nurkować na głębokość 535 m i wstrzymywać oddech 22 minuty.",
    "Skały księżycowe są o 200 milionów lat starsze niż najstarsze skały na Ziemi.",
    "Mrowisko argentyńskiej mrówki inwazyjnej ciągnie się od Włoch po Portugalię — jeden superorganizm.",
    "Kolibry są jedynymi ptakami, które mogą latać tyłem.",
    "Przeciętny człowiek chodzi w ciągu życia dystans równoważny czterem okrążeniom Ziemi.",
    "Banan jest jagodą, a truskawka botanicznie nią nie jest.",
    "Wieża Eiffla latem rośnie o ok. 15 cm, bo stal rozszerza się od ciepła.",
    "Krew kałamarnicy jest niebieska, bo do transportu tlenu używa miedzi.",
    "Serce krewetki znajduje się w jej głowie.",
    "Ślimak może spać nawet trzy lata z rzędu.",
    "Grupa flamingów nazywa się „flamboyance”, czyli wystawność.",
    "Słonie potrafią rozpoznać siebie w lustrze — to oznaka samoświadomości.",
    "Język niebieskiego wieloryba waży tyle co dorosły słoń.",
    "Ludzkie kości są, gram za gram, mocniejsze od stali.",
    "Twój żołądek dostaje nową wyściółkę co kilka dni, bo inaczej strawiłby sam siebie.",
    "Człowiek ma więcej komórek bakteryjnych niż własnych — mniej więcej po połowie.",
    "Twoje paznokcie u rąk rosną około cztery razy szybciej niż u stóp.",
    "Nie da się połknąć i oddychać jednocześnie — krtań się zamyka.",
    "Kichnięcie może pędzić z prędkością ponad 150 km/h.",
    "Twoje oczy mrugają około 20 000 razy dziennie.",
    "Mózg zużywa około 20% energii ciała, choć waży tylko 2%.",
    "Człowiek jest jedynym zwierzęciem, które rumieni się ze wstydu.",
    "Odcisk języka jest tak samo unikalny jak odcisk palca.",
    "Twoje serce bije około 100 000 razy dziennie.",
    "W ludzkim ciele jest dość węgla, by zrobić 9000 ołówków.",
    "Dorosły człowiek ma w naczyniach około 100 000 km naczyń krwionośnych.",
    "Co sekundę twoje ciało produkuje około 25 milionów nowych komórek.",
    "Najdłuższa czkawka trwała 68 lat bez przerwy.",
    "Najmniejszą kością ciała jest strzemiączko w uchu — ma ok. 3 mm.",
    "Mleko matki świeci pod światłem UV.",
    "Niemowlęta rodzą się bez rzepek — kostnieją dopiero koło 3. roku życia.",
    "Najsilniejszym mięśniem ciała względem masy jest mięsień żwacz w szczęce.",
    "Twoje ucho i nos rosną przez całe życie.",
    "Saturn jest tak lekki, że unosiłby się na wodzie, gdyby istniała taka wanna.",
    "Na Jowiszu i Saturnie prawdopodobnie pada deszcz z diamentów.",
    "Jeden dzień na Słońcu nie istnieje tak samo wszędzie — równik obraca się szybciej niż bieguny.",
    "Światło Słońca leci do Ziemi około 8 minut i 20 sekund.",
    "W Drodze Mlecznej jest więcej gwiazd niż ziaren piasku na ziemskich plażach.",
    "Neutronowa gwiazda jest tak gęsta, że łyżeczka jej materii waży miliardy ton.",
    "W kosmosie astronauci rosną o kilka centymetrów, bo kręgosłup się rozluźnia.",
    "Ślad stopy Armstronga na Księżycu może przetrwać miliony lat — nie ma tam wiatru.",
    "Kosmiczna próżnia pachnie podobno spalonym metalem i prochem.",
    "Wszystkie planety Układu Słonecznego zmieściłyby się między Ziemią a Księżycem.",
    "Galaktyka Andromedy zderzy się z Drogą Mleczną za około 4 miliardy lat.",
    "Jeden rok na Neptunie trwa 165 lat ziemskich.",
    "Plamy na Słońcu są chłodniejsze, dlatego wyglądają na ciemne.",
    "Czarna dziura tak zakrzywia czas, że blisko niej zegar zwalnia.",
    "Najgorętsza znana planeta ma temperaturę topiącą żelazo po stronie dziennej.",
    "Pierścienie Saturna mają miejscami zaledwie 10 metrów grubości.",
    "Wenus obraca się w przeciwną stronę niż większość planet — Słońce wschodzi tam na zachodzie.",
    "Promień kosmiczny może co jakiś czas przebić twój ekran i zmienić piksel.",
    "Międzynarodowa Stacja Kosmiczna okrąża Ziemię raz na 90 minut.",
    "Australia jest szersza niż Księżyc.",
    "Rosja ma 11 stref czasowych.",
    "Sahara bywa zimą pokryta śniegiem.",
    "Najmniejszy kraj świata, Watykan, zmieściłby się wielokrotnie w Central Parku.",
    "Punkt Nemo to miejsce na oceanie najdalej oddalone od jakiegokolwiek lądu.",
    "Wodospad Anioła w Wenezueli jest tak wysoki, że woda paruje, zanim spadnie na dół.",
    "Islandia nie ma komarów — to jeden z nielicznych takich krajów.",
    "Kanada ma więcej jezior niż wszystkie inne kraje świata razem wzięte.",
    "Najsuchsze miejsce na Ziemi, pustynia Atakama, miejscami nie widziało deszczu od stuleci.",
    "Mount Everest rośnie o kilka milimetrów rocznie wskutek ruchu płyt.",
    "Amazonka wytwarza ponad 20% tlenu produkowanego przez lądy.",
    "Morze Martwe jest tak słone, że nie da się w nim utonąć — człowiek unosi się sam.",
    "Najdłuższa rzeka świata, Nil, jest dłuższa niż szerokość całych Stanów Zjednoczonych.",
    "Bambus potrafi urosnąć nawet 90 cm w jeden dzień.",
    "W jednej łyżeczce zdrowej gleby żyje więcej organizmów niż ludzi na Ziemi.",
    "Drzewa potrafią ostrzegać się nawzajem przed szkodnikami przez sieć grzybni.",
    "Najstarsze żyjące drzewo świata ma ponad 4800 lat.",
    "Słoneczniki obracają się w ciągu dnia za Słońcem — to zjawisko zwie się heliotropizmem.",
    "Liść lotosu jest tak gładki, że woda spływa z niego, zbierając cały brud.",
    "Niektóre grzyby świecą w ciemności, by przyciągnąć owady roznoszące zarodniki.",
    "Najcięższy organizm świata to grzyb opieńka w Oregonie, rozległy na kilka kilometrów.",
    "Pomidory mają więcej genów niż człowiek.",
    "Ananas potrzebuje około dwóch lat, by dojrzeć do zbioru.",
    "Marchewki pierwotnie były fioletowe, a pomarańczowe wyhodowano dopiero później.",
    "Orzech ziemny nie jest orzechem, tylko rośliną strączkową jak groch.",
    "Chili piecze, bo zawiera kapsaicynę, która oszukuje receptory ciepła w języku.",
    "Czekolada była niegdyś tak cenna, że Aztekowie używali kakao jako pieniędzy.",
    "Gałka muszkatołowa była kiedyś warta więcej niż złoto tej samej wagi.",
    "Miód to jedyny pokarm, który zawiera wszystkie substancje potrzebne do życia.",
    "Jabłka unoszą się na wodzie, bo w jednej czwartej składają się z powietrza.",
    "Ketchup w XIX wieku sprzedawano jako lek na niestrawność.",
    "Krewetka modliszkowa uderza tak szybko, że woda wokół niej na chwilę się gotuje.",
    "Pies potrafi wyczuć zapach miliony razy słabszy niż człowiek.",
    "Kot spędza około 70% życia, śpiąc.",
    "Sowa nie potrafi ruszać oczami — zamiast tego obraca głowę nawet o 270 stopni.",
    "Rekiny istniały, zanim na Ziemi pojawiły się drzewa.",
    "Krokodyle istnieją w niemal niezmienionej formie od czasów dinozaurów.",
    "Niektóre meduzy są technicznie nieśmiertelne — potrafią cofać się do stadium larwy.",
    "Koala ma odciski palców tak podobne do ludzkich, że mylą kryminalistyków.",
    "Wombaty wydalają odchody w kształcie sześcianu.",
    "Gepard nie potrafi ryczeć, za to miauczy i mruczy jak duży kot.",
    "Żyrafa ma taką samą liczbę kręgów szyjnych jak człowiek, choć szyja ma 2 metry.",
    "Mrówka potrafi unieść ciężar pięćdziesięciokrotnie większy od własnego.",
    "Niedźwiedź polarny ma czarną skórę pod przezroczystym futrem.",
    "Kret potrafi w jedną noc wykopać tunel o długości 100 metrów.",
    "Nietoperze zawsze skręcają w lewo, wylatując z jaskini.",
    "Słoń morski potrafi zanurkować na głębokość ponad 1500 metrów.",
    "Język kameleona jest dłuższy niż całe jego ciało.",
    "Pszczoły potrafią rozpoznawać ludzkie twarze.",
    "Najszybszy mięsień ludzkiego ciała to ten, który zamyka powiekę.",
    "Diament i ołówek zbudowane są z tego samego pierwiastka — węgla.",
    "Szkło to w istocie bardzo wolno płynąca ciecz o nieuporządkowanej strukturze.",
    "Hel jest jedynym pierwiastkiem odkrytym najpierw w kosmosie, a dopiero potem na Ziemi.",
    "Gdyby zwinąć całe DNA jednej komórki, miałoby około 2 metry długości.",
    "Pojedyncza błyskawica jest pięciokrotnie gorętsza niż powierzchnia Słońca.",
    "Gorąca woda potrafi w pewnych warunkach zamarznąć szybciej niż zimna.",
    "Tytan jest tak wytrzymały i lekki, że używa się go w implantach i rakietach.",
)

# A fresh, deliberately finite deck. The shuffle-bag shows every item once
# before any repeat, so this is also an easy contract to cover in tests.
_SIMS_MESSAGES = (
    "Pierwsza piła tarczowa opisana drukiem była napędzana ręcznie i służyła do cięcia drewna.",
    "Rzaz to materiał bezpowrotnie usunięty przez zęby piły, dlatego musi być częścią geometrii rozkroju.",
    "Aluminium można przetapiać wielokrotnie bez utraty jego podstawowych właściwości.",
    "Stal rozszerza się wraz z temperaturą, więc długie pomiary warsztatowe zależą od warunków otoczenia.",
    "Suwmiarka z noniuszem pozwala odczytać ułamki milimetra bez elektroniki.",
    "Mikrometr wykorzystuje precyzyjny gwint do zamiany obrotu na bardzo mały przesuw.",
    "Cięcie gilotynowe dzieli prostokąt od jednej krawędzi do przeciwległej bez zatrzymywania linii w środku.",
    "Duża prostokątna resztka jest zwykle cenniejsza od kilku skrawków o tej samej łącznej powierzchni.",
    "W produkcji seryjnej kolejność cięć może oszczędzić więcej czasu niż niewielka poprawa wykorzystania materiału.",
    "Twardość i odporność na pękanie to różne cechy materiału; bardzo twardy materiał może być kruchy.",
    "Poliamid pochłania wilgoć z powietrza, co może wpływać na jego wymiary.",
    "POM ma małe tarcie i dobrą stabilność wymiarową, dlatego często trafia do precyzyjnych elementów maszyn.",
    "PE1000 ma bardzo dużą odporność na ścieranie i niski współczynnik tarcia.",
    "Ślad narzędzia zależy nie tylko od posuwu, ale też od liczby zębów i prędkości obrotowej.",
    "Za mały posuw może przegrzewać tworzywo zamiast poprawiać jakość krawędzi.",
    "Ostry nóż skrawa z mniejszą siłą, ale nadal wymaga stabilnego prowadzenia materiału.",
    "Jednostka megapaskal opisuje naprężenie równe milionowi niutonów na metr kwadratowy.",
    "Moduł Younga mówi, jak mocno materiał odkształca się sprężyście pod obciążeniem.",
    "Współczynnik tarcia nie ma jednostki, bo jest stosunkiem dwóch sił.",
    "Fazowanie krawędzi usuwa ostry narożnik i ułatwia montaż części.",
    "Grat to niepożądany materiał pozostający na krawędzi po cięciu lub wierceniu.",
    "Tolerancja określa dopuszczalny zakres wymiaru, a nie pojedynczą idealną wartość.",
    "Pasowanie otworu i wałka może być luźne, przejściowe albo ciasne.",
    "Błąd systematyczny przesuwa wszystkie pomiary podobnie; powtarzanie pomiaru go nie usuwa.",
    "Kalibracja porównuje przyrząd z wzorcem o znanej dokładności.",
    "Ciepło podczas skrawania powstaje głównie przez odkształcanie materiału i tarcie.",
    "Chłodziwo może jednocześnie odbierać ciepło, smarować i wypłukiwać wióry.",
    "Wiór ciągły i wiór łamany wymagają innego prowadzenia oraz zabezpieczenia stanowiska.",
    "Wyważenie obracającego się narzędzia ogranicza drgania i poprawia jakość powierzchni.",
    "Rezonans pojawia się, gdy wymuszenie trafia blisko częstotliwości własnej układu.",
    "Żebrowanie elementu może znacznie zwiększyć sztywność przy niewielkim wzroście masy.",
    "Moment bezwładności przekroju silnie zależy od odsunięcia materiału od osi obojętnej.",
    "Ta sama ilość materiału w profilu zamkniętym bywa sztywniejsza niż w pełnym pręcie.",
    "Powierzchnia referencyjna powinna być ustalona przed kolejnymi operacjami pomiarowymi.",
    "Łańcuch wymiarowy pokazuje, jak tolerancje wielu części sumują się w zespole.",
    "Numer partii umożliwia powiązanie gotowego detalu z materiałem i parametrami procesu.",
    "Kod QR może pomieścić dane dzięki korekcji błędów, nawet gdy fragment symbolu jest uszkodzony.",
    "Metoda 5S porządkuje stanowisko pracy przez selekcję, systematykę, sprzątanie, standaryzację i samodyscyplinę.",
    "Poka-yoke to rozwiązanie konstrukcyjne, które utrudnia albo uniemożliwia popełnienie błędu.",
    "SMED skraca przezbrojenia przez przenoszenie czynności poza czas postoju maszyny.",
    "Wąskie gardło wyznacza maksymalną przepustowość całego procesu.",
    "Bufor przed wąskim gardłem chroni produkcję przed krótkimi zakłóceniami wcześniejszych operacji.",
    "OEE łączy dostępność maszyny, jej wydajność i jakość wykonanych produktów.",
    "Histogram pokazuje rozkład wyników, którego sama średnia nie potrafi ujawnić.",
    "Mediana jest odporna na pojedyncze skrajne wyniki bardziej niż średnia arytmetyczna.",
    "Algorytm zachłanny podejmuje najlepszą lokalną decyzję, ale nie zawsze znajduje najlepszy wynik globalny.",
    "Optymalizacja wielokryterialna wymaga ustalenia, które cele są ważniejsze i w jakiej kolejności.",
    "Przeszukiwanie równoległe pozwala oceniać niezależne warianty jednocześnie na wielu rdzeniach procesora.",
    "Test regresyjny pilnuje, aby naprawiony wcześniej przypadek nie zepsuł się po kolejnej zmianie.",
    "Powtarzalny wynik optymalizatora ułatwia porównywanie scoringu i diagnozowanie zmian algorytmu.",
)

# A world-facts deck rather than workshop hints.  The overlay walks this as a
# shuffle bag, so each fact appears once before a new random cycle begins.
_WORLD_FACTS: tuple[str, ...] = (
    "Miód potrafi przetrwać tysiące lat, jeśli jest szczelnie przechowywany.",
    "Woda może jednocześnie zamarzać i wrzeć w warunkach punktu potrójnego.",
    "Najgłębszy znany punkt oceanów leży w Rowie Mariańskim.",
    "Pustynia Sahara bywała w przeszłości zielonym, wilgotnym regionem.",
    "Wenus obraca się tak wolno, że jej dzień trwa dłużej niż rok.",
    "Światło ze Słońca dociera do Ziemi w około osiem minut i dwadzieścia sekund.",
    "Księżyc oddala się od Ziemi o kilka centymetrów rocznie.",
    "Antarktyda jest największą pustynią na Ziemi według sumy opadów.",
    "Najdłuższą rzeką świata nazywa się zwykle Nil albo Amazonkę, zależnie od metody pomiaru.",
    "Wielka Rafa Koralowa jest widoczna z orbity tylko w sprzyjających warunkach obserwacji.",
    "Błyskawica może nagrzać powietrze do temperatury wyższej niż powierzchnia Słońca.",
    "Dźwięk w wodzie rozchodzi się znacznie szybciej niż w powietrzu.",
    "Największa góra Układu Słonecznego, Olympus Mons, znajduje się na Marsie.",
    "Saturn ma tak małą średnią gęstość, że w dostatecznie wielkim oceanie mógłby pływać.",
    "Jowisz ma burzę zwaną Wielką Czerwoną Plamą, obserwowaną od stuleci.",
    "Na Merkurym jeden pełny dzień słoneczny trwa dwa merkuryjskie lata.",
    "Ziemia nie jest idealną kulą, lecz jest lekko spłaszczona przy biegunach.",
    "W atmosferze Ziemi najwięcej jest azotu, a nie tlenu.",
    "Najstarsze znane drzewa rosną od tysięcy lat.",
    "Wielki Mur Chiński nie jest wyraźnie widoczny gołym okiem z Księżyca.",
    "Pierwsze mapy gwiazd powstawały tysiące lat przed wynalezieniem teleskopu.",
    "Biblioteka Aleksandryjska była centrum badań, a nie pojedynczym regałem z księgami.",
    "Papier wynaleziono w Chinach ponad dwa tysiące lat temu.",
    "Druk z ruchomą czcionką rozwijał się w Azji przed europejskim drukiem Gutenberga.",
    "Najstarsze znane malowidła jaskiniowe mają dziesiątki tysięcy lat.",
    "Machu Picchu leży wysoko w Andach, ponad dwa kilometry nad poziomem morza.",
    "Piramidy w Gizie były już starożytne dla Kleopatry.",
    "Kanał Panamski łączy Ocean Atlantycki i Spokojny przez system śluz.",
    "Pierwszy lot samolotem braci Wright trwał krócej niż minuta.",
    "Pierwsze zdjęcie Ziemi z kosmosu wykonano w 1946 roku.",
    "Międzynarodowa Stacja Kosmiczna okrąża Ziemię mniej więcej co dziewięćdziesiąt minut.",
    "Na pokładzie stacji kosmicznej wschód Słońca można obserwować wielokrotnie w ciągu doby.",
    "Czerwona barwa zachodu Słońca wynika z rozpraszania światła w atmosferze.",
    "Tęcza jest pełnym okręgiem, choć z ziemi zwykle widzimy tylko jej łuk.",
    "Śnieg może skrzypieć pod butami, gdy kryształki lodu pękają na mrozie.",
    "Nie wszystkie jeziora są słodkowodne; wiele z nich ma bardzo wysokie zasolenie.",
    "Morze Martwe jest jeziorem, mimo że jego nazwa sugeruje morze.",
    "Najwyższy wodospad świata, Salto Ángel, leży w Wenezueli.",
    "Jezioro Bajkał zawiera największą objętość ciekłej słodkiej wody powierzchniowej.",
    "Islandia leży na styku płyt tektonicznych i ma liczne źródła geotermalne.",
    "Himalaje nadal powoli rosną, ponieważ zderzają się tam płyty kontynentalne.",
    "Trzęsienia ziemi mierzy się dziś momentem sejsmicznym, nie wyłącznie skalą Richtera.",
    "Wulkaniczne gleby są często bardzo żyzne dzięki minerałom z popiołu.",
    "Największa część świeżej wody na Ziemi jest uwięziona w lodzie i pod ziemią.",
    "Prądy oceaniczne rozprowadzają ciepło między strefami klimatycznymi.",
    "El Niño zmienia temperaturę wód Pacyfiku i wpływa na pogodę w wielu krajach.",
    "Północ magnetyczna nie leży dokładnie w tym samym miejscu co biegun geograficzny.",
    "Kompas wskazuje kierunek pola magnetycznego, a nie automatycznie prawdziwą północ.",
    "Najkrótsza droga między dwoma punktami na globusie zwykle wygląda na mapie jak łuk.",
    "Strefy czasowe są decyzją administracyjną, dlatego nie zawsze biegną idealnie wzdłuż południków.",
    "Na Ziemi istnieją miejsca, w których można przejść przez więcej niż jedną strefę czasową w kilka minut.",
    "Język baskijski nie jest blisko spokrewniony z większością języków Europy.",
    "Alfabet łaciński wywodzi się pośrednio z alfabetu fenickiego.",
    "Najczęściej używanym językiem ojczystym na świecie jest mandaryński.",
    "Braille pozwala odczytywać tekst dotykiem dzięki układom sześciu punktów.",
    "Kod Morse'a zapisuje znaki jako sekwencje krótkich i długich sygnałów.",
    "Pierwszy e-mail wysłano w 1971 roku.",
    "Internet i World Wide Web to nie to samo: sieć WWW jest jedną z usług internetu.",
    "GPS wymaga poprawek relatywistycznych, aby zachować dokładność pozycjonowania.",
    "Satelity nawigacyjne przesyłają bardzo precyzyjne informacje o czasie.",
    "Pierwsze zdjęcia cyfrowe zapisywano długo przed pojawieniem się smartfonów.",
    "Liczba pi ma nieskończone i nieokresowe rozwinięcie dziesiętne.",
    "Zero jako liczba i symbol było jednym z przełomów w historii matematyki.",
    "System metryczny opiera się na jednostkach powiązanych potęgami dziesięciu.",
    "Kilogram był przez lata definiowany przez fizyczny wzorzec przechowywany pod Paryżem.",
    "Sekunda jest obecnie definiowana przez właściwość atomu cezu.",
    "W próżni wszystkie ciała spadają z takim samym przyspieszeniem, gdy pomija się opór.",
    "Promienie światła mogą zakrzywiać się w pobliżu bardzo masywnych obiektów.",
    "Czarne dziury nie zasysają wszystkiego z daleka; ich grawitacja działa jak każda inna przy tej samej masie.",
    "Galaktyka Andromedy zbliża się do Drogi Mlecznej.",
    "Droga Mleczna zawiera setki miliardów gwiazd.",
    "Widzialna część wszechświata ma granicę wynikającą z czasu podróży światła.",
    "Atom jest w większości pustą przestrzenią między jądrem a elektronami.",
    "Złoto jest tak kowalne, że z jednego grama można zrobić bardzo cienki arkusz.",
    "Diament i grafit składają się z węgla, ale mają inną strukturę atomową.",
    "Lód unosi się na wodzie, ponieważ ma mniejszą gęstość niż ciekła woda.",
    "Gorące źródła mogą istnieć pod lodem dzięki energii geotermalnej.",
    "Największe złoża soli powstały po odparowaniu dawnych mórz.",
    "Aurora powstaje, gdy cząstki ze Słońca oddziałują z ziemską atmosferą.",
    "Zaćmienie Słońca jest możliwe dlatego, że Księżyc i Słońce mają podobny pozorny rozmiar na niebie.",
    "Zaćmienie Księżyca może być widoczne z całej nocnej półkuli Ziemi.",
    "Rok przestępny pomaga utrzymać kalendarz w zgodzie z ruchem Ziemi wokół Słońca.",
    "Kalendarz gregoriański pomija część lat setnych jako przestępnych.",
    "Najstarsze działające uniwersytety mają korzenie sięgające średniowiecza.",
    "Oksford był ośrodkiem nauki zanim powstało Imperium Azteków.",
    "Pierwsze nowoczesne igrzyska olimpijskie odbyły się w Atenach w 1896 roku.",
    "Metro w Londynie było pierwszą podziemną koleją miejską na świecie.",
    "Wieża Eiffla miała być początkowo konstrukcją tymczasową.",
    "Opera w Sydney korzysta z dachów złożonych z fragmentów kuli.",
    "Most Golden Gate zawdzięcza kolor widoczności we mgle, nie złotu.",
    "Miasto Stambuł leży jednocześnie w Europie i Azji.",
    "Równik przebiega przez trzynaście państw.",
    "Największy kraj świata rozciąga się przez jedenaście stref czasowych.",
    "Nepal używa czasu przesuniętego o czterdzieści pięć minut względem UTC.",
    "W Japonii istnieje ponad sześć tysięcy wysp.",
    "Nowa Zelandia była jednym z ostatnich dużych obszarów zasiedlonych przez ludzi.",
    "Madagaskar oddzielił się od innych lądów miliony lat temu.",
    "Amazonia wytwarza własną wilgoć dzięki intensywnemu obiegowi wody.",
    "Lasy namorzynowe chronią wybrzeża przed falami i erozją.",
    "Torfowiska magazynują dużo węgla w wilgotnej glebie.",
    "Jedna cząsteczka wody może krążyć między oceanem, chmurą i lodem przez bardzo długi czas.",
    "Część piasku na plażach powstaje z rozdrobnionych skał przynoszonych przez rzeki.",
    "Fale tsunami na otwartym oceanie mogą być niskie, ale bardzo szybkie.",
    "Najwyższe pływy występują tam, gdzie kształt zatoki wzmacnia ruch wody.",
    "Deszcz meteorytów następuje, gdy Ziemia przechodzi przez ślad pyłu pozostawiony przez kometę.",
    "Komety tworzą warkocz skierowany od Słońca, niezależnie od kierunku ich lotu.",
)

# The progress overlay walks all 200 curated transport/history facts as a
# shuffle-bag.  A card cannot repeat until the entire deck has been shown.
_SIMS_MESSAGES = ALL_FACTS

_SAMURAI_FRAMES: tuple[tuple[str, str], ...] = (
    ("01  IDLE STANCE", r"""
                           .-^-.
                        .-'  _  '-.
                       /   .' '.   \
                      /   / .-. \   \
                     |   | (o o) |   |
                     |   |  /_\  |   |
                     |   | /===\ |   |
                    /|    \|===|/    |\
                 .-' |  .--/| |\--.  | '-.
               .'    | /   / | | \   \ |    '.
              /______|/___/  | |  \___\|______\
                     /  _/___| |___\_  \
                    /__/      | |     \__\
                   /  /      _| |_      \  \
                  /__/______/_____|______\__\
_________________/____/_____/     \_____\____\_________________"""),
    ("02  PREPARE", r"""
                                      /|
                           .-^-.     / |
                        .-'  _  '-. /  |
                       /   .' '.   /   |
                      /   / .-. \ /    |
                     |   | (o o) |     |
                     |   |  /_\  |     |
                     |   | /===\ |    / 
                    /|    \|===|/    /|
                 .-' |  .--/| |\--. / | '-.
               .'    | /   / | | \  /  |    '.
              /______|/___/  | |  \/___|______\
                     /  _/___| |___\_  \
                    /__/      | |     \__\
                   /  /      _| |_      \  \
__________________/__/______/_____|______\__\_________________"""),
    ("03  SWING START", r"""
              .  .  .  .  .  .  .  .  .  .
                           .-^-.
                        .-'  _  '-.              /|
                       /   .' '.   \            / |
                      /   / .-. \   \          /  |
                     |   | (o o) |   |        /   |
                     |   |  /_\  |   |       /    |
                     |   | /===\ |   |      /     |
                    /|    \|===|/    |\    /      |
                 .-' |  .--/| |\--.  | '-.       |
               .'    | /   / | | \   \ |    '.    |
              /______|/___/  | |  \___\|______\___|
                     /  _/___| |___\_  \
                    /__/      | |     \__\
___________________/__/______/_____|______\__\________________"""),
    ("04  FAST SLASH", r"""
                              ________________________________
                     ________/================================\=======>
              ----  ----  ----  ----  ----  ----  ----  ----
                           .-^-.
                        .-'  _  '-.
                       /   .' '.   \________________
                      /   / .-. \                    \
                     |   | (o o) |____________________\
                     |   |  /_\  |                     /
                     |   | /===\ |                    /
                    /|    \|===|/    |\              /
                 .-' |  .--/| |\--.  | '-.          /
_______________/_____|/___/_|_|_\___\|______\_______/_________"""),
    ("05  IMPACT", r"""
              =======================================================>
        ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ----
                           .-^-.                         . * .
                        .-'  _  '-.                  . *  |  * .
                       /   .' '.   \                * ----+---- *
                      /   / .-. \   \                 . *  |  * .
                     |   | (o o) |   |                    . * .
                     |   |  /_\  |___|_________________
                     |   | /===\ |                    \
                    /|    \|===|/    |\                \
                 .-' |  .--/| |\--.  | '-.              \
_______________/_____|/___/_|_|_\___\|______\____________\______"""),
    ("06  FOLLOW THROUGH", r"""
             .  .  .  .  .  .  .  .  .  .  .  .  .
                           .-^-.
                        .-'  _  '-.
                       /   .' '.   \                       __
                      /   / .-. \   \                ______/ /
                     |   | (o o) |   |          ____/       /
                     |   |  /_\  |___|______ __/           /
                     |   | /===\ |       /  /             /
                    /|    \|===|/    |\ /  /             /
                 .-' |  .--/| |\--.  | '-/______________/
               .'    | /   / | | \   \ |    '.
______________/______|/___/__|_|__\___\|______\________________"""),
    ("07  SPARK TRAIL", r"""
                           .-^-.
                        .-'  _  '-.                 .  *  .
                       /   .' '.   \            *     . .     *
                      /   / .-. \   \        .    *  /|\  *    .
                     |   | (o o) |   |           .  / | \  .
                     |   |  /_\  |   |       *     /  |  \     *
                     |   | /===\ |   |           /___|___\
                    /|    \|===|/    |\       .     |     .
                 .-' |  .--/| |\--.  | '-.          | 
               .'    | /   / | | \   \ |    '.
              /______|/___/  | |  \___\|______\
_____________/______/_____/___|_|___\_____\____\_______________"""),
    ("08  RETURN TO IDLE", r"""
                           .-^-.
                        .-'  _  '-.
                       /   .' '.   \
                      /   / .-. \   \
                     |   | (o o) |   |
                     |   |  /_\  |   |
                     |   | /===\ |   |
                    /|    \|===|/    |\
                 .-' |  .--/| |\--.  | '-.
               .'    | /   / | | \   \ |    '.
              /______|/___/  | |  \___\|______\
                     /  _/___| |___\_  \
                    /__/      | |     \__\
                   /  /      _| |_      \  \
                  /__/______/_____|______\__\
_________________/____/_____/     \_____\____\_________________"""),
)


# ---------------------------------------------------------------------------
# New Sims-style animation overlay
# ---------------------------------------------------------------------------

class OptimizationProgressOverlay(QWidget):
    """Animated loading overlay shown during optimization.

    Features:
    - Progress bar fed by actual optimizer phases
    - Cycling Sims-style funny descriptions (Polish)
    - Subtle pulsing background
    - Keeps a real elapsed-time counter visible
    """

    finished = Signal()
    cancelled = Signal()

    # seconds between message changes
    _MSG_INTERVAL_S: float = 2.8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calculationOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.state = OptimizationProgressState()

        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

        self._samurai_movie_label = QLabel(self)
        self._samurai_movie_label.setObjectName("samuraiCalculationRender")
        self._samurai_movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._samurai_movie_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._samurai_movie = QMovie(str(_resource_path("assets/animations/samurai_loader.gif")), b"gif", self)
        self._samurai_movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self._samurai_movie_label.setMovie(self._samurai_movie)
        self._samurai_movie_available = self._samurai_movie.isValid()
        self._samurai_movie_label.setVisible(False)
        self._animation_mode = "economy"

        # Message cycling
        self._messages = list(_SIMS_MESSAGES)
        random.shuffle(self._messages)
        self._msg_index: int = 0
        self._msg_label: str = self._messages[0]
        self._msg_phase: float = 0.0  # 0..1 fade-in/hold/fade-out within one message
        self._msg_elapsed_s: float = 0.0

        # Cancel button (positioned in paintEvent companion _reposition_cancel)
        self._cancel_button = QPushButton("Anuluj", self)
        self._cancel_button.setObjectName("overlayCancel")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setFlat(False)
        self._cancel_button.setStyleSheet(
            "QPushButton#overlayCancel {"
            "  background: rgba(220, 38, 38, 0.92);"
            "  color: white;"
            "  border: 1px solid rgba(255, 255, 255, 0.18);"
            "  border-radius: 9px;"
            "  padding: 7px 22px;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "  letter-spacing: 0.6px;"
            "}"
            "QPushButton#overlayCancel:hover {"
            "  background: rgba(239, 68, 68, 0.98);"
            "}"
            "QPushButton#overlayCancel:pressed {"
            "  background: rgba(185, 28, 28, 1.0);"
            "}"
            "QPushButton#overlayCancel:disabled {"
            "  background: rgba(120, 120, 120, 0.55);"
            "  color: rgba(255, 255, 255, 0.55);"
            "}"
        )
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.hide()

        self.hide()

    # ------------------------------------------------------------------
    def _on_cancel_clicked(self) -> None:
        self._cancel_button.setDisabled(True)
        self._cancel_button.setText("Anulowanie...")
        self.state.currentStageLabel = "Anulowanie..."
        self.cancelled.emit()
        self.update()

    def _reposition_cancel(self) -> None:
        bw = max(120, self._cancel_button.sizeHint().width() + 24)
        bh = max(28, self._cancel_button.sizeHint().height() + 6)
        bx = int(self.width() / 2 - bw / 2)
        by = int(self.height() * 0.84)
        self._cancel_button.setGeometry(bx, by, bw, bh)
        self._cancel_button.raise_()

    def _reposition_samurai_movie(self) -> None:
        if not self._samurai_movie_available or self._animation_mode != "quality":
            return
        target_w = min(600, max(320, int(self.width() * 0.58)))
        target_h = int(target_w * 400 / 640)
        x = int((self.width() - target_w) / 2)
        y = max(24, int(self.height() * 0.08))
        self._samurai_movie_label.setGeometry(x, y, target_w, target_h)
        self._samurai_movie.setScaledSize(QSize(target_w, target_h))
        self._samurai_movie_label.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition_cancel()
        self._reposition_samurai_movie()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reposition_cancel()
        self._reposition_samurai_movie()

    # ------------------------------------------------------------------
    def start(self, project: object | None = None, duration_ms: int = 1100) -> None:
        self._elapsed.restart()
        self._msg_elapsed_s = 0.0
        self._msg_phase = 0.0
        self.state.currentStageLabel = "Uruchamiam proces obliczania"
        # Continue through the persistent shuffle-bag instead of reshuffling and
        # restarting at index 0 every run.  Because a typical optimization lasts
        # only a few seconds, the user mostly sees the *first* message of each
        # run; advancing the deck here guarantees a fresh fact every time and no
        # repeats until the whole deck has been shown.
        self._advance_message()
        requested_animation = str(
            getattr(getattr(project, "settings", None), "animation_mode", self._animation_mode)
        )
        self._animation_mode = (
            requested_animation
            if requested_animation in {"economy", "quality"}
            else "economy"
        )
        if project and not getattr(project.settings, "multi_core", True):
            self._msg_label = "Uwaga: Wielowątkowość jest wyłączona. Proces obliczania się wydłuży."
            # Set the phase slightly forward so it holds longer before fading out
            self._msg_phase = 0.0
        
        self.state.visible = True
        self.state.globalProgressPercent = 0
        self._cancel_button.setDisabled(False)
        self._cancel_button.setText("Anuluj")
        self._cancel_button.show()
        if self._animation_mode == "quality" and self._samurai_movie_available:
            self._reposition_samurai_movie()
            self._samurai_movie_label.show()
            self._samurai_movie.start()
        else:
            self._samurai_movie.stop()
            self._samurai_movie_label.hide()
        self.show()
        self.raise_()
        self._reposition_cancel()
        self._timer.start()
        self.update()

    def set_progress(self, percent: int, label: str = "") -> None:
        """Receive a real phase update from the calculation process."""
        bounded = max(0, min(100, int(percent)))
        self.state.globalProgressPercent = max(self.state.globalProgressPercent, bounded)
        if label:
            self.state.currentStageLabel = label
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._samurai_movie.stop()
        self._samurai_movie_label.hide()
        self.state.visible = False
        self._cancel_button.hide()
        self.hide()
        self.update()

    def finish(self, delay_ms: int = 320) -> None:
        self._timer.stop()
        self._samurai_movie.stop()
        self._samurai_movie_label.hide()
        self.state.currentStageLabel = "Gotowe!"
        self.state.globalProgressPercent = 100
        self._cancel_button.hide()
        self.update()
        QTimer.singleShot(max(0, int(delay_ms)), self.stop)

    # ------------------------------------------------------------------
    def _advance_message(self) -> None:
        """Move to the next fact in a persistent shuffle-bag.

        The deck is walked card by card; only when it is exhausted do we
        reshuffle.  After reshuffling we make sure the new first card differs
        from the one just shown, so a fact never appears twice in a row across
        the wrap.  This guarantees every fact is shown once before any repeat.
        """
        if not self._messages:
            return
        self._msg_index += 1
        if self._msg_index >= len(self._messages):
            last = self._msg_label
            random.shuffle(self._messages)
            if len(self._messages) > 1 and self._messages[0] == last:
                # Push the duplicate to the back to avoid a back-to-back repeat.
                self._messages.append(self._messages.pop(0))
            self._msg_index = 0
        self._msg_label = self._messages[self._msg_index]

    def _tick(self) -> None:
        elapsed_ms = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        elapsed_s = elapsed_ms / 1000.0

        # Indeterminate bar: cycles 0→1 in ~2.4 s

        # Cycle messages
        self._msg_elapsed_s += 0.016  # ~16 ms per tick
        if self._msg_elapsed_s >= self._MSG_INTERVAL_S:
            self._msg_elapsed_s -= self._MSG_INTERVAL_S
            self._advance_message()

        # Within-message fade phase (0→0.15 fade-in, 0.15→0.85 hold, 0.85→1 fade-out)
        self._msg_phase = min(1.0, self._msg_elapsed_s / self._MSG_INTERVAL_S)

        self.update()

    # ------------------------------------------------------------------
    def _msg_alpha(self) -> float:
        """Smooth opacity for current message label."""
        p = self._msg_phase
        if p < 0.12:
            return p / 0.12
        if p > 0.88:
            return (1.0 - p) / 0.12
        return 1.0

    def _paint_economy_loader(self, painter: QPainter, bounds: QRectF, elapsed_s: float, fade_in: float, is_light: bool) -> None:
        """A restrained Windows-style boot loader made from orbiting dots."""
        center = QPointF(bounds.center().x(), bounds.height() * 0.36)
        orbit = max(34.0, min(bounds.width(), bounds.height()) * 0.078)
        linear_cycle = (elapsed_s * 0.58) % 1.0
        blue = QColor(99, 177, 255) if is_light else QColor(103, 190, 255)

        # Five dots follow the same circle with a delayed, accelerating tail.
        # The stagger is what gives the familiar Windows boot rhythm.
        for index in range(5):
            raw_position = (linear_cycle - index * 0.14) % 1.0
            # Apply the wave after each dot's delay. This expands the gaps at
            # the fast front and compresses the slower tail behind it.
            position = (raw_position - 0.06 * math.sin(raw_position * math.tau)) % 1.0
            angle = -math.pi / 2 + position * math.tau
            ease = _ease_out_quint(position)
            velocity = (1.0 - math.cos(raw_position * math.tau)) / 2.0
            fast = max(0.0, min(1.0, (velocity - 0.58) / 0.42))
            fast = fast * fast * (3.0 - 2.0 * fast)
            wave = 0.5 + 0.5 * math.sin(position * math.tau - 0.7)
            x = center.x() + math.cos(angle) * orbit
            y = center.y() + math.sin(angle) * orbit
            radius = 3.1 + ease * 1.45 + wave * 0.35
            stretch = 1.0 + fast * 0.72
            thickness = 1.0 - fast * 0.18
            alpha = int(230 * fade_in)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(blue.red(), blue.green(), blue.blue(), alpha))
            painter.save()
            painter.translate(x, y)
            painter.rotate(math.degrees(angle + math.pi / 2.0))
            painter.drawEllipse(QRectF(-radius * stretch, -radius * thickness, radius * stretch * 2.0, radius * thickness * 2.0))
            painter.restore()

    def _paint_economy_loader_metaball_legacy(self, painter: QPainter, bounds: QRectF, elapsed_s: float, fade_in: float, is_light: bool) -> None:
        """Two glowing metaball pairs with a slow, liquid bloom loop."""
        if not self._metaball_bloom.isNull():
            source = QRectF(
                self._metaball_bloom.width() * 0.14,
                self._metaball_bloom.height() * 0.10,
                self._metaball_bloom.width() * 0.72,
                self._metaball_bloom.height() * 0.72,
            )
            target_size = min(bounds.width() * 0.47, bounds.height() * 0.56)
            breath = 1.0 + 0.025 * math.sin(elapsed_s * math.tau / 4.8)
            rotation = 2.2 * math.sin(elapsed_s * math.tau / 8.0)
            center = QPointF(bounds.center().x(), bounds.height() * 0.34)
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            painter.setOpacity((0.96 if not is_light else 0.78) * fade_in)
            painter.translate(center)
            painter.rotate(rotation)
            draw_size = target_size * breath
            target = QRectF(-draw_size / 2.0, -draw_size / 2.0, draw_size, draw_size)
            painter.drawPixmap(target, self._metaball_bloom, source)
            painter.setOpacity((0.42 if not is_light else 0.24) * fade_in)
            bloom_target = target.adjusted(-3.0, -3.0, 3.0, 3.0)
            painter.drawPixmap(bloom_target, self._metaball_bloom, source)
            painter.restore()
            return

        scene = QRectF(
            bounds.center().x() - min(bounds.width(), bounds.height()) * 0.27,
            bounds.height() * 0.12,
            min(bounds.width(), bounds.height()) * 0.54,
            min(bounds.width(), bounds.height()) * 0.46,
        )
        center = scene.center()
        radius = max(36.0, scene.width() * 0.115)
        phase = elapsed_s * 0.58

        cyan = QColor(50, 217, 255) if not is_light else QColor(24, 132, 222)
        violet = QColor(141, 92, 255) if not is_light else QColor(99, 76, 218)

        def blob_path_legacy(first: QPointF, second: QPointF, first_radius: float, second_radius: float) -> QPainterPath:
            dx = second.x() - first.x()
            dy = second.y() - first.y()
            distance = max(1.0, math.hypot(dx, dy))
            vx, vy = dx / distance, dy / distance
            nx, ny = -vy, vx
            path = QPainterPath()
            path.moveTo(first.x() + nx * first_radius, first.y() + ny * first_radius)
            path.cubicTo(
                first.x() + vx * distance * 0.42 + nx * first_radius * 0.72,
                first.y() + vy * distance * 0.42 + ny * first_radius * 0.72,
                second.x() - vx * distance * 0.42 + nx * second_radius * 0.72,
                second.y() - vy * distance * 0.42 + ny * second_radius * 0.72,
                second.x() + nx * second_radius,
                second.y() + ny * second_radius,
            )
            path.cubicTo(
                second.x() + vx * second_radius * 0.92 + nx * second_radius * 0.34,
                second.y() + vy * second_radius * 0.92 + ny * second_radius * 0.34,
                second.x() + vx * second_radius * 0.92 - nx * second_radius * 0.34,
                second.y() + vy * second_radius * 0.92 - ny * second_radius * 0.34,
                second.x() - nx * second_radius,
                second.y() - ny * second_radius,
            )
            path.cubicTo(
                second.x() - vx * distance * 0.42 - nx * second_radius * 0.72,
                second.y() - vy * distance * 0.42 - ny * second_radius * 0.72,
                first.x() + vx * distance * 0.42 - nx * first_radius * 0.72,
                first.y() + vy * distance * 0.42 - ny * first_radius * 0.72,
                first.x() - nx * first_radius,
                first.y() - ny * first_radius,
            )
            path.cubicTo(
                first.x() - vx * first_radius * 0.92 - nx * first_radius * 0.34,
                first.y() - vy * first_radius * 0.92 - ny * first_radius * 0.34,
                first.x() - vx * first_radius * 0.92 + nx * first_radius * 0.34,
                first.y() - vy * first_radius * 0.92 + ny * first_radius * 0.34,
                first.x() + nx * first_radius,
                first.y() + ny * first_radius,
            )
            return path

        def blob_path(first: QPointF, second: QPointF, first_radius: float, second_radius: float) -> QPainterPath:
            """Union two rounded lobes and a narrow bridge into one smooth silhouette."""
            dx = second.x() - first.x()
            dy = second.y() - first.y()
            distance = max(1.0, math.hypot(dx, dy))
            nx, ny = -dy / distance, dx / distance
            neck = min(first_radius, second_radius) * 0.72
            bridge = QPainterPath()
            bridge.moveTo(first.x() + nx * neck, first.y() + ny * neck)
            bridge.lineTo(second.x() + nx * neck, second.y() + ny * neck)
            bridge.lineTo(second.x() - nx * neck, second.y() - ny * neck)
            bridge.lineTo(first.x() - nx * neck, first.y() - ny * neck)
            bridge.closeSubpath()
            lobes = QPainterPath()
            lobes.addEllipse(first, first_radius, first_radius)
            lobes.addEllipse(second, second_radius, second_radius)
            return lobes.united(bridge).simplified()

        def draw_pair(pair_phase: float, pair_scale: float, offset_x: float, offset_y: float, reverse: bool) -> None:
            angle = pair_phase + (math.pi if reverse else 0.0)
            wobble = math.sin(elapsed_s * 1.3 + pair_phase) * radius * 0.18
            pair_center = QPointF(
                center.x() + offset_x + math.cos(angle) * radius * 0.10,
                center.y() + offset_y + math.sin(angle * 1.2) * radius * 0.08,
            )
            direction = angle + (0.92 if reverse else -0.92)
            separation = radius * (1.30 + 0.10 * math.sin(elapsed_s * 1.05 + pair_phase))
            first = QPointF(
                pair_center.x() - math.cos(direction) * separation * 0.5,
                pair_center.y() - math.sin(direction) * separation * 0.5 + wobble,
            )
            second = QPointF(
                pair_center.x() + math.cos(direction) * separation * 0.5,
                pair_center.y() + math.sin(direction) * separation * 0.5 - wobble,
            )
            first_radius = radius * pair_scale * (0.95 + 0.10 * math.sin(elapsed_s * 1.16 + pair_phase))
            second_radius = radius * pair_scale * (1.02 + 0.10 * math.cos(elapsed_s * 0.97 + pair_phase))
            path = blob_path(first, second, first_radius, second_radius)

            glow = QRadialGradient(pair_center, radius * 3.2)
            glow.setColorAt(0.0, QColor(cyan.red(), cyan.green(), cyan.blue(), int(74 * fade_in)))
            glow.setColorAt(0.56, QColor(violet.red(), violet.green(), violet.blue(), int(24 * fade_in)))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(pair_center, radius * 3.0, radius * 3.0)

            gradient = QLinearGradient(first, second)
            gradient.setColorAt(0.0, QColor(violet.red(), violet.green(), violet.blue(), int(238 * fade_in)))
            gradient.setColorAt(0.42, QColor(111, 143, 255, int(250 * fade_in)))
            gradient.setColorAt(1.0, QColor(cyan.red(), cyan.green(), cyan.blue(), int(240 * fade_in)))
            painter.setBrush(gradient)
            painter.setPen(QColor(194, 244, 255, int(180 * fade_in)))
            painter.drawPath(path)

            highlight = QRadialGradient(second, second_radius * 1.10)
            highlight.setColorAt(0.0, QColor(255, 255, 255, int(105 * fade_in)))
            highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight)
            painter.drawEllipse(second, second_radius * 1.10, second_radius * 1.10)

        draw_pair(phase, 0.96, -scene.width() * 0.12, -scene.height() * 0.16, False)
        draw_pair(phase + 1.62, 0.92, scene.width() * 0.12, scene.height() * 0.16, True)

    def _paint_economy_loader_constellation(self, painter: QPainter, bounds: QRectF, elapsed_s: float, fade_in: float, is_light: bool) -> None:
        """A light, looping field of luminous nodes that merge and split."""
        center = QPointF(bounds.center().x(), bounds.height() * 0.36)
        orbit = max(112.0, min(bounds.width(), bounds.height()) * 0.26)
        palette = (
            QColor(68, 190, 255), QColor(72, 232, 190), QColor(178, 114, 255),
            QColor(203, 244, 112), QColor(255, 108, 212), QColor(82, 126, 255),
        )
        if is_light:
            palette = tuple(
                QColor(int(color.red() * 0.70), int(color.green() * 0.70), int(color.blue() * 0.78))
                for color in palette
            )

        loop = (elapsed_s % 6.4) / 6.4
        collision = math.exp(-((loop - 0.50) / 0.085) ** 2)

        # Soft Bezier trails keep the field organic while remaining cheap to draw.
        for index, color in enumerate(palette):
            phase = index * math.tau / len(palette) + elapsed_s * (0.22 + (index % 2) * 0.025)
            path = QPainterPath()
            path.moveTo(
                center.x() + math.cos(phase) * orbit * 0.86,
                center.y() + math.sin(phase * 1.32) * orbit * 0.56,
            )
            path.cubicTo(
                center.x() + math.sin(phase + 0.7) * orbit * 0.26,
                center.y() - math.cos(phase * 1.1) * orbit * 0.92,
                center.x() - math.cos(phase * 0.8) * orbit * 0.78,
                center.y() + math.sin(phase + 1.4) * orbit * 0.76,
                center.x() + math.cos(phase + 2.3) * orbit * 0.88,
                center.y() + math.sin(phase * 1.26 + 1.2) * orbit * 0.56,
            )
            painter.setPen(QColor(color.red(), color.green(), color.blue(), int(44 * fade_in)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        positions: list[QPointF] = []
        for index in range(6):
            speed = 0.47 if index == 0 else 0.72 + index * 0.022
            phase = elapsed_s * speed + index * math.tau / 6.0
            positions.append(QPointF(
                center.x() + math.cos(phase) * orbit * (0.76 + 0.10 * math.sin(phase * 1.7)),
                center.y() + math.sin(phase * 1.27) * orbit * 0.62,
            ))

        # Two focal points meet in the middle, grow into a flare, then split.
        approach = max(0.0, 1.0 - abs(loop - 0.50) / 0.30)
        offset = orbit * 0.76 * (1.0 - approach)
        drift = math.sin(loop * math.tau) * orbit * 0.20
        positions[0] = QPointF(center.x() - offset, center.y() + drift)
        positions[1] = QPointF(center.x() + offset, center.y() - drift)

        def glow(point: QPointF, color: QColor, radius: float) -> None:
            gradient = QRadialGradient(point, radius * 3.0)
            gradient.setColorAt(0.0, QColor(255, 255, 255, int(255 * fade_in)))
            gradient.setColorAt(0.16, QColor(color.red(), color.green(), color.blue(), int(235 * fade_in)))
            gradient.setColorAt(0.54, QColor(color.red(), color.green(), color.blue(), int(56 * fade_in)))
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(point, radius * 3.0, radius * 3.0)
            painter.setBrush(QColor(255, 255, 255, int(230 * fade_in)))
            painter.drawEllipse(point, max(1.4, radius * 0.30), max(1.4, radius * 0.30))

        for index, point in enumerate(positions):
            radius = 4.5 + 1.6 * math.sin(elapsed_s * 1.2 + index) ** 2
            if index in (0, 1):
                radius += collision * 8.0
            glow(point, palette[index], radius)

        if collision > 0.02:
            flare = orbit * (0.16 + collision * 0.20)
            painter.setPen(QColor(235, 250, 255, int(160 * collision * fade_in)))
            painter.drawLine(QPointF(center.x() - flare, center.y()), QPointF(center.x() + flare, center.y()))
            painter.drawLine(QPointF(center.x(), center.y() - flare), QPointF(center.x(), center.y() + flare))

    def _paint_economy_loader_legacy(self, painter: QPainter, bounds: QRectF, elapsed_s: float, fade_in: float, is_light: bool) -> None:
        """A calm, organic cosmic loader with a subtle katana constellation."""
        center = QPointF(bounds.center().x(), bounds.height() * 0.36)
        orbit = max(76.0, min(bounds.width(), bounds.height()) * 0.145)
        sky = QColor(37, 99, 235) if is_light else QColor(112, 205, 255)
        violet = QColor(124, 92, 255) if is_light else QColor(176, 140, 255)

        # Three loose orbital paths give the animation a breathing, hand-drawn
        # rhythm instead of a mechanical spinner.
        for arm in range(3):
            phase = elapsed_s * (0.32 + arm * 0.035) + arm * 2.15
            path = QPainterPath()
            path.moveTo(center.x() - orbit * 0.92, center.y() + math.sin(phase) * orbit * 0.26)
            path.cubicTo(
                center.x() - orbit * 0.28,
                center.y() - orbit * (0.98 + 0.10 * math.cos(phase)),
                center.x() + orbit * 0.48,
                center.y() + orbit * (0.90 + 0.08 * math.sin(phase)),
                center.x() + orbit * 1.02,
                center.y() - math.cos(phase) * orbit * 0.24,
            )
            color = sky if arm != 1 else violet
            painter.setPen(QColor(color.red(), color.green(), color.blue(), int(92 * fade_in)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        for index in range(9):
            phase = (elapsed_s * 0.21 + index / 9.0) % 1.0
            angle = phase * math.tau + index * 0.29
            radius = orbit * (0.54 + 0.43 * math.sin(index * 1.71 + elapsed_s * 0.22) ** 2)
            x = center.x() + math.cos(angle) * radius
            y = center.y() + math.sin(angle) * radius * 0.62
            size = 2.2 + 3.2 * (0.5 + 0.5 * math.sin(elapsed_s * 1.8 + index))
            color = sky if index % 3 else violet
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), int((145 + index * 11) * fade_in)))
            painter.drawEllipse(QPointF(x, y), size, size)

        # Crescent and a tiny diagonal katana form a restrained samurai mark.
        moon_r = orbit * 0.29
        painter.setPen(QColor(sky.red(), sky.green(), sky.blue(), int(190 * fade_in)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(center.x() - moon_r, center.y() - moon_r, moon_r * 2, moon_r * 2), 38 * 16, 278 * 16)
        painter.setPen(QColor(235, 248, 255, int(220 * fade_in)) if not is_light else QColor(30, 64, 175, int(220 * fade_in)))
        painter.drawLine(
            QPointF(center.x() - moon_r * 0.72, center.y() + moon_r * 0.50),
            QPointF(center.x() + moon_r * 0.67, center.y() - moon_r * 0.58),
        )
        painter.setPen(QColor(220, 38, 38, int(210 * fade_in)))
        painter.drawLine(
            QPointF(center.x() - moon_r * 0.18, center.y() + moon_r * 0.08),
            QPointF(center.x() + moon_r * 0.24, center.y() + moon_r * 0.08),
        )

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bounds = QRectF(self.rect())
        if bounds.isEmpty():
            return

        elapsed_ms = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        elapsed_s = elapsed_ms / 1000.0
        fade_in = min(1.0, elapsed_s / 0.32)  # 320 ms fade-in

        theme = (
            QApplication.instance().property("theme")
            if QApplication.instance()
            else "dark"
        )
        is_light = theme == "light"

        # ----------------------------------------------------------------
        # Background
        # ----------------------------------------------------------------
        if is_light:
            painter.fillRect(bounds, QColor(245, 248, 255, int(252 * fade_in)))
        else:
            painter.fillRect(bounds, QColor(5, 10, 22, int(252 * fade_in)))

        # Subtle radial vignette
        vignette = QRadialGradient(bounds.center(), max(bounds.width(), bounds.height()) * 0.7)
        if is_light:
            vignette.setColorAt(0.0, QColor(15, 40, 90, int(28 * fade_in)))
            vignette.setColorAt(1.0, QColor(15, 40, 90, 0))
        else:
            vignette.setColorAt(0.0, QColor(60, 120, 255, int(22 * fade_in)))
            vignette.setColorAt(1.0, QColor(60, 120, 255, 0))
        painter.fillRect(bounds, vignette)

        # ----------------------------------------------------------------
        # Use the prepared video render when available. ASCII remains as a
        # self-contained fallback for source-only or damaged installations.
        # ----------------------------------------------------------------
        cx = bounds.center().x()
        if self._animation_mode == "economy":
            self._paint_economy_loader(painter, bounds, elapsed_s, fade_in, is_light)
        elif not self._samurai_movie_available:
            frame_index = int(elapsed_s / 0.16) % len(_SAMURAI_FRAMES)
            frame_title, frame_art = _SAMURAI_FRAMES[frame_index]
            sampler_font_size = max(9, min(16, int(bounds.width() / 72)))
            samurai_font = QFont("Cascadia Mono", sampler_font_size)
            samurai_font.setStyleHint(QFont.StyleHint.Monospace)
            samurai_font.setWeight(QFont.Weight.DemiBold)
            phase_font = QFont("Cascadia Mono", max(8, sampler_font_size - 4))
            phase_font.setStyleHint(QFont.StyleHint.Monospace)
            phase_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            painter.setFont(phase_font)
            painter.setOpacity(0.72 * fade_in)
            painter.setPen(QColor(25, 100, 160) if is_light else QColor(94, 201, 255))
            painter.drawText(
                QRectF(bounds.x() + 30, bounds.height() * 0.12, bounds.width() - 60, 22),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                f"[ {frame_title} ]",
            )
            painter.setFont(samurai_font)
            painter.setOpacity(0.96 * fade_in)
            painter.setPen(QColor(20, 45, 100) if is_light else QColor(215, 230, 255))
            samurai_rect = QRectF(
                bounds.x() + 28,
                bounds.height() * 0.16,
                bounds.width() - 56,
                bounds.height() * 0.43,
            )
            painter.drawText(
                samurai_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                frame_art,
            )
            painter.setOpacity(1.0)

        # ----------------------------------------------------------------
        # Progress bar
        # ----------------------------------------------------------------
        bar_w = min(bounds.width() * 0.62, 380.0)
        bar_h = 6.0
        bar_x = cx - bar_w / 2.0
        bar_y = bounds.height() * 0.66

        # Track
        track_col = (
            QColor(180, 195, 220, int(70 * fade_in))
            if is_light
            else QColor(255, 255, 255, int(28 * fade_in))
        )
        painter.setBrush(track_col)
        painter.setPen(Qt.PenStyle.NoPen)
        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)
        painter.drawPath(track_path)

        # Fill (indeterminate sweep)
        fill_frac = self.state.globalProgressPercent / 100.0
        fill_w = bar_w * fill_frac

        # Shimmer effect on the fill
        shimmer_pos = (elapsed_s * 0.55) % 1.4 - 0.2  # -0.2 .. 1.2
        shimmer_center = max(0.0, min(1.0, shimmer_pos))
        shimmer_left = max(0.0, shimmer_center - 0.15)
        shimmer_right = min(1.0, shimmer_center + 0.15)
        fill_grad = QLinearGradient(bar_x, bar_y, bar_x + fill_w, bar_y)
        if is_light:
            fill_grad.setColorAt(0.0, QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(shimmer_left, QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(shimmer_center, QColor(100, 160, 255, int(240 * fade_in)))
            fill_grad.setColorAt(shimmer_right, QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(1.0, QColor(30, 80, 200, int(200 * fade_in)))
        else:
            fill_grad.setColorAt(0.0, QColor(60, 120, 255, int(220 * fade_in)))
            fill_grad.setColorAt(shimmer_left, QColor(60, 120, 255, int(220 * fade_in)))
            fill_grad.setColorAt(shimmer_center, QColor(160, 210, 255, int(255 * fade_in)))
            fill_grad.setColorAt(shimmer_right, QColor(60, 120, 255, int(220 * fade_in)))
            fill_grad.setColorAt(1.0, QColor(60, 120, 255, int(220 * fade_in)))

        if fill_w > bar_h:
            painter.setBrush(fill_grad)
            painter.setPen(Qt.PenStyle.NoPen)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), bar_h / 2, bar_h / 2)
            painter.drawPath(fill_path)

        # Percent label right of bar
        pct_text = f"{self.state.globalProgressPercent}%"
        pct_font = QFont("Segoe UI", 8)
        pct_font.setWeight(QFont.Weight.Bold)
        painter.setFont(pct_font)
        painter.setOpacity(0.82 * fade_in)
        painter.setPen(
            QColor(30, 50, 100) if is_light else QColor(160, 200, 255)
        )
        pct_rect = QRectF(bar_x + bar_w + 8, bar_y - 3, 36, bar_h + 6)
        painter.drawText(pct_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pct_text)

        # ----------------------------------------------------------------
        # Elapsed time (small, below bar)
        # ----------------------------------------------------------------
        elapsed_label = f"{elapsed_s:.1f}s"
        elapsed_font = QFont("Segoe UI", 7)
        elapsed_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        painter.setFont(elapsed_font)
        painter.setOpacity(0.38 * fade_in)
        painter.setPen(
            QColor(60, 80, 120) if is_light else QColor(100, 140, 200)
        )
        elapsed_rect = QRectF(bar_x, bar_y + bar_h + 6, bar_w, 14)
        painter.drawText(elapsed_rect, Qt.AlignmentFlag.AlignRight, elapsed_label)

        # ----------------------------------------------------------------
        # Actual optimizer state. This comes from the worker process, unlike
        # the rotating fact below it.
        # ----------------------------------------------------------------
        status_font = QFont("Segoe UI", 9)
        status_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(status_font)
        painter.setOpacity(0.82 * fade_in)
        painter.setPen(
            QColor(24, 78, 160) if is_light else QColor(116, 187, 255)
        )
        status_rect = QRectF(bounds.x() + 32, bar_y + bar_h + 20, bounds.width() - 64, 18)
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            f"Stan obliczen: {self.state.currentStageLabel}",
        )

        # ----------------------------------------------------------------
        # Current message (world fact)
        # ----------------------------------------------------------------
        msg_alpha = self._msg_alpha() * fade_in
        msg_font = QFont("Segoe UI", 11)
        msg_font.setWeight(QFont.Weight.Medium)
        painter.setFont(msg_font)
        painter.setOpacity(msg_alpha)
        painter.setPen(
            QColor(20, 40, 100) if is_light else QColor(200, 220, 255)
        )
        msg_rect = QRectF(bounds.x() + 32, bar_y + bar_h + 44, bounds.width() - 64, 44)
        painter.drawText(msg_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self._msg_label)

        # ----------------------------------------------------------------
        # Footer brand
        # ----------------------------------------------------------------
        footer_font = QFont("Segoe UI", 7)
        footer_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.8)
        painter.setFont(footer_font)
        painter.setOpacity(0.28 * fade_in)
        painter.setPen(QColor(60, 80, 120) if is_light else QColor(100, 140, 200))
        painter.drawText(
            bounds.adjusted(0, 0, 0, -12),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            "SIEKACZ 9000 by Kewin",
        )
        painter.setOpacity(1.0)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        event.accept()

    def wheelEvent(self, event) -> None:
        event.accept()


# ---------------------------------------------------------------------------
# Legacy (original orbital) overlay — kept as backup
# ---------------------------------------------------------------------------

class OptimizationProgressOverlayLegacy(QWidget):
    """Original orbital-animation overlay (backup)."""

    finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calculationOverlayLegacy")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.state = OptimizationProgressState()
        self._elapsed = QElapsedTimer()
        self._duration_ms = 1100
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self, project: object | None = None, duration_ms: int = 1100) -> None:
        self._duration_ms = max(800, min(1400, int(duration_ms)))
        self.state.visible = True
        self.state.currentStageLabel = "Liczenie..."
        self._elapsed.restart()
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.state.visible = False
        self.hide()
        self.update()

    def _tick(self) -> None:
        progress = self._cycle_progress()
        self.state.glowPhase = progress
        self.state.globalProgressPercent = int(progress * 100)
        if progress < 0.42:
            self.state.currentStageLabel = "Liczenie..."
        elif progress < 0.86:
            self.state.currentStageLabel = "Sprawdzam układ..."
        else:
            self.state.currentStageLabel = "Finalizowanie..."
        self.update()

    def finish(self, delay_ms: int = 260) -> None:
        self._timer.stop()
        self.state.currentStageLabel = "Gotowe"
        self.state.globalProgressPercent = 100
        self.update()
        QTimer.singleShot(max(0, int(delay_ms)), self.stop)

    def _progress(self) -> float:
        if not self._elapsed.isValid():
            return 0.0
        return min(1.0, self._elapsed.elapsed() / self._duration_ms)

    def _cycle_progress(self) -> float:
        if not self._elapsed.isValid():
            return 0.0
        return (self._elapsed.elapsed() % self._duration_ms) / self._duration_ms

    def _fade_in_progress(self) -> float:
        if not self._elapsed.isValid():
            return 0.0
        return _ease_out_quint(min(self._elapsed.elapsed() / 260.0, 1.0))

    def paintEvent(self, event) -> None:  # noqa: N802
        progress = self._cycle_progress()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        if bounds.isEmpty():
            return

        fade_in = self._fade_in_progress()
        theme = QApplication.instance().property("theme") if QApplication.instance() else "dark"
        is_light = theme == "light"
        if is_light:
            painter.fillRect(bounds, QColor(248, 251, 255, 248))
        else:
            painter.fillRect(bounds, QColor(5, 12, 24, 248))

        background = QRadialGradient(bounds.center(), max(bounds.width(), bounds.height()) * 0.68)
        if is_light:
            background.setColorAt(0.0, QColor(15, 23, 42, int(64 * fade_in)))
            background.setColorAt(0.30, QColor(15, 23, 42, 42))
            background.setColorAt(0.72, QColor(248, 251, 255, 248))
            background.setColorAt(1.0, QColor(248, 251, 255, 255))
        else:
            background.setColorAt(0.0, QColor(74, 132, 255, int(42 * fade_in)))
            background.setColorAt(0.28, QColor(10, 18, 35, 42))
            background.setColorAt(0.72, QColor(5, 12, 24, 246))
            background.setColorAt(1.0, QColor(5, 12, 24, 255))
        painter.fillRect(bounds, background)

        top_glass = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        top_glass.setColorAt(0.0, QColor(255, 255, 255, 120 if is_light else 18))
        top_glass.setColorAt(0.32, QColor(255, 255, 255, 45 if is_light else 4))
        top_glass.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(bounds, top_glass)

        sweep_width = max(110.0, bounds.width() * 0.16)
        sweep_x = bounds.left() - sweep_width + (bounds.width() + sweep_width * 2.0) * _ease_out_quint(progress)
        sweep = QLinearGradient(sweep_x - sweep_width, 0, sweep_x + sweep_width, 0)
        if is_light:
            sweep.setColorAt(0.0, QColor(15, 23, 42, 0))
            sweep.setColorAt(0.48, QColor(15, 23, 42, int(24 * fade_in)))
            sweep.setColorAt(1.0, QColor(15, 23, 42, 0))
        else:
            sweep.setColorAt(0.0, QColor(160, 205, 255, 0))
            sweep.setColorAt(0.48, QColor(170, 210, 255, int(26 * fade_in)))
            sweep.setColorAt(1.0, QColor(160, 205, 255, 0))
        painter.fillRect(bounds, sweep)

        center = bounds.center()
        base_radius = min(bounds.width(), bounds.height()) * 0.15
        phase = (self._elapsed.elapsed() % 2600) / 2600.0 if self._elapsed.isValid() else 0.0

        for index, delay in enumerate((0.0, 0.18, 0.36), start=1):
            local = (phase - delay) % 1.0
            wave = 0.5 - 0.5 * math.cos(local * math.tau)
            radius = base_radius * (1.35 + index * 0.52 + wave * 1.25)
            alpha = int((44 - index * 7) * (1.0 - wave * 0.64) * fade_in)
            halo = QRadialGradient(center, max(70.0, radius))
            if is_light:
                halo.setColorAt(0.0, QColor(15, 23, 42, min(120, alpha + 42)))
                halo.setColorAt(0.34, QColor(15, 23, 42, int(alpha * 0.56)))
            else:
                halo.setColorAt(0.0, QColor(170, 210, 255, alpha))
                halo.setColorAt(0.34, QColor(120, 170, 255, int(alpha * 0.45)))
            halo.setColorAt(1.0, QColor(15, 23, 42, 0) if is_light else QColor(120, 170, 255, 0))
            painter.setBrush(halo)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, radius, radius)

        core_wave = 0.5 + 0.5 * math.sin(phase * math.tau)
        core_radius = max(9.0, min(bounds.width(), bounds.height()) * (0.014 + 0.004 * core_wave))
        core = QRadialGradient(center, core_radius * 4.2)
        if is_light:
            core.setColorAt(0.0, QColor(15, 23, 42, int(220 * fade_in)))
            core.setColorAt(0.24, QColor(15, 23, 42, int(170 * fade_in)))
            core.setColorAt(0.58, QColor(15, 23, 42, int(70 * fade_in)))
            core.setColorAt(1.0, QColor(15, 23, 42, 0))
        else:
            core.setColorAt(0.0, QColor(255, 255, 255, int(250 * fade_in)))
            core.setColorAt(0.24, QColor(224, 240, 255, int(225 * fade_in)))
            core.setColorAt(0.58, QColor(47, 128, 255, int(96 * fade_in)))
            core.setColorAt(1.0, QColor(120, 178, 255, 0))
        painter.setBrush(core)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, core_radius * 4.2, core_radius * 4.2)
        painter.setBrush(
            QColor(15, 23, 42, int(220 * fade_in))
            if is_light
            else QColor(255, 255, 255, int(232 * fade_in))
        )
        painter.drawEllipse(center, core_radius, core_radius)

        label_opacity = (0.66 + 0.12 * math.sin((phase * math.tau) - 0.8)) * fade_in
        painter.setOpacity(label_opacity)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(31, 41, 55) if is_light else QColor(205, 224, 252))
        painter.drawText(
            bounds.adjusted(0, bounds.height() * 0.58, 0, -32),
            Qt.AlignmentFlag.AlignHCenter,
            self.state.currentStageLabel,
        )
        footer_font = QFont("Segoe UI", 8)
        footer_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        painter.setFont(footer_font)
        painter.setOpacity(0.34 * fade_in)
        painter.drawText(
            bounds.adjusted(0, 0, 0, -18),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            "SIEKACZ 9000 by Kewin",
        )
        painter.setOpacity(1.0)

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        event.accept()

    def wheelEvent(self, event) -> None:
        event.accept()


# ---------------------------------------------------------------------------
# Compat aliases
# ---------------------------------------------------------------------------
OptimizationProgressView = OptimizationProgressOverlay
OptimizationLoadingView = OptimizationProgressOverlay
OptimizationLoadingState = OptimizationProgressState
BoardVisualizationWidget = OptimizationProgressOverlay
