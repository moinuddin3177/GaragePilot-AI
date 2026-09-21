"""Garage discovery and geocoding via Google Places API (New) and the Geocoding API.

With no GOOGLE_MAPS_API_KEY the module runs in fixture mode: results come from
data/places_fixture.json (raw Places API response shape, centered on lower Manhattan)
and geocoding returns that same center. This lets the whole app run without keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .models import Garage

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "places_fixture.json"
FIXTURE_CENTER = (40.7128, -74.0060)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
FIELD_MASK = ",".join(
    f"places.{f}"
    for f in (
        "id",
        "displayName",
        "location",
        "rating",
        "userRatingCount",
        "regularOpeningHours.openNow",
        "nationalPhoneNumber",
        "websiteUri",
        "priceLevel",
    )
)
MAX_RADIUS_M = 50_000  # Places API limit for a circle restriction
TIMEOUT_S = 15

_PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


class PlacesError(RuntimeError):
    """Raised when Google returns an error or an unusable response."""


def _api_key() -> str | None:
    return os.environ.get("GOOGLE_MAPS_API_KEY") or None


def is_fixture_mode() -> bool:
    return _api_key() is None


def parse_place(place: dict) -> Garage | None:
    """Convert one raw Places API place into a Garage; None if it lacks an id or location."""
    loc = place.get("location") or {}
    if "id" not in place or "latitude" not in loc or "longitude" not in loc:
        return None
    return Garage(
        place_id=place["id"],
        name=(place.get("displayName") or {}).get("text", "Unnamed garage"),
        lat=loc["latitude"],
        lng=loc["longitude"],
        rating=place.get("rating"),
        review_count=place.get("userRatingCount", 0),
        open_now=(place.get("regularOpeningHours") or {}).get("openNow"),
        phone=place.get("nationalPhoneNumber"),
        website=place.get("websiteUri"),
        price_level=_PRICE_LEVELS.get(place.get("priceLevel", "")),
    )


def _parse_places(payload: dict) -> list[Garage]:
    return [g for g in map(parse_place, payload.get("places", [])) if g is not None]


def search_garages(lat: float, lng: float, radius_km: float = 15.0, max_results: int = 20) -> list[Garage]:
    """Find car repair shops near a point. Fixture mode ignores the location."""
    if is_fixture_mode():
        return _parse_places(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))

    body = {
        "includedTypes": ["car_repair"],
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": min(radius_km * 1000, MAX_RADIUS_M),
            }
        },
    }
    headers = {"X-Goog-Api-Key": _api_key(), "X-Goog-FieldMask": FIELD_MASK}
    try:
        resp = requests.post(SEARCH_URL, json=body, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise PlacesError(f"Could not reach Google Places: {exc}") from exc
    if resp.status_code != 200:
        raise PlacesError(f"Places API error {resp.status_code}: {_error_message(resp)}")
    return _parse_places(resp.json())


def geocode(address: str) -> tuple[float, float]:
    """Address -> (lat, lng). Fixture mode returns the fixture center."""
    if is_fixture_mode():
        return FIXTURE_CENTER
    try:
        resp = requests.get(
            GEOCODE_URL, params={"address": address, "key": _api_key()}, timeout=TIMEOUT_S
        )
    except requests.RequestException as exc:
        raise PlacesError(f"Could not reach Google Geocoding: {exc}") from exc
    data = resp.json() if resp.status_code == 200 else {}
    if data.get("status") != "OK" or not data.get("results"):
        detail = data.get("error_message") or data.get("status") or f"HTTP {resp.status_code}"
        raise PlacesError(f"Could not locate '{address}': {detail}")
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def _error_message(resp: requests.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return resp.text[:200]
