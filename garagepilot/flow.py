"""Glue between places, store and matching, shared by the customer app.

Searching (geocode + Places, which costs money per call) is separate from ranking (free), so the
UI can re-rank instantly when the customer changes the priority without calling Google again.
"""

from __future__ import annotations

from . import places
from .matching import WEIGHT_PRESETS, rank
from .models import Diagnosis, Garage, Match
from .store import Store

PRESET_LABELS = {
    "balanced": "Balanced",
    "cheapest": "Cheapest",
    "closest": "Closest",
    "best_rated": "Best rated",
}
assert set(PRESET_LABELS) == set(WEIGHT_PRESETS)

SEARCH_RADIUS_KM = 15.0


def search(address: str, store: Store, radius_km: float = SEARCH_RADIUS_KM) -> tuple[tuple[float, float], list[Garage]]:
    """Geocode the address and find nearby garages, marking the ones registered with GaragePilot."""
    origin = places.geocode(address)
    garages = places.search_garages(origin[0], origin[1], radius_km=radius_km)
    return origin, store.apply_registrations(garages)


def rank_matches(
    diagnosis: Diagnosis,
    garages: list[Garage],
    origin: tuple[float, float],
    preset: str = "balanced",
    top_n: int = 3,
    radius_km: float = SEARCH_RADIUS_KM,
) -> list[Match]:
    matches = rank(diagnosis, garages, origin, preset=preset, top_n=top_n, max_km=radius_km)
    for m in matches:
        m.reason = explain(m)
    return matches


def explain(match: Match) -> str:
    """One plain-language line on why this garage ranked well, built from real numbers only."""
    g = match.garage
    parts = [f"{match.distance_km:.1f} km away"]
    if g.open_now is True:
        parts.append("open now")
    elif g.open_now is False:
        parts.append("currently closed")
    if g.rating:
        parts.append(f"{g.rating:.1f} stars from {g.review_count:,} reviews")
    if match.breakdown.get("specialty", 0) >= 0.8:
        parts.append("matches the skills needed")
    parts.append(f"estimated ${match.cost.low:,.0f} to ${match.cost.high:,.0f}")
    tail = (
        "will be pre-briefed and can quote before you arrive"
        if g.registered
        else "not on GaragePilot yet, so call to book"
    )
    return ", ".join(parts) + "; " + tail
