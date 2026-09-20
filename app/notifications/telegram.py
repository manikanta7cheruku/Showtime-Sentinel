"""TelegramNotifier - talks to the Bot API over plain HTTPS with httpx.

Design note worth understanding: this project uses TWO Telegram approaches on
purpose.
  * OUTGOING notifications go through this module, a ~40-line httpx call to
    `sendMessage`. You can see exactly what an HTTP bot notification is.
  * INCOMING commands use python-telegram-bot (app/bot/), because hand-writing
    long-polling, update offsets and retry semantics would teach you nothing
    useful and get subtly wrong.
"""
from __future__ import annotations

import logging

import httpx

from app.models import NormalizedResult, TransitionDecision, Watch
from app.notifications.base import NotificationService
from app.notifications.formatter import format_error, format_state_change, format_test_message

logger = logging.getLogger(__name__)
API = "https://api.telegram.org"


class TelegramNotifier(NotificationService):
    channel = "telegram"

    def __init__(self, token: str, chat_id: str, *, timeout: float = 15.0) -> None:
        if not token or not chat_id:
            raise ValueError("TelegramNotifier needs both a bot token and a chat id")
        self._token = token          # private: never logged, never printed
        self.chat_id = chat_id
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def send_message(self, text: str) -> bool:
        client = await self._http()
        url = f"{API}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text[:4096],                 # Telegram's hard limit
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            logger.error("Telegram request failed: %s", exc)
            return False

        if response.status_code == 200 and response.json().get("ok"):
            logger.info("Telegram message delivered to chat %s", self.chat_id)
            return True

        # Telegram error bodies contain a description but never our token.
        logger.error("Telegram rejected the message (HTTP %s): %s",
                     response.status_code, response.text[:300])
        if response.status_code == 429:
            logger.error("HTTP 429 = you are sending too fast. Raise your poll interval.")
        if response.status_code in (401, 404):
            logger.error("HTTP %s usually means a wrong TELEGRAM_BOT_TOKEN.", response.status_code)
        if response.status_code == 400 and "chat not found" in response.text.lower():
            logger.error("'chat not found' -> send /start to your bot once, then re-check "
                         "TELEGRAM_CHAT_ID.")
        return False

    async def send_booking_open(self, watch: Watch, result: NormalizedResult,
                                decision: TransitionDecision) -> bool:
        return await self.send_message(format_state_change(watch, result, decision))

    async def send_error(self, watch: Watch, result: NormalizedResult,
                         decision: TransitionDecision) -> bool:
        return await self.send_message(format_error(watch, result, decision))

    async def send_test_message(self) -> bool:
        return await self.send_message(format_test_message())

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
