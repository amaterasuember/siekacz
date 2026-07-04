from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QPushButton, QWidget


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


# ---------------------------------------------------------------------------
# New Sims-style animation overlay
# ---------------------------------------------------------------------------

class OptimizationProgressOverlay(QWidget):
    """Animated loading overlay shown during optimization.

    Features:
    - Indeterminate progress bar that sweeps back and forth
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
        by = int(self.height() * 0.78)
        self._cancel_button.setGeometry(bx, by, bw, bh)
        self._cancel_button.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition_cancel()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reposition_cancel()

    # ------------------------------------------------------------------
    def start(self, project: object | None = None, duration_ms: int = 1100) -> None:
        self._elapsed.restart()
        self._msg_elapsed_s = 0.0
        self._msg_phase = 0.0
        # Continue through the persistent shuffle-bag instead of reshuffling and
        # restarting at index 0 every run.  Because a typical optimization lasts
        # only a few seconds, the user mostly sees the *first* message of each
        # run; advancing the deck here guarantees a fresh fact every time and no
        # repeats until the whole deck has been shown.
        self._advance_message()
        self.state.visible = True
        self.state.globalProgressPercent = 0
        self._cancel_button.setDisabled(False)
        self._cancel_button.setText("Anuluj")
        self._cancel_button.show()
        self.show()
        self.raise_()
        self._reposition_cancel()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.state.visible = False
        self._cancel_button.hide()
        self.hide()
        self.update()

    def finish(self, delay_ms: int = 320) -> None:
        self._timer.stop()
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
        cycle_s = 2.4
        bar_t = (elapsed_s % cycle_s) / cycle_s
        self.state.globalProgressPercent = int(_ease_out_quint(bar_t) * 100)

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
        # Pulsing icon area (SIEKACZ "S" glyph or geometric shape)
        # ----------------------------------------------------------------
        cx = bounds.center().x()
        icon_cy = bounds.height() * 0.34

        # Rotating ring
        ring_r = min(bounds.width(), bounds.height()) * 0.095
        ring_pen_color = (
            QColor(30, 60, 120, int(90 * fade_in))
            if is_light
            else QColor(80, 140, 255, int(80 * fade_in))
        )
        angle_speed = elapsed_s * 180.0  # degrees/s  (half rotation)
        for i in range(3):
            ring_rr = ring_r * (1.0 + i * 0.28)
            alpha_factor = (3 - i) / 3.0
            ring_col = QColor(ring_pen_color)
            ring_col.setAlpha(int(ring_col.alpha() * alpha_factor))
            from PySide6.QtGui import QPen
            pen = QPen(ring_col, max(1.0, ring_rr * 0.08))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            arc_len = 120 + i * 30  # degrees of arc
            start_angle = int((angle_speed * (1 + i * 0.4)) % 360 * 16)
            arc_rect = QRectF(cx - ring_rr, icon_cy - ring_rr, ring_rr * 2, ring_rr * 2)
            painter.drawArc(arc_rect, start_angle, arc_len * 16)

        # Central glowing dot
        pulse = 0.5 + 0.5 * math.sin(elapsed_s * math.pi * 1.6)
        dot_r = max(5.0, ring_r * (0.22 + 0.06 * pulse))
        dot_grad = QRadialGradient(QPointF(cx, icon_cy), dot_r * 3.0)
        if is_light:
            dot_grad.setColorAt(0.0, QColor(10, 30, 90, int(230 * fade_in)))
            dot_grad.setColorAt(0.5, QColor(30, 80, 180, int(120 * fade_in)))
            dot_grad.setColorAt(1.0, QColor(30, 80, 180, 0))
        else:
            dot_grad.setColorAt(0.0, QColor(255, 255, 255, int(240 * fade_in)))
            dot_grad.setColorAt(0.5, QColor(140, 190, 255, int(110 * fade_in)))
            dot_grad.setColorAt(1.0, QColor(60, 120, 255, 0))
        painter.setBrush(dot_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, icon_cy), dot_r * 3.0, dot_r * 3.0)
        if is_light:
            painter.setBrush(QColor(10, 30, 100, int(220 * fade_in)))
        else:
            painter.setBrush(QColor(255, 255, 255, int(235 * fade_in)))
        painter.drawEllipse(QPointF(cx, icon_cy), dot_r, dot_r)

        # ----------------------------------------------------------------
        # Progress bar
        # ----------------------------------------------------------------
        bar_w = min(bounds.width() * 0.62, 380.0)
        bar_h = 6.0
        bar_x = cx - bar_w / 2.0
        bar_y = bounds.height() * 0.58

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
        fill_grad = QLinearGradient(bar_x, bar_y, bar_x + fill_w, bar_y)
        if is_light:
            fill_grad.setColorAt(0.0, QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(max(0.0, shimmer_pos - 0.15), QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(min(1.0, shimmer_pos), QColor(100, 160, 255, int(240 * fade_in)))
            fill_grad.setColorAt(min(1.0, shimmer_pos + 0.15), QColor(30, 80, 200, int(200 * fade_in)))
            fill_grad.setColorAt(1.0, QColor(30, 80, 200, int(200 * fade_in)))
        else:
            fill_grad.setColorAt(0.0, QColor(60, 120, 255, int(220 * fade_in)))
            fill_grad.setColorAt(max(0.0, shimmer_pos - 0.15), QColor(60, 120, 255, int(220 * fade_in)))
            fill_grad.setColorAt(min(1.0, shimmer_pos), QColor(160, 210, 255, int(255 * fade_in)))
            fill_grad.setColorAt(min(1.0, shimmer_pos + 0.15), QColor(60, 120, 255, int(220 * fade_in)))
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
        # Current message (Sims-style)
        # ----------------------------------------------------------------
        msg_alpha = self._msg_alpha() * fade_in
        msg_font = QFont("Segoe UI", 11)
        msg_font.setWeight(QFont.Weight.Medium)
        painter.setFont(msg_font)
        painter.setOpacity(msg_alpha)
        painter.setPen(
            QColor(20, 40, 100) if is_light else QColor(200, 220, 255)
        )
        msg_rect = QRectF(bounds.x() + 32, bar_y + bar_h + 28, bounds.width() - 64, 30)
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
