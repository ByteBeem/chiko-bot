from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler

def handle_callback(update: Update, context: CallbackContext):
    """Handle inline button presses"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    if data.startswith("buy"):
        query.edit_message_text("Executing BUY order...")
        # call trading bot function here
    elif data.startswith("sell"):
        query.edit_message_text("Executing SELL order...")
        # call trading bot function here
    else:
        query.edit_message_text(f"Unknown action: {data}")
