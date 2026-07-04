from __future__ import annotations

"""License text shown in the in-app license dialog.

SIEKACZ 9000 uses a custom "free use — no resale" license:

  * Free to use by anyone, for ANY purpose, INCLUDING commercial / in-house
    business use in for-profit companies of any size.
  * You may copy and share the unmodified program for free.
  * You may NOT sell the program, charge for it, or offer it as a paid product
    or service whose value is the program itself.
  * You may NOT distribute modified versions.

Rationale: the author wants the program to be used freely (also by large,
profitable companies running their business with it) but does NOT want anyone
to make money by selling the program itself.

Note: this is NOT an OSI/CC open-source license — it is a deliberately simple,
custom proprietary "freeware, no-resale" license matching the author's intent.
The authoritative text lives in the LICENSE file at the project root.
"""

LICENSE_NAME = "Licencja SIEKACZ 9000 — darmowe użycie, bez odsprzedaży"
LICENSE_URL = "https://github.com/amaterasuember/siekacz/blob/main/LICENSE"
BUY_ME_COFFEE_URL = "https://buymeacoffee.com/kewinwojcik"

LICENSE_SUMMARY_PL = (
    "SIEKACZ 9000 — Licencja\n"
    "Darmowe użycie (także komercyjne) — bez odsprzedaży\n"
    "\n"
    "Co WOLNO:\n"
    "  • Używać programu CAŁKOWICIE ZA DARMO, bez limitów, w dowolnym celu —\n"
    "    również komercyjnie i w dużych firmach, które zarabiają na swojej\n"
    "    działalności (np. stolarnie, producenci mebli, zakłady przemysłowe).\n"
    "  • Instalować na dowolnej liczbie komputerów.\n"
    "  • Kopiować i przekazywać program innym w niezmienionej formie, za darmo,\n"
    "    z zachowaniem tej licencji i informacji o autorze (Kewin).\n"
    "\n"
    "Czego NIE WOLNO:\n"
    "  • Sprzedawać programu, pobierać za niego opłat, wynajmować ani\n"
    "    licencjonować odpłatnie.\n"
    "  • Oferować programu jako płatnego produktu lub usługi, której główną\n"
    "    wartością jest sam program.\n"
    "  • Rozpowszechniać zmienionych/przerobionych wersji programu.\n"
    "  • Usuwać informacji o autorze.\n"
    "\n"
    "W skrócie: KORZYSTANIE z programu w ramach Twojej (nawet bardzo dochodowej)\n"
    "działalności jest w pełni dozwolone i darmowe. Niedozwolone jest robienie\n"
    "biznesu z samej SPRZEDAŻY tego programu.\n"
    "\n"
    "Program jest dostarczany „tak jak jest”, bez żadnej gwarancji. Autor nie\n"
    "ponosi odpowiedzialności za jakiekolwiek szkody wynikłe z użycia programu.\n"
)

LICENSE_SUMMARY_EN = (
    "SIEKACZ 9000 — License\n"
    "Free use (including commercial) — No resale\n"
    "\n"
    "You ARE allowed to:\n"
    "  • Use the program completely FREE OF CHARGE, without limits, for ANY\n"
    "    purpose — including commercial and in-house use by for-profit companies\n"
    "    of any size (workshops, furniture makers, industrial plants, etc.).\n"
    "  • Install it on any number of computers.\n"
    "  • Copy and pass the unmodified program to others, free of charge, keeping\n"
    "    this license and the author attribution (Kewin) intact.\n"
    "\n"
    "You are NOT allowed to:\n"
    "  • Sell the program, charge for it, rent it, or license it for a fee.\n"
    "  • Offer the program as a paid product or service whose primary value is\n"
    "    the program itself.\n"
    "  • Distribute modified versions of the program.\n"
    "  • Remove the author attribution.\n"
    "\n"
    "In short: USING the program inside your (even highly profitable) business is\n"
    "fully allowed and free. Making money by SELLING the program itself is not.\n"
    "\n"
    "The program is provided \"as is\", without warranty of any kind. The author is\n"
    "not liable for any damages arising from the use of the program.\n"
)


def full_license_text() -> str:
    return LICENSE_SUMMARY_PL + "\n" + ("-" * 60) + "\n\n" + LICENSE_SUMMARY_EN
