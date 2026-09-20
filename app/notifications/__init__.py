import logging

from app.notifications.base import NotificationService
from app.notifications.console import ConsoleNotifier
from app.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

__all__ = ["ConsoleNotifier", "NotificationService", "TelegramNotifier", "build_notifier"]


def build_notifier(settings) -> NotificationService:
    """Pick a notifier. Dry-run and missing credentials both fall back to console."""
    if settings.dry_run:
        logger.warning("DRY_RUN=true -> notifications print to the console only")
        return ConsoleNotifier(reason="DRY_RUN=true")
    if not settings.telegram_configured:
        logger.warning(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing -> console notifier. "
            "See the README 'Telegram BotFather setup' section."
        )
        return ConsoleNotifier(reason="Telegram not configured")
    return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
