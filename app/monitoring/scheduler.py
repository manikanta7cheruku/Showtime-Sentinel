"""The Scheduler: many watches, one process, no overlap.

Why a hand-rolled asyncio loop instead of APScheduler? Because the interesting
behaviour here - per-watch non-overlap, per-watch backoff, live config reload,
graceful shutdown - is about 60 readable lines, and reading them teaches you
more than configuring a library would. APScheduler is a perfectly good choice
for cron-like schedules; see the README note if you want to swap it in.

TIMING HONESTY: with a 60 s interval, a change that happens right after a check
is noticed on the *next* check. End-to-end latency is therefore up to
~60 s + fetch time + parse time + Telegram delivery, i.e. typically 60-75 s and
more if the site is slow or a retry happens. This is not real-time, and nothing
in this design makes it real-time.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable

from app.config import Settings
from app.database.repositories import SqliteWatchRepository
from app.models import Watch
from app.monitoring.checker import AvailabilityChecker
from app.utils.retry import RetryPolicy, backoff_delay

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        *,
        settings: Settings,
        watch_repo: SqliteWatchRepository,
        checker: AvailabilityChecker,
        tick_seconds: float = 1.0,
        interval_scale: float = 1.0,     # test hook: 0.01 makes a 60s interval 0.6s
        clock: Callable[[], float] = time.monotonic,
        shutdown_timeout: float = 20.0,
    ) -> None:
        self.settings = settings
        self.watch_repo = watch_repo
        self.checker = checker
        self.tick_seconds = tick_seconds
        self.interval_scale = interval_scale
        self.clock = clock
        self.shutdown_timeout = shutdown_timeout

        self._stop = asyncio.Event()
        self._due: dict[int, float] = {}
        self._running: set[int] = set()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_checks)
        self.checks_started = 0
        self.max_concurrent_seen: dict[int, int] = {}

    # ------------------------------------------------------------------ #
    def request_stop(self, *_args) -> None:
        if not self._stop.is_set():
            logger.info("shutdown requested; finishing in-flight checks...")
            self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------ #
    async def run(self, *, max_cycles: int | None = None) -> None:
        """Main loop. `max_cycles` bounds the loop in tests; None = forever."""
        logger.info(
            "scheduler starting (tick=%.2fs, max_concurrent=%s, dry_run=%s, test_mode=%s)",
            self.tick_seconds, self.settings.max_concurrent_checks,
            self.settings.dry_run, self.settings.test_mode,
        )
        tasks: set[asyncio.Task] = set()
        cycles = 0

        while not self._stop.is_set():
            # Config is re-read every tick, so /pause, /setinterval and CLI
            # edits take effect without a restart.
            try:
                watches = self.watch_repo.list_watches(enabled_only=True)
            except Exception:                             # noqa: BLE001
                logger.exception("could not list watches; retrying next tick")
                watches = []

            active_ids = {w.id for w in watches}
            for stale in set(self._due) - active_ids:
                self._due.pop(stale, None)

            now = self.clock()
            for watch in watches:
                if watch.id in self._running:
                    # GUARD: never two concurrent checks of the same watch.
                    logger.debug("watch #%s still running; skipping this tick", watch.id)
                    continue
                if watch.id not in self._due:
                    self._due[watch.id] = now       # immediate check at startup
                if now >= self._due[watch.id]:
                    self._running.add(watch.id)
                    self.checks_started += 1
                    task = asyncio.create_task(self._run_one(watch), name=f"check-{watch.id}")
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            await self._sleep_tick()

        await self._drain(tasks)
        logger.info("scheduler stopped after %s checks", self.checks_started)

    async def _sleep_tick(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            pass

    async def _drain(self, tasks: set[asyncio.Task]) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout)
        for task in pending:
            logger.warning("cancelling check that outlived shutdown: %s", task.get_name())
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------ #
    async def _run_one(self, watch: Watch) -> None:
        """Run one check and schedule the next one. Never raises."""
        failures = watch.consecutive_failures
        try:
            async with self._semaphore:
                self._note_concurrency(watch.id, +1)
                report = await self.checker.check(watch)
            failures = 0 if report.ok else watch.consecutive_failures + 1
        except asyncio.CancelledError:
            raise
        except Exception:                                 # noqa: BLE001
            # ISOLATION: a crash here affects only this watch's schedule.
            logger.exception("watch #%s: unhandled error during check", watch.id)
            failures = watch.consecutive_failures + 1
        finally:
            self._note_concurrency(watch.id, -1)
            self._running.discard(watch.id)
            self._due[watch.id] = self.clock() + self._next_delay(watch, failures)

    def _note_concurrency(self, watch_id: int, delta: int) -> None:
        current = self.max_concurrent_seen.get(watch_id, 0)
        if delta > 0:
            self.max_concurrent_seen[watch_id] = current + 1
        else:
            self.max_concurrent_seen[watch_id] = max(0, current - 1)

    def _next_delay(self, watch: Watch, failures: int) -> float:
        base = max(watch.poll_interval_seconds, self.settings.min_poll_interval_seconds)
        if failures <= 0:
            return base * self.interval_scale            # success: back to normal
        policy = RetryPolicy(
            attempts=1,
            base_delay=base,
            max_delay=base * 8.0,
        )
        delay = backoff_delay(policy, min(failures, 8), rand=random.random)
        logger.info("watch #%s: %s consecutive failures -> next check in %.0fs (backoff+jitter)",
                    watch.id, failures, delay)
        return delay * self.interval_scale


def install_signal_handlers(scheduler: Scheduler) -> None:
    """SIGINT (Ctrl+C) and SIGTERM -> graceful stop. Works on Windows too."""
    import signal

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, scheduler.request_stop)
        except (NotImplementedError, AttributeError):
            # Windows ProactorEventLoop doesn't support add_signal_handler.
            signal.signal(sig, lambda *_: scheduler.request_stop())
