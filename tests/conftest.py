import pytest

from garagepilot.models import Cause, Diagnosis, Garage, Range

ORIGIN = (40.7128, -74.0060)


@pytest.fixture
def diagnosis() -> Diagnosis:
    return Diagnosis(
        summary="Worn brake pads.",
        likely_causes=[Cause(cause="Worn brake pads", probability=0.8)],
        severity=4,
        safe_to_drive=False,
        urgency="immediate",
        required_specialties=["brakes"],
        est_labor_hours=Range(low=1.0, high=2.0),
        est_parts_cost=Range(low=60, high=120),
    )


def make_garage(place_id: str, **kw) -> Garage:
    defaults = dict(name=place_id, lat=ORIGIN[0], lng=ORIGIN[1], rating=4.0, review_count=100)
    defaults.update(kw)
    return Garage(place_id=place_id, **defaults)
