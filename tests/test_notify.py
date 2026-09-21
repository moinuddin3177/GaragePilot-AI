import smtplib

import pytest

from garagepilot import notify
from garagepilot.models import Match, Range, Vehicle
from garagepilot.store import Store

from .conftest import make_garage

VEHICLE = Vehicle(make="Honda", model="Civic", year=2015, mileage=98000)
CONTACT = "sam.private@example.com"


@pytest.fixture(autouse=True)
def dry_run_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM", "SMTP_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://gp.example.com/")


@pytest.fixture
def store_with_lead(tmp_path, diagnosis):
    store = Store(tmp_path / "n.db")
    for pid in ("g1", "g2"):
        store.register_garage(pid, f"Garage {pid}", f"{pid}@example.com", ["brakes"], 90)
    matches = [
        Match(garage=make_garage(pid, registered=True), distance_km=3.2, cost=Range(low=160, high=340), score=0.7, breakdown={})
        for pid in ("g1", "g2")
    ]
    lead_id = store.create_lead(VEHICLE, "grinding noise when I brake", diagnosis, matches, "Sam Customer", CONTACT)
    return store, lead_id


def test_lead_email_content_and_no_customer_contact(store_with_lead):
    store, lead_id = store_with_lead
    subject, body = notify.build_lead_email("Garage g1", store.lead_for_garage(lead_id, "g1"))
    assert "2015 Honda Civic" in subject and "severity 4/5" in subject
    for expected in ("grinding noise when I brake", "NOT safe to drive", "brakes", "3.2 km", "160 to 340",
                     f"https://gp.example.com/Garage_Portal?lead={lead_id}", "Worn brake pads (80%)"):
        assert expected in body
    assert CONTACT not in body and "Sam Customer" not in body


def test_notify_lead_dry_run_prints_and_marks_notified(store_with_lead, capsys):
    store, lead_id = store_with_lead
    results = notify.notify_lead(store, lead_id)
    out = capsys.readouterr().out
    assert [(r.place_id, r.ok, r.dry_run) for r in results] == [("g1", True, True), ("g2", True, True)]
    assert "DRY-RUN EMAIL" in out and "To: g1@example.com" in out and "To: g2@example.com" in out
    assert CONTACT not in out
    assert notify.notify_lead(store, lead_id) == []  # nothing left to notify


def test_failure_isolated_and_only_failed_retried(store_with_lead, monkeypatch):
    store, lead_id = store_with_lead
    real = notify.send_email

    def flaky(to, subject, body):
        if to == "g1@example.com":
            raise notify.NotifyError("smtp down")
        return real(to, subject, body)

    monkeypatch.setattr(notify, "send_email", flaky)
    results = {r.place_id: r for r in notify.notify_lead(store, lead_id)}
    assert not results["g1"].ok and "smtp down" in results["g1"].error and results["g2"].ok
    assert [r["place_id"] for r in store.lead_recipients(lead_id)] == ["g1"]  # retry hits g1 only


def test_smtp_send_uses_starttls_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, pw):
            calls.append(("login", user, pw))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"], msg["From"]))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    assert notify.send_email("a@b.com", "Hi", "body") is False
    assert calls == [("connect", "smtp.example.com", 587, notify.SMTP_TIMEOUT_S), ("starttls",), ("login", "u", "p"),
                     ("send", "a@b.com", "Hi", "u")]


def test_smtp_failure_raises_notify_error(monkeypatch):
    def boom(*a, **k):
        raise smtplib.SMTPConnectError(421, "nope")

    monkeypatch.setattr(notify.smtplib, "SMTP", boom)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(notify.NotifyError, match="Could not send"):
        notify.send_email("a@b.com", "Hi", "body")


def test_header_injection_neutralized_or_rejected():
    # newlines in the subject are collapsed, so no extra header can be injected
    notify.send_email("a@b.com", "Hi\r\nBcc: evil@x.com", "body")
    with pytest.raises(notify.NotifyError, match="Invalid email header"):
        notify.send_email("a@b.com\r\nBcc: evil@x.com", "Hi", "body")


def test_registration_email_contains_token(capsys):
    assert notify.send_registration_email("g@example.com", "Joe's Garage", "TOKEN123") is True
    out = capsys.readouterr().out
    assert "TOKEN123" in out and "To: g@example.com" in out
