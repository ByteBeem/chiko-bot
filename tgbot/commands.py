from telegram import Update
from telegram.ext import ContextTypes
from system.status import uptime

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "👋 Hello! I am Chiko, your trading assistant bot.\n"
        "Use /help to see available commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    await update.message.reply_text(
        "📄 Available commands:\n"
        "/start - Start the bot\n"
        "/status - Show current trading status\n"
        "/alert - Trigger alert manually"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send current trading bot status"""
    current_uptime = uptime()
    await update.message.reply_text(f"Bot uptime: {current_uptime}")

async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an alert manually"""
    # Example: trigger alert logic here (you can await your alert manager)
    await update.message.reply_text("⚡ Alert triggered manually!")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for unknown commands"""
    await update.message.reply_text("❌ Sorry, I don't recognize that command.")
