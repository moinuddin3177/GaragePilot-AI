import pytest

from garagepilot.pricing import DEFAULT_LABOR_RATE, estimate_cost, hourly_rate

from .conftest import make_garage


def test_declared_rate_wins():
    assert hourly_rate(make_garage("a", labor_rate=80, price_level=4)) == 80


def test_price_level_scales_default():
    assert hourly_rate(make_garage("a", price_level=3)) == pytest.approx(DEFAULT_LABOR_RATE * 1.2)
    assert hourly_rate(make_garage("b")) == DEFAULT_LABOR_RATE


def test_estimate_cost_range(diagnosis):
    cost = estimate_cost(diagnosis, make_garage("a", labor_rate=100))
    assert cost.low == 1.0 * 100 + 60
    assert cost.high == 2.0 * 100 + 120
    assert cost.low < cost.mid < cost.high
