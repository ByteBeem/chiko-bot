import logging
import asyncio
import os
from typing import Optional
from telegram import Update
from telegram.constants import ChatAction  # <- fixed import
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
from .config import TELEGRAM_TOKEN
from .commands import start, help_command, status, unknown
from .listeners import handle_callback
from .config import TELEGRAM_TOKEN
from .commands import start, help_command, status, unknown
from .listeners import handle_callback

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

async def ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages with typing indicator."""
    # Send typing action
    await update.message.chat.send_action(ChatAction.TYPING)
    
    # Placeholder for intelligent AI response. Integrate with xAI API or similar for smart replies.
    user_message = update.message.text
    # TODO: Integrate with AI API (e.g., https://x.ai/api) for generating intelligent responses
    # Simulate processing delay for realism
    await asyncio.sleep(1)  # Adjust based on actual processing time
    response = f"I'm processing your message intelligently: '{user_message}'"
    await update.message.reply_text(response)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages with recording indicator."""
    # Send recording action
    await update.message.chat.send_action(ChatAction.RECORD_VOICE)
    
    # Placeholder for voice processing (e.g., transcription)
    # TODO: Integrate speech-to-text service if available
    # Simulate processing delay
    await asyncio.sleep(2)  # Adjust based on actual processing time
    response = "Voice message received and processed."
    await update.message.reply_text(response)

class TelegramBot:
    def __init__(self, persistence_file: str = "bot_data.pkl", use_webhook: bool = False, webhook_url: Optional[str] = None):
        """
        Initialize the Telegram Bot with advanced features.
        
        Args:
            persistence_file: File path for persistence data.
            use_webhook: Whether to use webhook instead of polling.
            webhook_url: The webhook URL if using webhook.
        """
        self.use_webhook = use_webhook
        self.webhook_url = webhook_url
        self.persistence = PicklePersistence(filepath=persistence_file)
        
        self.app = ApplicationBuilder() \
            .token(TELEGRAM_TOKEN) \
            .persistence(self.persistence) \
            .build()

        # Register handlers
        self.app.add_handler(CommandHandler("start", start))
        self.app.add_handler(CommandHandler("help", help_command))
        self.app.add_handler(CommandHandler("status", status))
        self.app.add_handler(CallbackQueryHandler(handle_callback))
        self.app.add_handler(MessageHandler(filters.COMMAND, unknown))
        
        # Add intelligent text response handler with typing
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_response))
        
        # Add voice message handler with recording
        self.app.add_handler(MessageHandler(filters.VOICE, handle_voice))
        
        # Add error handler
        self.app.add_error_handler(error_handler)

        # Job queue for scheduled tasks (advanced feature)
        self.job_queue = self.app.job_queue

    async def start(self):
        """Start the bot with polling or webhook."""
        await self.app.initialize()
        await self.app.start()
        
        if self.use_webhook:
            if not self.webhook_url:
                raise ValueError("Webhook URL is required when use_webhook is True")
            await self.app.updater.start_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", 8443)),
                url_path=TELEGRAM_TOKEN,
                webhook_url=self.webhook_url + TELEGRAM_TOKEN
            )
            logger.info("Bot started with webhook")
        else:
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("Bot started with polling")

    async def stop(self):
        """Gracefully stop the bot."""
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Bot stopped gracefully")

    def run_scheduled_task(self, callback, interval: int):
        """Add a scheduled task using job queue."""
        self.job_queue.run_repeating(callback, interval=interval)


    async def send_alert(self, chat_id: int, message: str):
        """Send an alert message to a specific chat."""
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Alert sent to {chat_id}: {message}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

            