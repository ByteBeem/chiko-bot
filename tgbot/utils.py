from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_inline_keyboard(buttons: list[list[tuple[str, str]]]):
    """
    Convert a list of button labels and callback data into Telegram markup.
    buttons: [[("Buy BTC", "buy_btc"), ("Sell BTC", "sell_btc")]]
    """
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in buttons
    ]
    return InlineKeyboardMarkup(keyboard)
