from __future__ import annotations

from enum import Enum
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, field_validator
from datetime import datetime

class EventStatus(str, Enum):
    # Extended lifecycle (new model)
    draft = 'draft'
    coming_soon = 'coming_soon'
    open = 'open'
    closed = 'closed'
    matched = 'matched'
    released = 'released'
    cancelled = 'cancelled'
    # Legacy value (kept for backward compatibility in persisted records / migrations)
    published = 'published'

    @classmethod
    def normalize(cls, v: Optional[str]) -> 'EventStatus':
        if not v:
            return cls.draft
        value = str(v).lower().strip()
        # map legacy -> new canonical names
        if value == 'published':
            value = 'open'
        if value not in {m.value for m in cls}:
            return cls.draft
        # If value is legacy published we already mapped; ensure member exists
        return cls(value)  # type: ignore[arg-type]


class MatchingStatus(str, Enum):
    not_started = 'not_started'
    in_progress = 'in_progress'
    proposed = 'proposed'
    finalized = 'finalized'
    archived = 'archived'


class RegistrationStatus(str, Enum):
    draft = 'draft'
    pending = 'pending'
    invited = 'invited'
    confirmed = 'confirmed'
    paid = 'paid'  # kept for compatibility but payments use PaymentStatus
    cancelled_by_user = 'cancelled_by_user'
    cancelled_admin = 'cancelled_admin'
    refunded = 'refunded'
    expired = 'expired'


class PaymentStatus(str, Enum):
    created = 'created'
    pending = 'pending'
    in_process = 'in_process'
    succeeded = 'succeeded'
    failed = 'failed'
    refunded = 'refunded'


class DietType(str, Enum):
    omnivore = 'omnivore'
    vegetarian = 'vegetarian'
    vegan = 'vegan'
    pescatarian = 'pescatarian'
    gluten_free = 'gluten_free'
    lactose_free = 'lactose_free'
    other = 'other'


class Location(BaseModel):
    address_encrypted: Optional[str] = None
    address_public: Optional[str] = None
    point: Optional[Dict[str, Any]] = None  # GeoJSON { type: 'Point', coordinates: [lon, lat] }
    reveal_at: Optional[datetime] = None


class EventBase(BaseModel):
    title: str
    description: Optional[str] = None
    date: Optional[str] = None
    start_at: Optional[datetime] = None
    capacity: Optional[int] = None
    fee_cents: Optional[int] = 0
    registration_deadline: Optional[datetime] = None
    payment_deadline: Optional[datetime] = None
    after_party_location: Optional[Location] = None


class EventCreate(EventBase):
    organizer_id: Optional[str] = None
    status: EventStatus = EventStatus.draft


class EventOut(EventBase):
    id: Optional[str]
    attendee_count: int = 0
    status: EventStatus = EventStatus.draft
    matching_status: MatchingStatus = MatchingStatus.not_started
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator('status', mode='before')
    @classmethod
    def _normalize_status(cls, v):  # type: ignore[override]
        try:
            return EventStatus.normalize(v)
        except Exception:
            return EventStatus.draft


class RegistrationBase(BaseModel):
    event_id: str
    user_id: Optional[str] = None
    user_email_snapshot: Optional[EmailStr] = None
    team_id: Optional[str] = None
    team_size: int = 1
    preferences: Optional[Dict[str, Any]] = None
    diet: Optional[DietType] = DietType.omnivore
    allergies: Optional[List[str]] = None


class RegistrationCreate(RegistrationBase):
    invited_emails: Optional[List[EmailStr]] = None


class RegistrationOut(RegistrationBase):
    id: Optional[str]
    status: RegistrationStatus = RegistrationStatus.pending
    invitation_id: Optional[str] = None
    payment_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaymentBase(BaseModel):
    registration_id: str
    amount_cents: int
    currency: str = 'EUR'


class PaymentOut(PaymentBase):
    id: Optional[str]
    provider: Optional[str] = None
    provider_payment_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    status: PaymentStatus = PaymentStatus.created
    created_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None


class MatchGroup(BaseModel):
    group_id: Optional[str]
    members: List[Dict[str, Any]] = []  # { user_id, registration_id }
    route: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None


class MatchDoc(BaseModel):
    id: Optional[str]
    event_id: str
    groups: List[MatchGroup] = []
    status: MatchingStatus = MatchingStatus.proposed
    version: int = 1
    finalized_by: Optional[str] = None
    finalized_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
