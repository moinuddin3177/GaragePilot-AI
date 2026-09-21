import pytest

from garagepilot.matching import bayesian_rating, haversine_km, rank, specialty_fit

from .conftest import ORIGIN, make_garage

# ~1.11 km per 0.01 degree of latitude
NEAR = ORIGIN[0] + 0.01
FAR = ORIGIN[0] + 0.10


def test_haversine_known_distance():
    # NYC -> Philadelphia is roughly 130 km
    assert haversine_km(40.7128, -74.0060, 39.9526, -75.1652) == pytest.approx(130, abs=5)
    assert haversine_km(1, 2, 1, 2) == 0


def test_bayesian_rating_shrinks_low_review_counts():
    assert bayesian_rating(5.0, 3) < bayesian_rating(5.0, 300)
    assert bayesian_rating(5.0, 300) == pytest.approx(5.0, abs=0.1)
    assert bayesian_rating(None, 0) == 4.0


def test_specialty_fit(diagnosis):
    assert specialty_fit(diagnosis, make_garage("a", specialties=["brakes", "ac"])) == 1.0
    assert specialty_fit(diagnosis, make_garage("b", specialties=["ac"])) == 0.0
    assert specialty_fit(diagnosis, make_garage("c", name="Joe's Brakes & Tires")) == 0.8
    assert specialty_fit(diagnosis, make_garage("d", name="Joe's Auto")) == 0.4


def test_closest_preset_prefers_nearby(diagnosis):
    near = make_garage("near", lat=NEAR, rating=3.5)
    far = make_garage("far", lat=FAR, rating=4.9)
    top = rank(diagnosis, [far, near], ORIGIN, preset="closest")
    assert top[0].garage.place_id == "near"


def test_best_rated_preset_prefers_rating(diagnosis):
    near = make_garage("near", lat=NEAR, rating=3.5)
    far = make_garage("far", lat=NEAR + 0.01, rating=4.9)
    top = rank(diagnosis, [near, far], ORIGIN, preset="best_rated")
    assert top[0].garage.place_id == "far"


def test_cheapest_preset_prefers_low_rate(diagnosis):
    cheap = make_garage("cheap", lat=FAR, labor_rate=60, registered=True)
    pricey = make_garage("pricey", lat=NEAR, labor_rate=200, registered=True)
    top = rank(diagnosis, [pricey, cheap], ORIGIN, preset="cheapest")
    assert top[0].garage.place_id == "cheap"


def test_excludes_garages_beyond_max_km(diagnosis):
    g = make_garage("far", lat=ORIGIN[0] + 1.0)  # ~111 km
    assert rank(diagnosis, [g], ORIGIN, max_km=25) == []


def test_top_n_and_breakdown(diagnosis):
    garages = [make_garage(f"g{i}", lat=ORIGIN[0] + 0.01 * i) for i in range(5)]
    top = rank(diagnosis, garages, ORIGIN, top_n=3)
    assert len(top) == 3
    assert [m.score for m in top] == sorted((m.score for m in top), reverse=True)
    assert set(top[0].breakdown) == {"distance", "cost", "rating", "open", "specialty", "registered"}


def test_single_candidate_does_not_divide_by_zero(diagnosis):
    top = rank(diagnosis, [make_garage("only", lat=NEAR)], ORIGIN)
    assert len(top) == 1 and 0 <= top[0].score <= 1
