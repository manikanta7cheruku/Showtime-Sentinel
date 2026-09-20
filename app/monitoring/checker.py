"""AvailabilityChecker: one complete check of one watch, start to finish.

This is the data-flow spine of the project:

    Watch -> source.fetch()            (with timeout + retries + backoff)
          -> FetchOutcome
          -> normalize()               (filter, derive state, hash)
          -> NormalizedResult
          -> snapshot saved to SQLite
          -> ChangeDetector.decide()   (previous state from SQLite)
          -> maybe one notification    (deduped, recorded)
          -> watch row updated, monitor_run recorded

Every exception is caught here. A watch that explodes must never take down the
scheduler or its sibling watches.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.database import NotificationRepository, RunRepository, SnapshotRepository
from app.database.repositories import SqliteWatchRepository
from app.models import (
    AvailabilityState, FetchOutcome, FetchStatus, NormalizedResult,
    NotificationKind, TransitionDecision, Watch,
)
from app.monitoring.change_detector import ChangeDetector
from app.monitoring.normalizer import normalize
from app.notifications.base import NotificationService
from app.sources.base import SourceRegistry
from app.utils.retry import RetryPolicy, run_with_retries
from app.utils.timeutil import utc_now

logger = logging.getLogger(__name__)


@dataclass
class CheckReport:
    watch_id: int
    state: AvailabilityState
    decision: TransitionDecision
    notified: bool
    attempts: int
    result: NormalizedResult

    @property
    def ok(self) -> bool:
        return not self.result.is_failure


class AvailabilityChecker:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: SourceRegistry,
        watch_repo: SqliteWatchRepository,
        snapshots: SnapshotRepository,
        notifications: NotificationRepository,
        runs: RunRepository,
        notifier: NotificationService,
        detector: ChangeDetector | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.watch_repo = watch_repo
        self.snapshots = snapshots
        self.notifications = notifications
        self.runs = runs
        self.notifier = notifier
        self.detector = detector or ChangeDetector(
            error_notify_threshold=settings.error_notify_threshold
        )

    # ------------------------------------------------------------------ #
    async def check(self, watch: Watch) -> CheckReport:
        started = utc_now()
        attempts = 1

        # --- 1. fetch -----------------------------------------------------
        try:
            source = self.registry.get(watch.source)
        except KeyError as exc:
            outcome = FetchOutcome(status=FetchStatus.ERROR, source_name=watch.source, error=str(exc))
        else:
            policy = RetryPolicy(
                attempts=self.settings.max_retries,
                base_delay=self.settings.backoff_base_seconds,
                max_delay=self.settings.backoff_max_seconds,
                timeout=self.settings.fetch_timeout_seconds,
            )
            counter = {"n": 0}

            async def operation():
                counter["n"] += 1
                return await source.fetch(watch)

            def on_retry(attempt: int, exc: BaseException, delay: float) -> None:
                logger.warning(
                    "watch #%s fetch attempt %s failed (%s: %s); retrying in %.1fs",
                    watch.id, attempt, type(exc).__name__, exc, delay,
                )

            try:
                outcome = await run_with_retries(operation, policy, on_retry=on_retry)
            except Exception as exc:                      # noqa: BLE001
                logger.error("watch #%s: all %s fetch attempts failed: %s",
                             watch.id, policy.attempts, exc)
                outcome = FetchOutcome(
                    status=FetchStatus.ERROR, source_name=watch.source,
                    error=f"{type(exc).__name__}: {exc}",
                )
            attempts = counter["n"] or 1

        # --- 2. normalize (never allowed to raise out of here) ------------
        try:
            result = normalize(watch, outcome)
        except Exception as exc:                          # noqa: BLE001
            logger.exception("watch #%s: normalization failed", watch.id)
            result = NormalizedResult(
                state=AvailabilityState.ERROR, source_name=watch.source,
                error=f"normalization failed: {type(exc).__name__}: {exc}",
            )

        # --- 3. persist the snapshot --------------------------------------
        try:
            self.snapshots.add(watch.id, result)
        except Exception:                                 # noqa: BLE001
            logger.exception("watch #%s: could not store snapshot", watch.id)

        # --- 4. decide ----------------------------------------------------
        decision = self.detector.decide(watch, result)
        logger.info("watch #%s check: %s | %s | %s",
                    watch.id, result.summary(), decision.label, decision.reason)

        # --- 5. notify (deduped) ------------------------------------------
        notified = False
        if decision.should_notify and decision.dedupe_key:
            if self.notifications.exists_within(
                decision.dedupe_key, self.settings.renotify_cooldown_seconds
            ):
                logger.info("watch #%s: suppressed duplicate notification (key=%s)",
                            watch.id, decision.dedupe_key)
            else:
                notified = await self._send(watch, result, decision)

        # --- 6. update watch + run log ------------------------------------
        try:
            self.watch_repo.record_check(watch.id, result, notified=notified)
            if notified and watch.notify_once and decision.kind is NotificationKind.STATE_CHANGE:
                self.watch_repo.set_enabled(watch.id, False)
                logger.info("watch #%s disabled automatically (notify_once=True)", watch.id)
        except Exception:                                 # noqa: BLE001
            logger.exception("watch #%s: could not update watch row", watch.id)

        finished = utc_now()
        try:
            self.runs.add(
                watch_id=watch.id, started_at=started, finished_at=finished,
                state=result.state.value, ok=not result.is_failure,
                notified=notified, attempts=attempts, error=result.error,
            )
        except Exception:                                 # noqa: BLE001
            logger.exception("watch #%s: could not record monitor run", watch.id)

        return CheckReport(watch_id=watch.id, state=result.state, decision=decision,
                           notified=notified, attempts=attempts, result=result)

    # ------------------------------------------------------------------ #
    async def _send(self, watch: Watch, result: NormalizedResult, decision) -> bool:
        try:
            if decision.kind is NotificationKind.ERROR:
                sent = await self.notifier.send_error(watch, result, decision)
            else:
                sent = await self.notifier.send_booking_open(watch, result, decision)
        except Exception as exc:                          # noqa: BLE001
            logger.exception("watch #%s: notification failed", watch.id)
            self.notifications.add(
                watch_id=watch.id, kind=decision.kind, dedupe_key=decision.dedupe_key,
                channel=self.notifier.channel, delivered=False,
                previous_state=decision.previous_state.value,
                new_state=decision.new_state.value, error=str(exc),
            )
            return False

        # Recorded even when delivery failed: the row is our audit trail. Only a
        # *delivered* message writes a dedupe-blocking row, so a Telegram outage
        # doesn't cost you the alert - the next successful check re-sends.
        self.notifications.add(
            watch_id=watch.id, kind=decision.kind,
            dedupe_key=decision.dedupe_key if sent else f"failed|{decision.dedupe_key}",
            channel=self.notifier.channel, delivered=sent,
            previous_state=decision.previous_state.value, new_state=decision.new_state.value,
            message=decision.reason,
        )
        return sent
