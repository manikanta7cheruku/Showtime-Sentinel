import pytest

from app.utils.retry import RetryPolicy, backoff_delay, run_with_retries


def test_backoff_grows_exponentially_and_is_capped():
    policy = RetryPolicy(base_delay=5, max_delay=40, jitter=0.0)
    delays = [backoff_delay(policy, n, rand=lambda: 0.5) for n in range(1, 6)]
    assert delays == [5, 10, 20, 40, 40]


def test_jitter_stays_inside_its_band():
    policy = RetryPolicy(base_delay=10, max_delay=1000, jitter=0.25)
    assert backoff_delay(policy, 1, rand=lambda: 0.0) == pytest.approx(7.5)
    assert backoff_delay(policy, 1, rand=lambda: 1.0) == pytest.approx(12.5)


async def test_succeeds_after_transient_failures():
    calls = {"n": 0}
    slept: list[float] = []

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("nope")
        return "ok"

    async def fake_sleep(delay):
        slept.append(delay)

    result = await run_with_retries(flaky, RetryPolicy(attempts=3, base_delay=1, jitter=0.0),
                                    sleep=fake_sleep, rand=lambda: 0.5)
    assert result == "ok" and calls["n"] == 3 and slept == [1, 2]


async def test_reraises_after_exhausting_attempts():
    async def always_fails():
        raise ValueError("permanent")

    async def fake_sleep(_):
        return None

    with pytest.raises(ValueError, match="permanent"):
        await run_with_retries(always_fails, RetryPolicy(attempts=2, base_delay=0.01),
                               sleep=fake_sleep)


async def test_timeout_is_enforced_per_attempt():
    import asyncio

    async def too_slow():
        await asyncio.sleep(5)

    async def fake_sleep(_):
        return None

    with pytest.raises(asyncio.TimeoutError):
        await run_with_retries(too_slow, RetryPolicy(attempts=1, timeout=0.01), sleep=fake_sleep)
