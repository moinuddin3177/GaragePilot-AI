"""End-to-end tests of the Streamlit pages using Streamlit's AppTest (no browser, no network)."""

import os
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from garagepilot import diagnose as diagnose_module
from garagepilot.models import Vehicle
from garagepilot.store import Store

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fixture-mode Places, dry-run email, throwaway DB."""
    db = tmp_path / "app.db"
    monkeypatch.setenv("GARAGEPILOT_DB", str(db))
    for k in ("GOOGLE_MAPS_API_KEY", "SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    st.cache_resource.clear()  # get_store() must pick up this test's DB
    return Store(db)


def customer_app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()


def portal_app() -> AppTest:
    return AppTest.from_file(str(ROOT / "pages" / "1_Garage_Portal.py"), default_timeout=30).run()


def click(at: AppTest, label: str) -> AppTest:
    matches = [b for b in at.button if b.label == label]
    assert matches, f"no button {label!r}; have {[b.label for b in at.button]}"
    return matches[0].click().run()


def fill_intake(at: AppTest, symptoms="Grinding noise when I brake") -> AppTest:
    at.text_input(key="in_make").input("Honda")
    at.text_input(key="in_model").input("Civic")
    at.text_area(key="in_symptoms").input(symptoms)
    at.text_input(key="in_address").input("Lower Manhattan")
    return at


@pytest.fixture
def fake_diagnose(monkeypatch, diagnosis):
    calls = []

    def fake(vehicle, symptoms, answers=None, image=None, client=None):
        calls.append({"vehicle": vehicle, "symptoms": symptoms, "answers": list(answers or [])})
        return diagnosis

    monkeypatch.setattr(diagnose_module, "diagnose", fake)
    return calls


def test_sidebar_never_prefills_or_exposes_server_key(env):
    # env fixture sets a real-looking server ANTHROPIC_API_KEY
    at = customer_app()
    assert not at.exception, at.exception
    assert any("configured by this server" in c.value for c in at.caption)
    assert not [t for t in at.text_input if t.label == "Anthropic API key"]  # no box to leak it from


def test_sidebar_prompts_for_session_key_when_server_has_none(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st.cache_resource.clear()
    at = customer_app()
    assert not at.exception, at.exception
    box = at.text_input(key="session_anthropic_key")
    assert box.value == ""  # never pre-filled from anywhere
    assert any("Enter your API key" in w.value for w in at.warning)
    assert not any("configured by this server" in c.value for c in at.caption)


def test_typed_session_key_is_not_written_to_process_environment(env, monkeypatch, fake_diagnose):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st.cache_resource.clear()
    at = customer_app()
    at.text_input(key="session_anthropic_key").input("sk-ant-visitor-typed-this")
    at = fill_intake(at)
    at = click(at, "Diagnose and find garages")
    assert not at.exception, at.exception
    assert os.environ.get("ANTHROPIC_API_KEY") is None  # never mutated globally
    client = at.session_state.get("session_anthropic_key")
    assert client == "sk-ant-visitor-typed-this"  # stays scoped to this session's own state


def test_customer_full_flow(env, fake_diagnose):
    # a registered garage that the fixture search should surface
    token = env.register_garage("fx_001", "Downtown Brake & Tire", "d@example.com", ["brakes"], 70, "(212) 555-0101")

    at = fill_intake(customer_app())
    at = click(at, "Diagnose and find garages")
    assert not at.exception, at.exception
    assert fake_diagnose[0]["vehicle"] == Vehicle(make="Honda", model="Civic", year=2015, mileage=None)
    assert [s.value for s in at.subheader] == [
        "Your pre-diagnosis", "Best garages near you", "Get quotes before you arrive"
    ]
    assert any("Do not drive" in e.value for e in at.error)  # brakes fixture is unsafe to drive
    # regression: two prices in one line must both keep their escaped $ (unescaped, they render as LaTeX)
    reasons = [c.value for c in at.caption if "estimated" in c.value]
    assert reasons and all(r.count("\\$") == 2 for r in reasons), reasons

    # re-ranking on a new priority needs no new diagnosis
    at.radio(key="preset").set_value("cheapest").run()
    assert not at.exception and len(fake_diagnose) == 1

    # validation: consent and contact details are required
    at = click(at, "Send my issue to these garages")
    assert any("name" in e.value for e in at.error)
    at.text_input(key="send_name").input("Sam Customer")
    at.text_input(key="send_contact").input("sam@example.com")
    at = click(at, "Send my issue to these garages")
    assert any("consent" in e.value for e in at.error)

    at.checkbox(key="consent").check()
    at = click(at, "Send my issue to these garages")
    assert not at.exception, at.exception
    lead_id = at.query_params["lead"][0] if isinstance(at.query_params["lead"], list) else at.query_params["lead"]
    assert env.lead_exists(lead_id)
    assert [r["place_id"] for r in env.lead_recipients(lead_id)] == []  # already notified (dry-run)
    assert at.subheader[0].value == "Your request is with the garages"

    # garage replies; customer refreshes, sees the quote and chooses it
    env.submit_quote(lead_id, "fx_001", 240, "Tomorrow 9am", "pads and rotors")
    at = click(at, "🔄 Refresh quotes")
    assert any("\\$240" in m.value for m in at.markdown)  # escaped so Streamlit doesn't treat $ as LaTeX
    at = click(at, "Choose this garage")
    assert env.chosen_garage(lead_id) == "fx_001"
    assert any("(212) 555-0101" in s.value for s in at.success)
    assert env.lead_for_garage(lead_id, "fx_001").customer_contact == "sam@example.com"

    # a fresh request starts clean
    at = click(at, "Start a new request")
    assert at.text_input(key="in_make").value == ""


def test_no_registered_garage_shows_call_advice_not_send_form(env, fake_diagnose):
    at = click(fill_intake(customer_app()), "Diagnose and find garages")
    assert not at.exception, at.exception
    assert any("None of these garages have joined" in i.value for i in at.info)
    assert not [b for b in at.button if b.label == "Send my issue to these garages"]


def test_intake_validation(env, fake_diagnose):
    at = customer_app()
    at.text_input(key="in_make").input("Honda")
    at = click(at, "Diagnose and find garages")
    assert any("Please fill in" in e.value for e in at.error)
    assert fake_diagnose == []


def test_diagnosis_error_is_shown_and_state_cleared(env, monkeypatch):
    def boom(*a, **k):
        raise diagnose_module.DiagnosisError("No Anthropic API key found.")

    monkeypatch.setattr(diagnose_module, "diagnose", boom)
    at = click(fill_intake(customer_app()), "Diagnose and find garages")
    assert not at.exception
    assert any("No Anthropic API key" in e.value for e in at.error)
    assert "diagnosis" not in at.session_state


def test_follow_up_questions_flow(env, monkeypatch, diagnosis):
    calls = []

    def fake(vehicle, symptoms, answers=None, image=None, client=None):
        calls.append(list(answers or []))
        if not answers:
            return diagnosis.model_copy(update={"follow_up_questions": ["Does it happen at high speed?"]})
        return diagnosis

    monkeypatch.setattr(diagnose_module, "diagnose", fake)
    at = click(fill_intake(customer_app(), "weird noise"), "Diagnose and find garages")
    assert not at.exception, at.exception
    assert at.subheader[0].value == "A couple of quick questions"

    at.text_input(key="fu_0_0").input("Yes, mostly on the highway")
    at = click(at, "Update diagnosis")
    assert not at.exception, at.exception
    assert calls[-1] == [("Does it happen at high speed?", "Yes, mostly on the highway")]
    assert at.subheader[0].value == "Your pre-diagnosis"  # questions gone


def test_garage_portal_register_receive_quote(env, diagnosis):
    at = portal_app()
    assert [t.label for t in at.tabs] == ["Sign in", "Register your garage"]

    at.text_input(key="reg_where").input("Lower Manhattan")
    at = click(at, "Search")
    assert not at.exception, at.exception
    at.selectbox(key="reg_choice").select_index(0)  # Downtown Brake & Tire
    at.text_input(key="reg_email").input("owner@example.com")
    at.multiselect(key="reg_skills").set_value(["brakes"])
    at = click(at, "Register")
    assert not at.exception, at.exception
    garage = env.get_garage("fx_001")
    assert garage["email"] == "owner@example.com" and garage["specialties"] == ["brakes"]
    token = at.session_state["new_token"]
    assert env.authenticate("fx_001", token)
    assert any("Save your access token" in s.value for s in at.success)

    # a customer job arrives
    from garagepilot.models import Match, Range
    from tests.conftest import make_garage

    m = Match(garage=make_garage("fx_001", registered=True), distance_km=1.4, cost=Range(low=150, high=300), score=0.8, breakdown={})
    lead_id = env.create_lead(Vehicle(make="Honda", model="Civic", year=2015), "grinding brakes", diagnosis, [m], "Sam", "sam@example.com")

    at = click(at, "Sign out")
    at.text_input[0].input("wrong-token")
    at = click(at, "Sign in")
    assert any("wasn't recognised" in e.value for e in at.error)
    at.text_input[0].input(token)
    at = click(at, "Sign in")
    assert not at.exception, at.exception

    assert any("2015 Honda Civic" in m.value for m in at.markdown)
    assert not any("sam@example.com" in x.value for x in at.markdown)  # contact hidden before the customer chooses
    assert env.lead_for_garage(lead_id, "fx_001").status == "viewed"

    at.number_input[-1].set_value(260.0)
    at = click(at, "Send quote")
    assert not at.exception, at.exception
    q = env.lead_quotes(lead_id)[0]
    assert (q.price, q.garage_name) == (260.0, "Downtown Brake & Tire")


def test_portal_rejects_duplicate_claim(env):
    env.register_garage("fx_001", "Downtown Brake & Tire", "first@example.com", ["brakes"], 70)
    at = portal_app()
    at.text_input(key="reg_where").input("Lower Manhattan")
    at = click(at, "Search")
    at.selectbox(key="reg_choice").select_index(0)
    at.text_input(key="reg_email").input("squatter@example.com")
    at = click(at, "Register")
    assert any("already been claimed" in e.value for e in at.error)
    assert env.get_garage("fx_001")["email"] == "first@example.com"
