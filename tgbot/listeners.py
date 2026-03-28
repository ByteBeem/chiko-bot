from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data.startswith("buy"):
        await query.edit_message_text("📈 BUY order noted. Please execute via your exchange.")
    elif data.startswith("sell"):
        await query.edit_message_text("📉 SELL order noted. Please execute via your exchange.")
    elif data == "dismiss":
        await query.edit_message_text("✅ Alert dismissed.")
    else:
        await query.edit_message_text(f"Unknown action: {data}")


def build_signal_keyboard(symbol: str, direction: str) -> InlineKeyboardMarkup:
    """Build inline keyboard attached to trading signal alerts."""
    action = "buy" if direction == "bullish" else "sell"
    keyboard = [
        [
            InlineKeyboardButton(f"{'📈 BUY' if action == 'buy' else '📉 SELL'} {symbol}", callback_data=f"{action}_{symbol}"),
            InlineKeyboardButton("❌ Dismiss", callback_data="dismiss"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)