import asyncio

import pytest

from app.models import AvailabilityState
from app.monitoring.scheduler import Scheduler
from app.sources.fake import FakeMovieSource
from tests.conftest import make_checker


class SlowChecker:
    """Records overlap: if two checks of the same watch ever run together, we see it."""

    def __init__(self, delay: float = 0.15):
        self.delay = delay
        self.inflight: dict[int, int] = {}
        self.max_parallel_per_watch = 0
        self.calls = 0

    async def check(self, watch):
        self.calls += 1
        self.inflight[watch.id] = self.inflight.get(watch.id, 0) + 1
        self.max_parallel_per_watch = max(self.max_parallel_per_watch, self.inflight[watch.id])
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.inflight[watch.id] -= 1

        class Report:
            ok = True
        return Report()


async def test_no_overlapping_checks_for_the_same_watch(settings, repos, spec):
    repos["watches"].create(spec.model_copy(update={"poll_interval_seconds": 5}))
    slow = SlowChecker(delay=0.2)
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=slow,
                          tick_seconds=0.01, interval_scale=0.0)  # always "due"
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.5)
    scheduler.request_stop()
    await task
    assert slow.max_parallel_per_watch == 1, "the same watch ran twice at once"
    assert slow.calls >= 2


async def test_immediate_check_at_startup(settings, repos, spec, notifier):
    repos["watches"].create(spec.model_copy(update={"poll_interval_seconds": 3600}))
    checker = make_checker(settings, repos, notifier, FakeMovieSource())
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=checker,
                          tick_seconds=0.01)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.2)
    scheduler.request_stop()
    await task
    # Despite a 1-hour interval, the first check happened right away.
    assert repos["watches"].list_watches()[0].last_checked_at is not None


async def test_disabled_watches_are_skipped(settings, repos, spec):
    watch = repos["watches"].create(spec)
    repos["watches"].set_enabled(watch.id, False)
    slow = SlowChecker(delay=0.01)
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=slow,
                          tick_seconds=0.01)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.15)
    scheduler.request_stop()
    await task
    assert slow.calls == 0


async def test_one_failing_watch_does_not_stop_the_others(settings, repos, spec, notifier):
    good = repos["watches"].create(spec.model_copy(update={"movie_name": "Good"}))
    bad = repos["watches"].create(spec.model_copy(update={"movie_name": "Bad"}))

    source = FakeMovieSource(
        ("SEATS_AVAILABLE",),
        per_watch_scripts={bad.id: ["ERROR"]},
        raise_on_error_step=True,        # the bad watch RAISES, not just returns ERROR
    )
    checker = make_checker(settings, repos, notifier, source)
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=checker,
                          tick_seconds=0.01, interval_scale=0.001)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.6)
    scheduler.request_stop()
    await task

    good_after = repos["watches"].get(good.id)
    bad_after = repos["watches"].get(bad.id)
    assert good_after.current_state is AvailabilityState.SEATS_AVAILABLE
    assert bad_after.current_state is AvailabilityState.ERROR
    assert bad_after.consecutive_failures >= 1
    assert good_after.last_success_at is not None


async def test_graceful_stop_waits_for_inflight_work(settings, repos, spec):
    repos["watches"].create(spec)
    slow = SlowChecker(delay=0.2)
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=slow,
                          tick_seconds=0.01)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    scheduler.request_stop()
    await asyncio.wait_for(task, timeout=2)
    assert all(count == 0 for count in slow.inflight.values())


def test_backoff_extends_the_next_check_after_failures(settings, repos, spec):
    watch = repos["watches"].create(spec)
    scheduler = Scheduler(settings=settings, watch_repo=repos["watches"], checker=SlowChecker())
    normal = scheduler._next_delay(repos["watches"].get(watch.id), failures=0)
    backed_off = scheduler._next_delay(repos["watches"].get(watch.id), failures=3)
    assert backed_off > normal
    assert scheduler._next_delay(repos["watches"].get(watch.id), failures=0) == normal  # reset
