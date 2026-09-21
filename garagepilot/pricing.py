"""Repair cost estimation.

Estimated cost = labor_hours x hourly_rate + parts. Figures are estimates, not quotes.
Currency-agnostic: set DEFAULT_LABOR_RATE to the local hourly rate in your currency.
"""

from __future__ import annotations

from .models import Diagnosis, Garage, Range

DEFAULT_LABOR_RATE = 110.0

# Google Places priceLevel (0-4) -> multiplier on the regional default rate.
PRICE_LEVEL_MULTIPLIER = {0: 0.8, 1: 0.85, 2: 1.0, 3: 1.2, 4: 1.4}


def hourly_rate(garage: Garage, default_rate: float = DEFAULT_LABOR_RATE) -> float:
    """Declared rate for registered garages, else regional default scaled by price level."""
    if garage.labor_rate is not None:
        return garage.labor_rate
    return default_rate * PRICE_LEVEL_MULTIPLIER.get(garage.price_level, 1.0)


def estimate_cost(
    diagnosis: Diagnosis, garage: Garage, default_rate: float = DEFAULT_LABOR_RATE
) -> Range:
    rate = hourly_rate(garage, default_rate)
    labor, parts = diagnosis.est_labor_hours, diagnosis.est_parts_cost
    return Range(low=labor.low * rate + parts.low, high=labor.high * rate + parts.high)
