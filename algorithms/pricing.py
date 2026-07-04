"""
Wycena zlecenia rozkroju.

Na podstawie metryk cięcia (cut_metrics.CutSummary) i stawek użytkownika
liczy koszt materiału, cięcia i robocizny — ogółem oraz per format.

Model kosztu (per format):
    materiał  = gross_m2  × stawka_m2      (formatki + odpad produkcyjny; bez resztek magazynowych)
    cięcie    = saw_m     × stawka_mb_ciecia
    robocizna = time_s/3600 × stawka_godzinowa

Koszt całkowity = suma kosztów wszystkich formatów (spójna z per-format).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from algorithms.cut_metrics import CutSummary, FormatCutMetrics


@dataclass
class PricingRates:
    per_m2: float = 0.0        # zł za 1 m² płyty (materiał brutto, z odpadem)
    per_saw_m: float = 0.0     # zł za 1 mb cięcia
    per_hour: float = 0.0      # zł za 1 godzinę robocizny

    @property
    def is_zero(self) -> bool:
        return self.per_m2 <= 0 and self.per_saw_m <= 0 and self.per_hour <= 0


@dataclass
class FormatCost:
    name: str
    pieces: int = 0
    material_cost: float = 0.0
    cut_cost: float = 0.0
    labor_cost: float = 0.0

    @property
    def total(self) -> float:
        return self.material_cost + self.cut_cost + self.labor_cost


@dataclass
class JobPricing:
    formats: list[FormatCost] = field(default_factory=list)
    material_cost: float = 0.0
    cut_cost: float = 0.0
    labor_cost: float = 0.0

    @property
    def total(self) -> float:
        return self.material_cost + self.cut_cost + self.labor_cost


def _format_cost(fmt: FormatCutMetrics, rates: PricingRates) -> FormatCost:
    return FormatCost(
        name=fmt.name,
        pieces=fmt.pieces,
        material_cost=fmt.gross_m2 * max(0.0, rates.per_m2),
        cut_cost=fmt.saw_m * max(0.0, rates.per_saw_m),
        labor_cost=(fmt.time_s / 3600.0) * max(0.0, rates.per_hour),
    )


def compute_pricing(summary: CutSummary, rates: PricingRates) -> JobPricing:
    """Wycena per format i ogółem na podstawie metryk i stawek."""
    pricing = JobPricing()
    for fmt in summary.formats:
        fc = _format_cost(fmt, rates)
        pricing.formats.append(fc)
        pricing.material_cost += fc.material_cost
        pricing.cut_cost += fc.cut_cost
        pricing.labor_cost += fc.labor_cost
    return pricing
