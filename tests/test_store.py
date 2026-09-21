import pytest

from garagepilot.models import Match, Range, Vehicle
from garagepilot.store import Store, StoreError

from .conftest import make_garage

VEHICLE = Vehicle(make="Honda", model="Civic", year=2015, mileage=98000)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "test.db")


def match_for(garage, distance=2.0) -> Match:
    return Match(garage=garage, distance_km=distance, cost=Range(low=160, high=340), score=0.7, breakdown={})


def register(store, place_id="g1", **kw):
    args = dict(name=f"Garage {place_id}", email=f"{place_id}@example.com", specialties=["brakes"], labor_rate=90.0)
    args.update(kw)
    return store.register_garage(place_id, **args)


def make_lead(store, diagnosis, place_ids=("g1",), unregistered=()):
    matches = [match_for(make_garage(p, registered=True)) for p in place_ids]
    matches += [match_for(make_garage(p, registered=False)) for p in unregistered]
    return store.create_lead(VEHICLE, "grinding when braking", diagnosis, matches, "Sam Customer", "sam@example.com")


# ---- garages ---------------------------------------------------------------

def test_register_and_authenticate(store):
    token = register(store)
    assert store.authenticate("g1", token)
    assert not store.authenticate("g1", "wrong")
    assert not store.authenticate("nope", token)
    g = store.get_garage("g1")
    assert g["specialties"] == ["brakes"] and g["labor_rate"] == 90.0
    assert "token" not in "".join(g)  # no token/hash in the returned record


def test_token_is_not_stored_in_plaintext(store):
    token = register(store)
    with store._conn() as conn:
        stored = conn.execute("SELECT token_hash FROM garages").fetchone()[0]
    assert token not in stored and len(stored) == 64


def test_duplicate_claim_rejected(store):
    register(store)
    with pytest.raises(StoreError, match="already been claimed"):
        register(store, email="other@example.com")


@pytest.mark.parametrize("kw", [{"email": "not-an-email"}, {"email": "a b@c.com"}, {"name": "  "}, {"labor_rate": 0}, {"labor_rate": 5000}])
def test_registration_validation(store, kw):
    with pytest.raises(StoreError):
        register(store, **kw)


def test_specialties_cleaned_and_updatable(store):
    register(store, specialties=[" Brakes", "brakes", "magic", "AC"])
    assert store.get_garage("g1")["specialties"] == ["brakes", "ac"]
    store.update_garage("g1", specialties=["tires"], labor_rate=75, phone="555")
    g = store.get_garage("g1")
    assert (g["specialties"], g["labor_rate"], g["phone"]) == (["tires"], 75, "555")
    with pytest.raises(StoreError, match="not found"):
        store.update_garage("missing", phone="1")


def test_apply_registrations_marks_and_copies(store):
    register(store, "g1", specialties=["brakes", "ac"], labor_rate=80)
    found = [make_garage("g1"), make_garage("g2")]
    out = {g.place_id: g for g in store.apply_registrations(found)}
    assert out["g1"].registered and out["g1"].specialties == ["brakes", "ac"] and out["g1"].labor_rate == 80
    assert not out["g2"].registered and out["g2"].labor_rate is None
    assert not found[0].registered  # input not mutated
    assert store.apply_registrations([]) == []


# ---- leads, privacy --------------------------------------------------------

def test_lead_attaches_only_registered_garages(store, diagnosis):
    register(store, "g1")
    lead_id = make_lead(store, diagnosis, place_ids=("g1",), unregistered=("g2",))
    assert [r["place_id"] for r in store.lead_recipients(lead_id)] == ["g1"]
    assert store.lead_for_garage(lead_id, "g2") is None


def test_customer_contact_hidden_until_chosen_and_only_for_chosen(store, diagnosis):
    register(store, "g1"), register(store, "g2")
    lead_id = make_lead(store, diagnosis, place_ids=("g1", "g2"))
    for pid in ("g1", "g2"):
        store.submit_quote(lead_id, pid, 250)

    for pid in ("g1", "g2"):
        brief = store.lead_for_garage(lead_id, pid)
        assert brief.customer_name is None and brief.customer_contact is None and not brief.chosen

    store.choose_garage(lead_id, "g1")
    chosen, other = store.lead_for_garage(lead_id, "g1"), store.lead_for_garage(lead_id, "g2")
    assert chosen.chosen and chosen.customer_contact == "sam@example.com" and chosen.customer_name == "Sam Customer"
    assert other.customer_contact is None and other.customer_name is None
    assert "sam@example.com" not in other.model_dump_json()


def test_brief_contents_and_viewed_status(store, diagnosis):
    register(store, "g1")
    lead_id = make_lead(store, diagnosis)
    assert store.lead_for_garage(lead_id, "g1").status == "sent"
    brief = store.lead_for_garage(lead_id, "g1", mark_viewed=True)
    assert brief.status == "viewed" and store.garage_leads("g1")[0].status == "viewed"
    assert brief.vehicle == VEHICLE and brief.diagnosis == diagnosis
    assert brief.distance_km == 2.0 and (brief.est_cost.low, brief.est_cost.high) == (160, 340)


def test_garage_leads_newest_first_and_isolated(store, diagnosis):
    register(store, "g1"), register(store, "g2")
    first = make_lead(store, diagnosis, place_ids=("g1",))
    second = make_lead(store, diagnosis, place_ids=("g1", "g2"))
    assert [b.lead_id for b in store.garage_leads("g1")] == [second, first]
    assert [b.lead_id for b in store.garage_leads("g2")] == [second]


# ---- quotes ----------------------------------------------------------------

def test_quote_flow_sorted_cheapest_first_and_updatable(store, diagnosis):
    register(store, "g1"), register(store, "g2")
    lead_id = make_lead(store, diagnosis, place_ids=("g1", "g2"))
    store.submit_quote(lead_id, "g1", 300, "Tomorrow 9am", "incl. pads")
    store.submit_quote(lead_id, "g2", 220)
    assert [q.garage_name for q in store.lead_quotes(lead_id)] == ["Garage g2", "Garage g1"]
    store.submit_quote(lead_id, "g1", 200)  # revise
    quotes = store.lead_quotes(lead_id)
    assert [q.price for q in quotes] == [200, 220] and len(quotes) == 2
    assert store.lead_for_garage(lead_id, "g1").status == "quoted"


def test_quote_validation(store, diagnosis):
    register(store, "g1"), register(store, "g2")
    lead_id = make_lead(store, diagnosis, place_ids=("g1",))
    with pytest.raises(StoreError, match="greater than zero"):
        store.submit_quote(lead_id, "g1", 0)
    with pytest.raises(StoreError, match="not sent to your garage"):
        store.submit_quote(lead_id, "g2", 100)  # g2 never got this lead


def test_decline_removes_quote(store, diagnosis):
    register(store, "g1")
    lead_id = make_lead(store, diagnosis)
    store.submit_quote(lead_id, "g1", 100)
    store.decline_lead(lead_id, "g1")
    assert store.lead_quotes(lead_id) == []
    assert store.lead_for_garage(lead_id, "g1").status == "declined"


def test_choose_requires_quote_and_locks_further_quotes(store, diagnosis):
    register(store, "g1"), register(store, "g2")
    lead_id = make_lead(store, diagnosis, place_ids=("g1", "g2"))
    with pytest.raises(StoreError, match="not quoted"):
        store.choose_garage(lead_id, "g1")
    store.submit_quote(lead_id, "g1", 100)
    store.choose_garage(lead_id, "g1")
    assert store.chosen_garage(lead_id) == "g1"
    with pytest.raises(StoreError, match="already chosen"):
        store.submit_quote(lead_id, "g2", 50)


def test_data_persists_across_store_instances(tmp_path, diagnosis):
    path = tmp_path / "p.db"
    token = register(Store(path))
    assert Store(path).authenticate("g1", token)
