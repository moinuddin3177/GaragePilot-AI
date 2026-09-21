"""Deterministic garage ranking.

Ranking is plain arithmetic so it is auditable: each Match carries a per-factor
breakdown. The LLM is only used elsewhere to phrase the "why this match" sentence.
"""

from __future__ import annotations

import math

from .models import Diagnosis, Garage, Match
from .pricing import estimate_cost

# Relative importance of each factor; normalized by their sum when scoring.
WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "balanced": {"distance": 0.20, "cost": 0.25, "rating": 0.25, "open": 0.05, "specialty": 0.20, "registered": 0.05},
    "cheapest": {"distance": 0.10, "cost": 0.55, "rating": 0.15, "open": 0.05, "specialty": 0.10, "registered": 0.05},
    "closest": {"distance": 0.55, "cost": 0.10, "rating": 0.10, "open": 0.10, "specialty": 0.10, "registered": 0.05},
    "best_rated": {"distance": 0.10, "cost": 0.10, "rating": 0.55, "open": 0.05, "specialty": 0.15, "registered": 0.05},
}

# Bayesian rating prior: pulls ratings with few reviews toward the mean.
PRIOR_RATING = 4.0
PRIOR_WEIGHT = 10

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bayesian_rating(rating: float | None, review_count: int) -> float:
    if rating is None or review_count <= 0:
        return PRIOR_RATING
    return (rating * review_count + PRIOR_RATING * PRIOR_WEIGHT) / (review_count + PRIOR_WEIGHT)


def specialty_fit(diagnosis: Diagnosis, garage: Garage) -> float:
    """0..1. Declared specialties (registered) are trusted; otherwise infer from the name."""
    required = {s.lower() for s in diagnosis.required_specialties}
    if garage.specialties:
        if not required:
            return 0.5
        declared = {s.lower() for s in garage.specialties}
        return len(required & declared) / len(required)
    name = garage.name.lower()
    return 0.8 if any(s in name for s in required) else 0.4


def _open_score(open_now: bool | None) -> float:
    return {True: 1.0, False: 0.0, None: 0.5}[open_now]


def rank(
    diagnosis: Diagnosis,
    garages: list[Garage],
    origin: tuple[float, float],
    preset: str = "balanced",
    top_n: int = 3,
    max_km: float = 25.0,
) -> list[Match]:
    weights = WEIGHT_PRESETS[preset]
    total_w = sum(weights.values())

    candidates = []
    for g in garages:
        d = haversine_km(origin[0], origin[1], g.lat, g.lng)
        if d <= max_km:
            candidates.append((g, d, estimate_cost(diagnosis, g)))
    if not candidates:
        return []

    mids = [c[2].mid for c in candidates]
    lo, hi = min(mids), max(mids)

    matches = []
    for g, d, cost in candidates:
        factors = {
            "distance": 1 - d / max_km,
            "cost": 1.0 if hi == lo else (hi - cost.mid) / (hi - lo),
            "rating": (bayesian_rating(g.rating, g.review_count) - 1) / 4,
            "open": _open_score(g.open_now),
            "specialty": specialty_fit(diagnosis, g),
            "registered": 1.0 if g.registered else 0.0,
        }
        score = sum(weights[k] * v for k, v in factors.items()) / total_w
        matches.append(
            Match(garage=g, distance_km=round(d, 2), cost=cost, score=round(score, 4), breakdown=factors)
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:top_n]
