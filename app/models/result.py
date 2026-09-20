"""The *source contract*: every adapter must produce these shapes.

FakeMovieSource and BookMyShowSource both emit a payload dict that parses into
RawShow objects. Nothing downstream knows or cares which one produced it - that
is what makes the fake source a real test of the real pipeline.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AvailabilityState, FetchStatus, NotificationKind
from app.utils.timeutil import normalise_hhmm, utc_now


class SeatCategory(BaseModel):
    """`available_seats=None` means "the page did not tell us" - which is very
    different from `0` ("sold out"). Keeping None distinct is what lets us
    separate BOOKING_OPEN from NO_SEATS_AVAILABLE honestly."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = "GENERAL"
    available_seats: int | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    seat_ids: list[str] = Field(default_factory=list)

    @field_validator("seat_ids", mode="before")
    @classmethod
    def _canon(cls, v):
        if not v:
            return []
        return sorted({str(s).strip().upper() for s in v if str(s).strip()})

    @field_validator("name")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper() or "GENERAL"


class RawShow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    theatre: str
    screen: str | None = None
    showtime: str | None = None
    booking_open: bool = False
    categories: list[SeatCategory] = Field(default_factory=list)

    @field_validator("showtime")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        try:
            return normalise_hhmm(v)
        except ValueError:
            return v  # keep the raw value; filters will simply not match it

    @property
    def known_seat_count(self) -> int | None:
        counts = [c.available_seats for c in self.categories if c.available_seats is not None]
        return sum(counts) if counts else None

    def sort_key(self) -> tuple[str, str, str]:
        return (self.theatre.casefold(), (self.screen or "").casefold(), self.showtime or "")


class SourcePayload(BaseModel):
    """The document a source returns on FetchStatus.OK."""

    movie_found: bool = True
    date_available: bool = True
    booking_open: bool = False          # site-level hint ("Coming soon" banner)
    booking_url: str | None = None      # ONLY if read from a real link/verified input
    shows: list[RawShow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FetchOutcome(BaseModel):
    """Transport-level result. Never contains a state - that's state.py's job."""

    status: FetchStatus
    source_name: str
    payload: SourcePayload | None = None
    url: str | None = None
    error: str | None = None
    fetched_at: datetime = Field(default_factory=utc_now)


class NormalizedResult(BaseModel):
    """What the monitor stores and compares. Deterministic by construction."""

    state: AvailabilityState
    source_name: str
    matched_shows: list[RawShow] = Field(default_factory=list)
    all_show_count: int = 0
    matched_seat_count: int | None = None
    matched_seat_ids: list[str] = Field(default_factory=list)
    availability_hash: str = ""
    booking_url: str | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)

    @property
    def is_failure(self) -> bool:
        from app.models.enums import FAILURE_STATES
        return self.state in FAILURE_STATES

    def summary(self) -> str:
        parts = [self.state.value, f"{len(self.matched_shows)}/{self.all_show_count} shows"]
        if self.matched_seat_count is not None:
            parts.append(f"{self.matched_seat_count} seats")
        if self.error:
            parts.append(f"error={self.error[:80]}")
        return ", ".join(parts)


class TransitionDecision(BaseModel):
    """ChangeDetector's verdict - a value object, easy to assert on in tests."""

    changed: bool
    should_notify: bool
    kind: NotificationKind | None = None
    previous_state: AvailabilityState
    new_state: AvailabilityState
    reason: str
    dedupe_key: str | None = None

    @property
    def label(self) -> str:
        return f"{self.previous_state.value} -> {self.new_state.value}"
