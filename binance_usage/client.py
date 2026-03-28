import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

if not API_KEY or not API_SECRET:
    raise EnvironmentError("BINANCE_API_KEY and BINANCE_API_SECRET must be set in your .env file.")

client = Client(API_KEY, API_SECRET)