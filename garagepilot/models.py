"""Shared data models for GaragePilot AI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Shared vocabulary: diagnosis output, garage registration and matching all use these.
SPECIALTIES = (
    "brakes", "engine", "transmission", "electrical", "ac", "tires",
    "suspension", "exhaust", "battery", "ev", "diagnostics", "maintenance",
)


class Vehicle(BaseModel):
    make: str
    model: str
    year: int
    mileage: int | None = None


class Range(BaseModel):
    low: float
    high: float

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


class Cause(BaseModel):
    cause: str
    probability: float = Field(description="Rough likelihood between 0 and 1")


class Diagnosis(BaseModel):
    """Structured diagnosis produced by Claude from the customer's description."""

    summary: str = Field(description="One or two plain-language sentences for the customer")
    likely_causes: list[Cause]
    severity: int = Field(description="1 (cosmetic/minor) to 5 (dangerous, stop driving)")
    safe_to_drive: bool
    urgency: Literal["immediate", "this_week", "routine"]
    required_specialties: list[str] = Field(
        description="Lowercase skills needed, e.g. brakes, transmission, electrical, ac, tires, engine, ev"
    )
    est_labor_hours: Range
    est_parts_cost: Range
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="At most 2 questions, only if the description is too vague to diagnose",
    )


class Garage(BaseModel):
    place_id: str
    name: str
    lat: float
    lng: float
    rating: float | None = None
    review_count: int = 0
    open_now: bool | None = None
    phone: str | None = None
    website: str | None = None
    price_level: int | None = None  # Google Places: 0 (free) .. 4 (very expensive)
    specialties: list[str] = Field(default_factory=list)  # declared by registered garages
    labor_rate: float | None = None  # per hour, declared by registered garages
    registered: bool = False


class Quote(BaseModel):
    lead_id: str
    place_id: str
    garage_name: str
    price: float
    earliest_slot: str = ""
    note: str = ""
    created_at: str = ""


class LeadBrief(BaseModel):
    """What a garage sees about a lead. Customer contact is None until the customer picks this garage."""

    lead_id: str
    created_at: str
    vehicle: Vehicle
    symptoms: str
    diagnosis: Diagnosis
    distance_km: float
    est_cost: Range
    status: Literal["sent", "viewed", "quoted", "declined"]
    quote: Quote | None = None
    chosen: bool = False
    customer_name: str | None = None
    customer_contact: str | None = None


class Match(BaseModel):
    garage: Garage
    distance_km: float
    cost: Range
    score: float
    breakdown: dict[str, float]
    reason: str = ""
