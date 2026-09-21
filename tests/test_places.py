import pytest

from garagepilot import places
from garagepilot.matching import rank

from .conftest import ORIGIN


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def fixture_mode(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)


@pytest.fixture
def live_mode(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")


def test_fixture_mode_loads_and_skips_bad_listing(fixture_mode):
    assert places.is_fixture_mode()
    garages = places.search_garages(*ORIGIN)
    assert len(garages) == 10  # the location-less listing is dropped
    g = next(g for g in garages if g.place_id == "fx_003")
    assert (g.name, g.rating, g.review_count, g.open_now, g.price_level) == (
        "Precision Transmission Experts", 4.8, 230, False, 3
    )
    assert next(g for g in garages if g.place_id == "fx_009").price_level is None


def test_fixture_geocode_and_rank_end_to_end(fixture_mode, diagnosis):
    origin = places.geocode("anywhere")
    top = rank(diagnosis, places.search_garages(*origin), origin, max_km=15)
    assert len(top) == 3
    assert all(m.distance_km <= 15 for m in top)
    assert "fx_010" not in {m.garage.place_id for m in top}  # ~50 km away


def test_live_search_sends_expected_request(live_mode, monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, headers=headers)
        return FakeResponse(payload={"places": [{"id": "p1", "displayName": {"text": "A"},
                                                  "location": {"latitude": 1.0, "longitude": 2.0}}]})

    monkeypatch.setattr(places.requests, "post", fake_post)
    garages = places.search_garages(40.0, -74.0, radius_km=100)
    assert [g.place_id for g in garages] == ["p1"]
    assert seen["url"] == places.SEARCH_URL
    assert seen["headers"]["X-Goog-Api-Key"] == "test-key"
    assert "places.userRatingCount" in seen["headers"]["X-Goog-FieldMask"]
    assert seen["body"]["includedTypes"] == ["car_repair"]
    assert seen["body"]["locationRestriction"]["circle"]["radius"] == places.MAX_RADIUS_M  # capped


def test_live_search_empty_result(live_mode, monkeypatch):
    monkeypatch.setattr(places.requests, "post", lambda *a, **k: FakeResponse(payload={}))
    assert places.search_garages(0, 0) == []


def test_live_search_api_error(live_mode, monkeypatch):
    err = {"error": {"message": "API key not valid"}}
    monkeypatch.setattr(places.requests, "post", lambda *a, **k: FakeResponse(403, err))
    with pytest.raises(places.PlacesError, match="403.*API key not valid"):
        places.search_garages(0, 0)


def test_live_search_network_error(live_mode, monkeypatch):
    def boom(*a, **k):
        raise places.requests.ConnectionError("down")

    monkeypatch.setattr(places.requests, "post", boom)
    with pytest.raises(places.PlacesError, match="Could not reach"):
        places.search_garages(0, 0)


def test_geocode_ok_and_failure(live_mode, monkeypatch):
    ok = {"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5}}}]}
    monkeypatch.setattr(places.requests, "get", lambda *a, **k: FakeResponse(payload=ok))
    assert places.geocode("somewhere") == (1.5, 2.5)

    zero = {"status": "ZERO_RESULTS", "results": []}
    monkeypatch.setattr(places.requests, "get", lambda *a, **k: FakeResponse(payload=zero))
    with pytest.raises(places.PlacesError, match="ZERO_RESULTS"):
        places.geocode("nowhereville")
