"""The Watch: one user intent, validated once, trusted everywhere after."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AvailabilityState
from app.utils.timeutil import normalise_hhmm, parse_hhmm

#: Floor enforced by the *model*. Config can require more, never less.
ABSOLUTE_MIN_INTERVAL = 5


class WatchSpec(BaseModel):
    """What the user asks for. No database fields, no runtime state."""

    model_config = ConfigDict(str_strip_whitespace=True)

    movie_name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    target_date: date
    theatre: str = Field(min_length=1, max_length=200)

    screen: str | None = Field(default=None, max_length=100)
    showtime: str | None = None                  # exact "HH:MM"
    time_from: str | None = None                 # or a range ...
    time_to: str | None = None
    seat_category: str | None = Field(default=None, max_length=100)
    exact_seats: list[str] = Field(default_factory=list)
    min_seats: int = Field(default=1, ge=1, le=50)

    poll_interval_seconds: int = Field(default=60, ge=ABSOLUTE_MIN_INTERVAL, le=86_400)
    enabled: bool = True
    notify_once: bool = True
    source: str = Field(default="fake", pattern=r"^(fake|bookmyshow)$")
    #: A URL YOU opened in your own browser and verified. Never auto-generated.
    source_url: str | None = None

    @field_validator("showtime", "time_from", "time_to")
    @classmethod
    def _check_times(cls, v: str | None) -> str | None:
        return None if v in (None, "") else normalise_hhmm(v)

    @field_validator("exact_seats", mode="before")
    @classmethod
    def _split_seats(cls, v):
        """Accept "A1, A2" or ["a1","a2"]; store canonical, sorted, de-duped."""
        if v in (None, ""):
            return []
        if isinstance(v, str):
            v = v.split(",")
        return sorted({str(s).strip().upper() for s in v if str(s).strip()})

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("source_url must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def _cross_checks(self):
        if self.time_from and self.time_to:
            if parse_hhmm(self.time_from) >= parse_hhmm(self.time_to):
                raise ValueError("time_from must be earlier than time_to")
        if self.showtime and (self.time_from or self.time_to):
            raise ValueError("use either showtime (exact) or time_from/time_to (range), not both")
        if (self.time_from is None) != (self.time_to is None):
            raise ValueError("time_from and time_to must be given together")
        if self.exact_seats and self.min_seats > len(self.exact_seats):
            raise ValueError("min_seats cannot exceed the number of exact_seats requested")
        return self

    def time_window_label(self) -> str:
        if self.showtime:
            return self.showtime
        if self.time_from and self.time_to:
            return f"{self.time_from}-{self.time_to}"
        return "any showtime"


class Watch(WatchSpec):
    """A WatchSpec plus everything the monitor remembers about it."""

    id: int | None = None
    current_state: AvailabilityState = AvailabilityState.UNKNOWN
    current_hash: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    notification_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def describe(self) -> str:
        bits = [f"#{self.id}", self.movie_name, self.city, str(self.target_date), self.theatre]
        if self.screen:
            bits.append(self.screen)
        bits.append(self.time_window_label())
        return " | ".join(bits)
