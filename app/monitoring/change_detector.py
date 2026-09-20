"""Should this check produce a Telegram message?

Two independent guards, both required:

  GUARD 1 - a *meaningful transition*: the state must have improved into
  something you can act on. Polling every 60 s must not mean a message every
  60 s, so "same state as last time" is silence, by construction.

  GUARD 2 - *deduplication*: we remember a dedupe key
  "<watch>|<from>-><to>|<hash>" in the notification_events table. Even if the
  state machine somehow re-fires (restart, clock change, DB restored from a
  backup), the same key inside the cooldown window is dropped.

Because the previous state is read from SQLite, restarting the process does not
re-notify: the bot comes back up already knowing it told you.
"""
from __future__ import annotations

import logging

from app.models import (
    ACTIONABLE_STATES, FAILURE_STATES, AvailabilityState, NormalizedResult,
    NotificationKind, TransitionDecision, Watch, is_show_available,
)

logger = logging.getLogger(__name__)


class ChangeDetector:
    def __init__(self, *, error_notify_threshold: int = 3) -> None:
        self.error_notify_threshold = error_notify_threshold

    # ------------------------------------------------------------------ #
    def decide(self, watch: Watch, result: NormalizedResult) -> TransitionDecision:
        previous = watch.current_state
        new = result.state
        changed = previous != new

        # A skipped check (throttled) teaches us nothing: report no change.
        if new is AvailabilityState.UNKNOWN:
            return TransitionDecision(
                changed=False, should_notify=False, previous_state=previous, new_state=new,
                reason="check produced no information (skipped or throttled)",
            )

        # --- failures -----------------------------------------------------
        if new in FAILURE_STATES:
            streak = watch.consecutive_failures + 1
            if streak == self.error_notify_threshold:
                return TransitionDecision(
                    changed=changed, should_notify=True, kind=NotificationKind.ERROR,
                    previous_state=previous, new_state=new,
                    reason=f"{streak} consecutive failures reached the alert threshold",
                    dedupe_key=f"{watch.id}|error|{new.value}|streak{streak}",
                )
            return TransitionDecision(
                changed=changed, should_notify=False, previous_state=previous, new_state=new,
                reason=f"failure #{streak}; alerting at {self.error_notify_threshold}",
            )

        # --- good news ----------------------------------------------------
        if changed and self._is_improvement(previous, new):
            return TransitionDecision(
                changed=True, should_notify=True, kind=NotificationKind.STATE_CHANGE,
                previous_state=previous, new_state=new,
                reason=self._describe(previous, new),
                dedupe_key=f"{watch.id}|{previous.value}->{new.value}|{result.availability_hash}",
            )

        if changed:
            return TransitionDecision(
                changed=True, should_notify=False, previous_state=previous, new_state=new,
                reason="state changed but not into something actionable",
            )

        return TransitionDecision(
            changed=False, should_notify=False, previous_state=previous, new_state=new,
            reason="no state change",
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_improvement(previous: AvailabilityState, new: AvailabilityState) -> bool:
        """Only transitions INTO an actionable state are worth waking you up.

        Covers the cases you listed:
          BOOKING_NOT_OPEN   -> BOOKING_OPEN      (yes)
          NO_SEATS_AVAILABLE -> SEATS_AVAILABLE   (yes)
          SHOW_NOT_AVAILABLE -> BOOKING_OPEN/SEATS_AVAILABLE ("show became
                                available", see is_show_available() in enums.py)
          BOOKING_OPEN       -> SEATS_AVAILABLE   (yes: more specific good news)
          SEATS_AVAILABLE    -> NO_SEATS_AVAILABLE (no: that's bad news, logged only)
          anything           -> ERROR/BLOCKED      (handled above)
        """
        if new not in ACTIONABLE_STATES:
            return False
        if previous is AvailabilityState.SEATS_AVAILABLE and new is AvailabilityState.BOOKING_OPEN:
            return False        # we already told you something stronger
        return True

    @staticmethod
    def _describe(previous: AvailabilityState, new: AvailabilityState) -> str:
        if previous is AvailabilityState.BOOKING_NOT_OPEN and new is AvailabilityState.BOOKING_OPEN:
            return "booking just opened"
        if new is AvailabilityState.SEATS_AVAILABLE:
            if previous is AvailabilityState.NO_SEATS_AVAILABLE:
                return "seats freed up (was sold out)"
            return "matching seats are available"
        if not is_show_available(previous) and new in ACTIONABLE_STATES:
            return "your show appeared and is bookable"
        return f"{previous.value} -> {new.value}"