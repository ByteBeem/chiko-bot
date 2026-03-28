from __future__ import annotations
import os
from telegram import Update
from telegram.ext import ContextTypes
from system.status import uptime


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    name = update.effective_user.first_name if update.effective_user else "Trader"
    await update.message.reply_text(
        f"👋 Hello {name}! I'm *Chiko*, your trading monitor bot.\n\n"
        f"I watch Binance candles 24/7 and alert you the moment I detect a signal.\n\n"
        f"Use /help to see all available commands.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message."""
    await update.message.reply_text(
        "📄 *Available commands:*\n\n"
        "/start  – Welcome message\n"
        "/status – Bot uptime & monitoring status\n"
        "/help   – This message",
        parse_mode="Markdown",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send current bot status."""
    symbol  = os.getenv("SYMBOL", "BTCUSDT")
    gran    = int(os.getenv("GRANULARITY", "300")) // 60
    uptime_ = uptime()
    await update.message.reply_text(
        f"🟢 *Bot Status*\n\n"
        f"• Uptime:     `{uptime_}`\n"
        f"• Symbol:     `{symbol}`\n"
        f"• Timeframe:  `{gran}m`\n"
        f"• Engine:     StrategyEngine (multi-signal)\n"
        f"• Signals:    3-Candle · EMA Cross · RSI · MACD",
        parse_mode="Markdown",
    )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for unknown commands."""
    await update.message.reply_text("❌ Unknown command. Use /help.")