import pytest

from garagepilot import flow, places
from garagepilot.store import Store

from .conftest import ORIGIN


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "f.db")


def test_search_marks_registered_garages(store):
    store.register_garage("fx_001", "Downtown Brake & Tire", "d@example.com", ["brakes"], 70)
    origin, garages = flow.search("anywhere", store)
    assert origin == places.FIXTURE_CENTER
    by_id = {g.place_id: g for g in garages}
    assert by_id["fx_001"].registered and by_id["fx_001"].labor_rate == 70
    assert not by_id["fx_002"].registered


def test_rank_matches_adds_reasons_and_reranks_without_new_search(store, diagnosis, monkeypatch):
    origin, garages = flow.search("anywhere", store)
    monkeypatch.setattr(places, "search_garages", lambda *a, **k: pytest.fail("must not re-search"))
    closest = flow.rank_matches(diagnosis, garages, origin, preset="closest")
    cheapest = flow.rank_matches(diagnosis, garages, origin, preset="cheapest")
    assert len(closest) == 3 and all(m.reason for m in closest)
    assert [m.garage.place_id for m in closest] != [m.garage.place_id for m in cheapest]
    assert all(m.distance_km <= flow.SEARCH_RADIUS_KM for m in closest + cheapest)


def test_explain_states_registration_status(store, diagnosis):
    store.register_garage("fx_001", "Downtown Brake & Tire", "d@example.com", ["brakes"], 70)
    origin, garages = flow.search("anywhere", store)
    matches = {m.garage.place_id: m for m in flow.rank_matches(diagnosis, garages, origin, top_n=10)}
    assert "pre-briefed" in matches["fx_001"].reason and "matches the skills needed" in matches["fx_001"].reason
    assert "call to book" in matches["fx_002"].reason
    assert "km away" in matches["fx_002"].reason and "estimated $" in matches["fx_002"].reason
