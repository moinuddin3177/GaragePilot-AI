from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from garagepilot import diagnose as dg
from garagepilot.models import Cause, Diagnosis, Range, Vehicle

VEHICLE = Vehicle(make="Honda", model="Civic", year=2015, mileage=98000)


def make_diagnosis(**kw) -> Diagnosis:
    base = dict(
        summary="Likely worn brake pads.",
        likely_causes=[Cause(cause="Worn brake pads", probability=0.8)],
        severity=3,
        safe_to_drive=True,
        urgency="this_week",
        required_specialties=["brakes"],
        est_labor_hours=Range(low=1, high=2),
        est_parts_cost=Range(low=60, high=120),
    )
    base.update(kw)
    return Diagnosis(**base)


class FakeClient:
    """Stands in for anthropic.Anthropic; records the parse() call."""

    def __init__(self, parsed=None, stop_reason="end_turn", raises=None):
        self.calls = []
        self._result = SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, stop_details=None)
        self._raises = raises
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._result


def _status_error(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def test_request_shape_and_result():
    client = FakeClient(parsed=make_diagnosis())
    result = dg.diagnose(VEHICLE, "  squealing when I brake ", client=client)
    assert result.summary.startswith("Likely worn")
    call = client.calls[0]
    assert call["output_format"] is Diagnosis
    assert call["system"] == dg.SYSTEM_PROMPT
    text = call["messages"][0]["content"][-1]["text"]
    assert "2015 Honda Civic, 98,000 miles" in text
    assert "squealing when I brake" in text
    assert "final round" not in text


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("GARAGEPILOT_MODEL", "claude-opus-5")
    client = FakeClient(parsed=make_diagnosis())
    dg.diagnose(VEHICLE, "noise", client=client)
    assert client.calls[0]["model"] == "claude-opus-5"
    monkeypatch.delenv("GARAGEPILOT_MODEL")
    dg.diagnose(VEHICLE, "noise", client=client)
    assert client.calls[1]["model"] == dg.DEFAULT_MODEL


def test_answers_included_and_final_round_forced():
    client = FakeClient(parsed=make_diagnosis(follow_up_questions=["still asking?"]))
    answers = [(f"q{i}", f"a{i}") for i in range(dg.MAX_ANSWERED_QUESTIONS)]
    result = dg.diagnose(VEHICLE, "noise", answers=answers, client=client)
    text = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "Q: q0" in text and "A: a3" in text and "final round" in text
    assert result.follow_up_questions == []  # dropped even if the model asks again


def test_follow_up_questions_capped_before_final_round():
    client = FakeClient(parsed=make_diagnosis(follow_up_questions=["a?", "b?", "c?"]))
    assert dg.diagnose(VEHICLE, "weird", client=client).follow_up_questions == ["a?", "b?"]


def test_image_block_precedes_text():
    client = FakeClient(parsed=make_diagnosis())
    dg.diagnose(VEHICLE, "warning light", image=(b"\x89PNG", "image/png"), client=client)
    blocks = client.calls[0]["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image", "text"]
    assert blocks[0]["source"]["media_type"] == "image/png"


def test_normalize_enforces_safety_invariants():
    d = dg._normalize(make_diagnosis(severity=5, safe_to_drive=True, urgency="routine"), final_round=False)
    assert (d.severity, d.safe_to_drive, d.urgency) == (5, False, "immediate")
    d = dg._normalize(make_diagnosis(severity=9), final_round=False)
    assert d.severity == 5
    d = dg._normalize(make_diagnosis(severity=-2), final_round=False)
    assert d.severity == 1
    d = dg._normalize(make_diagnosis(safe_to_drive=False, urgency="routine", severity=2), final_round=False)
    assert d.urgency == "immediate"


def test_normalize_specialties_and_ranges():
    d = dg._normalize(
        make_diagnosis(
            required_specialties=[" Brakes ", "brakes", "witchcraft", "AC"],
            est_labor_hours=Range(low=3, high=1),
            est_parts_cost=Range(low=-5, high=50),
        ),
        final_round=False,
    )
    assert d.required_specialties == ["brakes", "ac"]
    assert (d.est_labor_hours.low, d.est_labor_hours.high) == (1, 3)
    assert d.est_parts_cost.low == 0


def test_empty_description_rejected_without_api_call():
    client = FakeClient(parsed=make_diagnosis())
    with pytest.raises(dg.DiagnosisError, match="describe"):
        dg.diagnose(VEHICLE, "   ", client=client)
    assert client.calls == []


def test_refusal_and_incomplete_output():
    with pytest.raises(dg.DiagnosisError, match="could not process"):
        dg.diagnose(VEHICLE, "x", client=FakeClient(parsed=None, stop_reason="refusal"))
    with pytest.raises(dg.DiagnosisError, match="incomplete"):
        dg.diagnose(VEHICLE, "x", client=FakeClient(parsed=None, stop_reason="max_tokens"))


def test_no_credentials_at_request_time_gives_clear_message():
    # Exact text the SDK raises on the request when the client was built without any credentials.
    err = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set."')
    with pytest.raises(dg.DiagnosisError, match="No Anthropic API key"):
        dg.diagnose(VEHICLE, "noise", client=FakeClient(raises=err))
    with pytest.raises(TypeError, match="unrelated"):  # other TypeErrors are real bugs, not hidden
        dg.diagnose(VEHICLE, "noise", client=FakeClient(raises=TypeError("unrelated")))


def test_missing_api_key_gives_clear_message(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "nonexistent-dir-for-test")
    with pytest.raises(dg.DiagnosisError, match="No Anthropic API key"):
        dg.diagnose(VEHICLE, "noise")  # real client construction, no network call


@pytest.mark.parametrize(
    "exc, message",
    [
        (_status_error(anthropic.AuthenticationError, 401), "API key"),
        (_status_error(anthropic.RateLimitError, 429), "busy"),
        (_status_error(anthropic.InternalServerError, 500), "error \\(500\\)"),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), "internet"),
    ],
)
def test_api_errors_become_user_safe_messages(exc, message):
    with pytest.raises(dg.DiagnosisError, match=message):
        dg.diagnose(VEHICLE, "x", client=FakeClient(raises=exc))
