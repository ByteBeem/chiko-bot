from __future__ import annotations
import logging
import os
from typing import Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)

from .config import TELEGRAM_TOKEN, ADMIN_CHAT_ID
from .commands import start, help_command, status, unknown
from .listeners import handle_callback

logger = logging.getLogger(__name__)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    await update.message.reply_text("🎙 Voice messages are not yet supported.")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning("Update %s caused error: %s", update, context.error, exc_info=context.error)


class TelegramBot:
    """
    Wraps python-telegram-bot Application.

    Call `await bot.start()` inside an asyncio event loop.
    Call `await bot.send_alert(chat_id, text)` from anywhere in the app.
    """

    def __init__(
        self,
        persistence_file: str = "bot_data.pkl",
        use_webhook: bool = False,
        webhook_url: Optional[str] = None,
    ) -> None:
        self.use_webhook = use_webhook
        self.webhook_url = webhook_url
        self.persistence = PicklePersistence(filepath=persistence_file)

        self.app = (
            ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .persistence(self.persistence)
            .build()
        )

        # Register handlers
        self.app.add_handler(CommandHandler("start",  start))
        self.app.add_handler(CommandHandler("help",   help_command))
        self.app.add_handler(CommandHandler("status", status))
        self.app.add_handler(CallbackQueryHandler(handle_callback))
        self.app.add_handler(MessageHandler(filters.VOICE,                     _handle_voice))
        self.app.add_handler(MessageHandler(filters.COMMAND,                   unknown))
        self.app.add_error_handler(_error_handler)

        self.job_queue = self.app.job_queue

    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        if self.use_webhook:
            if not self.webhook_url:
                raise ValueError("webhook_url is required when use_webhook=True")
            await self.app.updater.start_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", 8443)),
                url_path=TELEGRAM_TOKEN,
                webhook_url=self.webhook_url + TELEGRAM_TOKEN,
            )
            logger.info("Telegram bot started (webhook mode)")
        else:
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started (polling mode)")

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Telegram bot stopped gracefully.")

    async def send_alert(self, chat_id: int, text: str) -> bool:
        """
        Send a plain-text alert to `chat_id`.
        Returns True on success.
        """
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            logger.info("Alert sent to %s", chat_id)
            return True
        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)
            return False

    async def send_signal_alert(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
    ) -> bool:
        """Send an alert with optional inline keyboard."""
        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.error("Failed to send signal alert: %s", exc)
            return False

    def run_scheduled_task(self, callback, interval: int) -> None:
        """Add a repeating background task (interval in seconds)."""
        self.job_queue.run_repeating(callback, interval=interval)