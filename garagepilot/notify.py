"""Email notifications to garages.

With SMTP_HOST unset, emails are printed to the console and logged instead of sent
(dry-run), so the whole flow works without a mail server.

Lead emails never contain the customer's name or contact details; those are only released
to the garage the customer chooses (see store.py).
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from .models import LeadBrief
from .store import Store

log = logging.getLogger(__name__)

SMTP_TIMEOUT_S = 15


class NotifyError(RuntimeError):
    """Raised when an email could not be sent."""


@dataclass
class NotifyResult:
    place_id: str
    garage_name: str
    ok: bool
    dry_run: bool = False
    error: str = ""


def is_dry_run() -> bool:
    return not os.environ.get("SMTP_HOST")


def _base_url() -> str:
    return os.environ.get("APP_BASE_URL", "http://localhost:8501").rstrip("/")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_lead_email(garage_name: str, brief: LeadBrief) -> tuple[str, str]:
    """(subject, body) for a new-lead email. Plain text; contains no customer contact details."""
    v, d = brief.vehicle, brief.diagnosis
    car = _one_line(f"{v.year} {v.make} {v.model}")
    drive = "NOT safe to drive (may need towing)" if not d.safe_to_drive else "Drivable"
    causes = "\n".join(f"  - {c.cause} ({c.probability:.0%})" for c in d.likely_causes) or "  - unknown"
    subject = f"New job near you: {car}, severity {d.severity}/5"
    body = f"""Hello {_one_line(garage_name)},

A customer near you needs help with their car. GaragePilot's AI pre-diagnosis is below so you
can prepare and quote before they arrive.

Vehicle:        {car}{f", {v.mileage:,} miles" if v.mileage else ""}
Distance:       {brief.distance_km:.1f} km from you
Severity:       {d.severity}/5, {drive}, urgency: {d.urgency.replace("_", " ")}
Skills needed:  {", ".join(d.required_specialties) or "general"}

Customer's description:
  {_one_line(brief.symptoms)}

AI summary:
  {_one_line(d.summary)}

Most likely causes:
{causes}

Estimated job value at your rate: {brief.est_cost.low:,.0f} to {brief.est_cost.high:,.0f} (AI estimate, not a quote)

Send your quote and earliest slot here:
  {_base_url()}/Garage_Portal?lead={brief.lead_id}

The customer's contact details are shared only if they choose your garage.

-- GaragePilot AI
"""
    return subject, body


def build_registration_email(garage_name: str, token: str) -> tuple[str, str]:
    subject = "Your GaragePilot garage access token"
    body = f"""Hello {_one_line(garage_name)},

Your garage is registered with GaragePilot. Use this token to sign in to the Garage Portal:

  {token}

Keep it private; anyone with it can see and answer your leads. It is shown only once.

Portal: {_base_url()}/Garage_Portal

-- GaragePilot AI
"""
    return subject, body


def send_email(to: str, subject: str, body: str) -> bool:
    """Send (or dry-run) one email. Returns True if dry-run. Raises NotifyError on failure."""
    msg = EmailMessage()
    try:
        msg["To"] = to
        msg["Subject"] = _one_line(subject)
        msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "garagepilot@localhost"
    except ValueError as exc:  # header injection attempt or malformed address
        raise NotifyError(f"Invalid email header: {exc}") from exc
    msg.set_content(body)

    if is_dry_run():
        text = f"--- DRY-RUN EMAIL (SMTP_HOST not set) ---\nTo: {to}\nSubject: {msg['Subject']}\n\n{body}--- END ---"
        print(text)
        log.info("dry-run email to %s: %s", to, msg["Subject"])
        return True

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        raise NotifyError(f"Could not send email to {to}: {exc}") from exc
    return False


def send_registration_email(to: str, garage_name: str, token: str) -> bool:
    subject, body = build_registration_email(garage_name, token)
    return send_email(to, subject, body)


def notify_lead(store: Store, lead_id: str) -> list[NotifyResult]:
    """Email every not-yet-notified registered garage on this lead.

    One garage's failure never blocks the others; failed garages stay un-notified so a retry
    only re-sends to them.
    """
    results = []
    for r in store.lead_recipients(lead_id):
        brief = store.lead_for_garage(lead_id, r["place_id"])
        try:
            subject, body = build_lead_email(r["name"], brief)
            dry = send_email(r["email"], subject, body)
        except NotifyError as exc:
            results.append(NotifyResult(r["place_id"], r["name"], ok=False, error=str(exc)))
            continue
        store.mark_notified(lead_id, r["place_id"])
        results.append(NotifyResult(r["place_id"], r["name"], ok=True, dry_run=dry))
    return results
