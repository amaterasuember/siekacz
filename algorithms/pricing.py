"""
Wycena zlecenia rozkroju.

Na podstawie metryk cięcia (cut_metrics.CutSummary) i stawek użytkownika
liczy koszt materiału, cięcia i robocizny — ogółem oraz per format.

Model kosztu (per format):
    materiał  = gross_m2  × stawka_m2      (formatki + odpad produkcyjny; bez resztek magazynowych)
    cięcie    = sztuki    × stawka_szt_ciecia
    robocizna = time_s/3600 × stawka_godzinowa

Koszt całkowity = suma kosztów wszystkich formatów (spójna z per-format).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from algorithms.cut_metrics import CutSummary, FormatCutMetrics


@dataclass
class PricingRates:
    per_m2: float | dict[str, float] = 0.0        # zł za 1 m² płyty (materiał brutto)
    per_piece: float = 0.0     # zł za 1 sztukę formatki (cięcie)
    per_hour: float = 0.0      # zł za 1 godzinę robocizny

    @property
    def is_zero(self) -> bool:
        m2_zero = all(v <= 0 for v in self.per_m2.values()) if isinstance(self.per_m2, dict) else self.per_m2 <= 0
        return m2_zero and self.per_piece <= 0 and self.per_hour <= 0

    def get_m2_price(self, material: str, thickness: float = 0.0) -> float:
        if isinstance(self.per_m2, dict):
            exact = f"{material}|{thickness:g}"
            if exact in self.per_m2:
                return self.per_m2[exact]
            return self.per_m2.get(material, 0.0)
        return self.per_m2


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
    # A supplier-backed result carries the price of the exact stock board that
    # was selected for the layout. It must override any historical manual
    # material/thickness setting, which has no information about board format.
    material_rate = float(getattr(fmt, "catalog_price_m2", 0.0) or 0.0)
    if material_rate <= 0:
        material_rate = rates.get_m2_price(fmt.material, fmt.thickness)
    return FormatCost(
        name=fmt.name,
        pieces=fmt.pieces,
        material_cost=fmt.gross_m2 * max(0.0, material_rate),
        cut_cost=fmt.pieces * max(0.0, rates.per_piece),
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
