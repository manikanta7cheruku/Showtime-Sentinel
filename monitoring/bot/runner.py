"""Run the Telegram command bot and the scheduler in ONE asyncio loop.

Two concurrent jobs, one process, one SQLite file. No Celery, no extra workers.
"""
from __future__ import annotations

import asyncio
import logging

from app.bot.commands import BotContext, register_handlers
from app.monitoring.scheduler import Scheduler

logger = logging.getLogger(__name__)


async def run_bot_and_scheduler(ctx: BotContext, scheduler: Scheduler) -> None:
    if not ctx.settings.telegram_bot_token:
        logger.warning("no
                           if not ctx.settings.telegram_bot_token:
        logger.warning("no TELEGRAM_BOT_TOKEN -> running the scheduler only, no command bot")
        await scheduler.run()
        return

    from telegram.ext import ApplicationBuilder

    application = ApplicationBuilder().token(ctx.settings.telegram_bot_token).build()
    register_handlers(application, ctx)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram command bot is polling")

    try:
        await scheduler.run()          # returns when SIGINT/SIGTERM arrives
    finally:
        logger.info("stopping Telegram bot...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
