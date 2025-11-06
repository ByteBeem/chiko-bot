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
from collections import defaultdict

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

endpoint = os.getenv("endpoint")
model_name = "gpt-5-mini"
deployment = "gpt-5-mini"

subscription_key = os.getenv("AZURE_API_KEY")
api_version = "2024-12-01-preview"

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=endpoint,
    api_key=subscription_key,
)


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
endpoint = os.getenv("endpoint")
subscription_key = os.getenv("AZURE_API_KEY")

# OpenAI / GPT-5-mini client
client = AzureOpenAI(
    api_version="2024-12-01-preview",
    azure_endpoint=endpoint,
    api_key=subscription_key,
)

MAX_HISTORY_LENGTH = 50
chat_histories = defaultdict(list) 

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

# === AI Prompt and Response ===
SYSTEM_PROMPT = (
    "You are CHIKO, a friendly and professional assistant. "
    "Always answer concisely and directly, while maintaining a polite, approachable tone. "
    "Prioritize clarity and helpfulness. "
    "You are knowledgeable about forex trading, Exness, and Deriv platforms. "
    "If unsure, admit it briefly and guide the user professionally."
)

async def ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages with memory support for GPT-5-mini."""
    await update.message.chat.send_action(ChatAction.TYPING)
    chat_id = update.message.chat_id
    user_message = update.message.text

    # Append user message to chat history
    chat_histories[chat_id].append({"role": "user", "content": user_message})

    # Trim history to MAX_HISTORY_LENGTH to avoid exceeding token limits
    if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]

    # Include system prompt first
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_histories[chat_id]

    try:
        response = client.chat.completions.create(
            messages=messages,
            model="gpt-5-mini",
            max_completion_tokens=2048
        )
        CHIKO_response = response.choices[0].message.content.strip()

        # Append bot response to history
        chat_histories[chat_id].append({"role": "assistant", "content": CHIKO_response})

        # Trim again to enforce MAX_HISTORY_LENGTH
        if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
            chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_LENGTH:]

        await update.message.reply_text(CHIKO_response)

    except Exception as e:
        logger.error(f"AI response failed: {e}")
        await update.message.reply_text("Sorry, I couldn't process your request at the moment.")

# === Voice Messages ===
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages (placeholder). Integrate speech-to-text later."""
    await update.message.chat.send_action(ChatAction.RECORD_VOICE)
    await asyncio.sleep(2)  # simulate processing
    await update.message.reply_text("Voice message received and processed.")

# === Telegram Bot Class ===
class TelegramBot:
    def __init__(self, persistence_file: str = "bot_data.pkl", use_webhook: bool = False, webhook_url: Optional[str] = None):
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
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_response))
        self.app.add_handler(MessageHandler(filters.VOICE, handle_voice))
        self.app.add_error_handler(error_handler)

        # Job queue for scheduled tasks
        self.job_queue = self.app.job_queue

    async def start(self):
        await self.app.initialize()
        await self.app.start()
        if self.use_webhook:
            if not self.webhook_url:
                raise ValueError("Webhook URL is required for webhook mode.")
            await self.app.updater.start_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", 8443)),
                url_path=TELEGRAM_TOKEN,
                webhook_url=self.webhook_url + TELEGRAM_TOKEN
            )
            logger.info("Bot started with webhook.")
        else:
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("Bot started with polling.")

    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Bot stopped gracefully.")

    def run_scheduled_task(self, callback, interval: int):
        """Add a scheduled repeating task (interval in seconds)."""
        self.job_queue.run_repeating(callback, interval=interval)

    async def send_alert(self, chat_id: int, message: str):
        """Send an alert to a specific chat ID."""
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Alert sent to {chat_id}: {message}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

# === Additional Improvements / Features ===
# - Add rich text or tables using telegram Markdown / HTML formatting
# - Add logging of user queries for analytics
# - Add retry logic for OpenAI API failures
# - Optionally integrate a memory/cache for context across multiple messages
