"""The vocabulary of the whole system."""
from __future__ import annotations

from enum import StrEnum


class AvailabilityState(StrEnum):
    """Explicit states. `UNKNOWN` is the starting point for every new watch."""

    UNKNOWN = "UNKNOWN"                        # never checked / check skipped
    NOT_FOUND = "NOT_FOUND"                    # movie not listed in that city
    DATE_NOT_AVAILABLE = "DATE_NOT_AVAILABLE"  # movie exists, your date isn't offered
    SHOW_NOT_AVAILABLE = "SHOW_NOT_AVAILABLE"  # date OK, no show matches your filters
    BOOKING_NOT_OPEN = "BOOKING_NOT_OPEN"      # show listed, "Coming soon"/no booking link
    BOOKING_OPEN = "BOOKING_OPEN"              # bookable, but seat counts unknown
    NO_SEATS_AVAILABLE = "NO_SEATS_AVAILABLE"  # seat data known, nothing matches
    SEATS_AVAILABLE = "SEATS_AVAILABLE"        # seat data known, >= min_seats match
    ERROR = "ERROR"                            # our fault / transient failure
    BLOCKED = "BLOCKED"                        # site refused us (Cloudflare etc.)


#: States that mean "you can act right now" -> worth a Telegram message.
ACTIONABLE_STATES = frozenset({AvailabilityState.BOOKING_OPEN, AvailabilityState.SEATS_AVAILABLE})

#: States meaning "the check itself did not succeed".
FAILURE_STATES = frozenset({AvailabilityState.ERROR, AvailabilityState.BLOCKED})


def is_show_available(state: AvailabilityState) -> bool:
    """You asked about a SHOW_NOT_AVAILABLE -> SHOW_AVAILABLE transition.

    There is deliberately no SHOW_AVAILABLE state: once a show *is* found we
    always know something more useful (BOOKING_NOT_OPEN / BOOKING_OPEN /
    NO_SEATS_AVAILABLE / SEATS_AVAILABLE). "Show available" is therefore a
    *predicate over states*, not a state of its own. Keeping the enum free of
    redundant members keeps the transition table small and testable.
    """
    return state in {
        AvailabilityState.BOOKING_NOT_OPEN,
        AvailabilityState.BOOKING_OPEN,
        AvailabilityState.NO_SEATS_AVAILABLE,
        AvailabilityState.SEATS_AVAILABLE,
    }


class FetchStatus(StrEnum):
    """What the *transport* did, independent of what it found."""

    OK = "OK"                      # we got a payload we can parse
    NOT_FOUND = "NOT_FOUND"        # 404 / page says no such movie
    BLOCKED = "BLOCKED"            # anti-bot challenge, 403, rate-limit page
    RATE_LIMITED = "RATE_LIMITED"  # *our own* local throttle said "too soon"
    ERROR = "ERROR"                # timeout, parse failure, unexpected markup


class NotificationKind(StrEnum):
    STATE_CHANGE = "STATE_CHANGE"
    ERROR = "ERROR"
    TEST = "TEST"
