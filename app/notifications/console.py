"""ConsoleNotifier - used when DRY_RUN=true or Telegram isn't configured.

This is what makes dry-run genuinely safe to demo: identical formatting and
identical bookkeeping, zero network traffic.
"""
from __future__ import annotations

import logging

from app.models import NormalizedResult, TransitionDecision, Watch
from app.notifications.base import NotificationService
from app.notifications.formatter import format_error, format_state_change, format_test_message

logger = logging.getLogger(__name__)


def _plain(html_text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html_text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


class ConsoleNotifier(NotificationService):
    channel = "console"

    def __init__(self, *, reason: str = "dry-run") -> None:
        self.reason = reason
        self.sent: list[str] = []   # inspected by tests

    async def send_message(self, text: str) -> bool:
        self.sent.append(text)
        banner = f"── NOTIFICATION ({self.reason}, not sent to Telegram) ──"
        print(f"\n{banner}\n{_plain(text)}\n{'─' * len(banner)}\n")
        logger.info("console notification emitted (%s)", self.reason)
        return True

    async def send_booking_open(self, watch: Watch, result: NormalizedResult,
                                decision: TransitionDecision) -> bool:
        return await self.send_message(format_state_change(watch, result, decision))

    async def send_error(self, watch: Watch, result: NormalizedResult,
                         decision: TransitionDecision) -> bool:
        return await self.send_message(format_error(watch, result, decision))

    async def send_test_message(self) -> bool:
        return await self.send_message(format_test_message())
