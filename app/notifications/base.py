"""NotificationService interface. Two implementations: console and Telegram."""
from __future__ import annotations

import abc

from app.models import NormalizedResult, TransitionDecision, Watch


class NotificationService(abc.ABC):
    channel: str = "base"

    @abc.abstractmethod
    async def send_message(self, text: str) -> bool:
        """Deliver raw text. Returns True on success, never raises for HTTP errors."""

    @abc.abstractmethod
    async def send_booking_open(
        self, watch: Watch, result: NormalizedResult, decision: TransitionDecision
    ) -> bool: ...

    @abc.abstractmethod
    async def send_error(
        self, watch: Watch, result: NormalizedResult, decision: TransitionDecision
    ) -> bool: ...

    @abc.abstractmethod
    async def send_test_message(self) -> bool: ...

    async def aclose(self) -> None:
        return None
