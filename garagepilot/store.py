"""SQLite persistence for registered garages, leads and quotes.

Privacy rules enforced here (not left to the UI):
- Customer name/contact are stored on the lead but only ever returned to the one garage
  the customer chose (`choose_garage`).
- Only garage-supplied data and Google `place_id` are stored for garages. Coordinates,
  ratings etc. are re-fetched from Places each search (Google's caching terms).
- Garage access tokens are stored as SHA-256 hashes; the raw token is returned once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    SPECIALTIES,
    Diagnosis,
    Garage,
    LeadBrief,
    Match,
    Quote,
    Range,
    Vehicle,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "garagepilot.db"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS garages (
    place_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    phone        TEXT,
    specialties  TEXT NOT NULL DEFAULT '[]',
    labor_rate   REAL,
    token_hash   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    vehicle           TEXT NOT NULL,
    symptoms          TEXT NOT NULL,
    diagnosis         TEXT NOT NULL,
    customer_name     TEXT,
    customer_contact  TEXT,
    chosen_place_id   TEXT
);
CREATE TABLE IF NOT EXISTS lead_garages (
    lead_id       TEXT NOT NULL REFERENCES leads(id),
    place_id      TEXT NOT NULL REFERENCES garages(place_id),
    distance_km   REAL NOT NULL,
    est_cost_low  REAL NOT NULL,
    est_cost_high REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'sent',
    notified_at   TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (lead_id, place_id)
);
CREATE TABLE IF NOT EXISTS quotes (
    lead_id        TEXT NOT NULL,
    place_id       TEXT NOT NULL,
    price          REAL NOT NULL,
    earliest_slot  TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (lead_id, place_id),
    FOREIGN KEY (lead_id, place_id) REFERENCES lead_garages(lead_id, place_id)
);
"""


class StoreError(ValueError):
    """Raised for invalid operations; the message is safe to show the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("GARAGEPILOT_DB") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        # A connection per operation: safe with Streamlit's per-session threads.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- garages ---------------------------------------------------------

    def register_garage(
        self,
        place_id: str,
        name: str,
        email: str,
        specialties: list[str],
        labor_rate: float | None = None,
        phone: str | None = None,
    ) -> str:
        """Claim a listing. Returns the access token, which is shown/emailed once and never stored."""
        email = email.strip()
        if not _EMAIL_RE.match(email):
            raise StoreError("Please enter a valid email address.")
        if not name.strip():
            raise StoreError("Garage name is required.")
        self._validate_rate(labor_rate)
        token = secrets.token_urlsafe(16)
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO garages (place_id, name, email, phone, specialties, labor_rate, token_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (place_id, name.strip(), email, phone, json.dumps(self._clean_specialties(specialties)),
                     labor_rate, _hash_token(token), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError("This garage has already been claimed.") from exc
        return token

    def authenticate(self, place_id: str, token: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT token_hash FROM garages WHERE place_id = ?", (place_id,)).fetchone()
        return bool(row) and hmac.compare_digest(row["token_hash"], _hash_token(token.strip()))

    def update_garage(
        self,
        place_id: str,
        *,
        email: str | None = None,
        phone: str | None = None,
        specialties: list[str] | None = None,
        labor_rate: float | None = None,
    ) -> None:
        sets, args = [], []
        if email is not None:
            if not _EMAIL_RE.match(email.strip()):
                raise StoreError("Please enter a valid email address.")
            sets.append("email = ?"), args.append(email.strip())
        if phone is not None:
            sets.append("phone = ?"), args.append(phone)
        if specialties is not None:
            sets.append("specialties = ?"), args.append(json.dumps(self._clean_specialties(specialties)))
        if labor_rate is not None:
            self._validate_rate(labor_rate)
            sets.append("labor_rate = ?"), args.append(labor_rate)
        if not sets:
            return
        with self._conn() as conn:
            cur = conn.execute(f"UPDATE garages SET {', '.join(sets)} WHERE place_id = ?", (*args, place_id))
        if cur.rowcount == 0:
            raise StoreError("Garage not found.")

    def get_garage(self, place_id: str) -> dict | None:
        """Registered garage record without the token hash; specialties decoded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT place_id, name, email, phone, specialties, labor_rate, created_at FROM garages WHERE place_id = ?",
                (place_id,),
            ).fetchone()
        if not row:
            return None
        return {**dict(row), "specialties": json.loads(row["specialties"])}

    def apply_registrations(self, garages: list[Garage]) -> list[Garage]:
        """Mark discovered garages that have registered and copy in their declared specialties/rate."""
        if not garages:
            return []
        ids = [g.place_id for g in garages]
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT place_id, specialties, labor_rate FROM garages WHERE place_id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        reg = {r["place_id"]: r for r in rows}
        out = []
        for g in garages:
            r = reg.get(g.place_id)
            out.append(
                g.model_copy(update={"registered": True, "specialties": json.loads(r["specialties"]), "labor_rate": r["labor_rate"]})
                if r else g
            )
        return out

    # ---- leads -----------------------------------------------------------

    def create_lead(
        self,
        vehicle: Vehicle,
        symptoms: str,
        diagnosis: Diagnosis,
        matches: list[Match],
        customer_name: str,
        customer_contact: str,
    ) -> str:
        """Create a lead and attach it to the *registered* garages among `matches`.

        Discovered-only garages have no contact channel and are skipped.
        """
        lead_id = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO leads (id, created_at, vehicle, symptoms, diagnosis, customer_name, customer_contact)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lead_id, now, vehicle.model_dump_json(), symptoms, diagnosis.model_dump_json(),
                 customer_name.strip() or None, customer_contact.strip() or None),
            )
            for m in matches:
                if m.garage.registered:
                    conn.execute(
                        "INSERT INTO lead_garages (lead_id, place_id, distance_km, est_cost_low, est_cost_high, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (lead_id, m.garage.place_id, m.distance_km, m.cost.low, m.cost.high, now),
                    )
        return lead_id

    def lead_recipients(self, lead_id: str) -> list[dict]:
        """Garages (with email) attached to a lead that haven't been notified yet."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT g.place_id, g.name, g.email FROM lead_garages lg JOIN garages g USING (place_id)"
                " WHERE lg.lead_id = ? AND lg.notified_at IS NULL",
                (lead_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, lead_id: str, place_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE lead_garages SET notified_at = ? WHERE lead_id = ? AND place_id = ?",
                (_now(), lead_id, place_id),
            )

    def garage_leads(self, place_id: str) -> list[LeadBrief]:
        """Inbox for one garage, newest first."""
        with self._conn() as conn:
            ids = [r["lead_id"] for r in conn.execute(
                "SELECT lead_id FROM lead_garages lg JOIN leads l ON l.id = lg.lead_id"
                " WHERE lg.place_id = ? ORDER BY l.created_at DESC, l.rowid DESC", (place_id,))]
        return [b for b in (self.lead_for_garage(i, place_id) for i in ids) if b]

    def lead_for_garage(self, lead_id: str, place_id: str, mark_viewed: bool = False) -> LeadBrief | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT l.*, lg.distance_km, lg.est_cost_low, lg.est_cost_high, lg.status FROM leads l"
                " JOIN lead_garages lg ON lg.lead_id = l.id WHERE l.id = ? AND lg.place_id = ?",
                (lead_id, place_id),
            ).fetchone()
            if not row:
                return None
            status = row["status"]
            if mark_viewed and status == "sent":
                conn.execute(
                    "UPDATE lead_garages SET status = 'viewed', updated_at = ? WHERE lead_id = ? AND place_id = ?",
                    (_now(), lead_id, place_id),
                )
                status = "viewed"
            quote = self._quote(conn, lead_id, place_id)
        chosen = row["chosen_place_id"] == place_id
        return LeadBrief(
            lead_id=lead_id,
            created_at=row["created_at"],
            vehicle=Vehicle.model_validate_json(row["vehicle"]),
            symptoms=row["symptoms"],
            diagnosis=Diagnosis.model_validate_json(row["diagnosis"]),
            distance_km=row["distance_km"],
            est_cost=Range(low=row["est_cost_low"], high=row["est_cost_high"]),
            status=status,
            quote=quote,
            chosen=chosen,
            customer_name=row["customer_name"] if chosen else None,
            customer_contact=row["customer_contact"] if chosen else None,
        )

    # ---- quotes ----------------------------------------------------------

    def submit_quote(self, lead_id: str, place_id: str, price: float, earliest_slot: str = "", note: str = "") -> None:
        if not price or price <= 0:
            raise StoreError("Enter a price greater than zero.")
        with self._conn() as conn:
            self._require_attached(conn, lead_id, place_id)
            if self._chosen(conn, lead_id):
                raise StoreError("The customer has already chosen a garage for this job.")
            conn.execute(
                "INSERT INTO quotes (lead_id, place_id, price, earliest_slot, note, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(lead_id, place_id) DO UPDATE SET price = excluded.price,"
                " earliest_slot = excluded.earliest_slot, note = excluded.note, created_at = excluded.created_at",
                (lead_id, place_id, price, earliest_slot.strip(), note.strip(), _now()),
            )
            conn.execute(
                "UPDATE lead_garages SET status = 'quoted', updated_at = ? WHERE lead_id = ? AND place_id = ?",
                (_now(), lead_id, place_id),
            )

    def decline_lead(self, lead_id: str, place_id: str) -> None:
        with self._conn() as conn:
            self._require_attached(conn, lead_id, place_id)
            conn.execute("DELETE FROM quotes WHERE lead_id = ? AND place_id = ?", (lead_id, place_id))
            conn.execute(
                "UPDATE lead_garages SET status = 'declined', updated_at = ? WHERE lead_id = ? AND place_id = ?",
                (_now(), lead_id, place_id),
            )

    def lead_quotes(self, lead_id: str) -> list[Quote]:
        """Quotes for the customer's lead, cheapest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT place_id FROM quotes WHERE lead_id = ? ORDER BY price, created_at", (lead_id,)
            ).fetchall()
            return [self._quote(conn, lead_id, r["place_id"]) for r in rows]

    def choose_garage(self, lead_id: str, place_id: str) -> None:
        """Customer picks a quote; from now on only that garage can see the customer's contact details."""
        with self._conn() as conn:
            if not self._quote(conn, lead_id, place_id):
                raise StoreError("That garage has not quoted this job.")
            conn.execute("UPDATE leads SET chosen_place_id = ? WHERE id = ?", (place_id, lead_id))

    def chosen_garage(self, lead_id: str) -> str | None:
        with self._conn() as conn:
            return self._chosen(conn, lead_id)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _clean_specialties(specialties: list[str]) -> list[str]:
        return [s for s in dict.fromkeys(x.strip().lower() for x in specialties) if s in SPECIALTIES]

    @staticmethod
    def _validate_rate(rate: float | None) -> None:
        if rate is not None and not (0 < rate <= 1000):
            raise StoreError("Hourly labor rate must be between 0 and 1000.")

    @staticmethod
    def _require_attached(conn: sqlite3.Connection, lead_id: str, place_id: str) -> None:
        if not conn.execute(
            "SELECT 1 FROM lead_garages WHERE lead_id = ? AND place_id = ?", (lead_id, place_id)
        ).fetchone():
            raise StoreError("This job was not sent to your garage.")

    @staticmethod
    def _chosen(conn: sqlite3.Connection, lead_id: str) -> str | None:
        row = conn.execute("SELECT chosen_place_id FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return row["chosen_place_id"] if row else None

    @staticmethod
    def _quote(conn: sqlite3.Connection, lead_id: str, place_id: str) -> Quote | None:
        row = conn.execute(
            "SELECT q.*, g.name AS garage_name FROM quotes q JOIN garages g USING (place_id)"
            " WHERE q.lead_id = ? AND q.place_id = ?",
            (lead_id, place_id),
        ).fetchone()
        if not row:
            return None
        return Quote(
            lead_id=lead_id, place_id=place_id, garage_name=row["garage_name"], price=row["price"],
            earliest_slot=row["earliest_slot"], note=row["note"], created_at=row["created_at"],
        )
