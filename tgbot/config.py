import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID: int  = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", 0))

if not TELEGRAM_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN must be set in your .env file.")