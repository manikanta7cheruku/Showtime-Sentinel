"""Timeouts, limited retries, exponential backoff with jitter.

Why jitter? If five watches all fail at the same second (network down), a pure
2/4/8s backoff makes all five retry at the exact same instants forever. Jitter
spreads them out. This is the standard "full-ish jitter" approach.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 5.0
    max_delay: float = 300.0
    jitter: float = 0.25           # +/- 25 %
    timeout: float | None = 30.0   # per attempt


def backoff_delay(policy: RetryPolicy, attempt: int, rand: Callable[[], float] = random.random) -> float:
    """attempt is 1-based. rand() is injectable so tests are deterministic."""
    raw = min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
    factor = 1.0 - policy.jitter + (2.0 * policy.jitter * rand())
    return min(raw * factor, policy.max_delay)


async def run_with_retries(
    operation: Callable[[], Awaitable],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
):
    """Call `operation()` up to policy.attempts times. Re-raises the last error."""
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            if policy.timeout is not None:
                return await asyncio.wait_for(operation(), timeout=policy.timeout)
            return await operation()
        except asyncio.CancelledError:
            raise  # never swallow shutdown
        except Exception as exc:          # noqa: BLE001 - deliberate catch-all
            last = exc
            if attempt >= policy.attempts:
                break
            delay = backoff_delay(policy, attempt, rand)
            if on_retry:
                on_retry(attempt, exc, delay)
            await sleep(delay)
    assert last is not None
    raise last
