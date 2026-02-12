# system/status.py
from datetime import datetime

# Store when the bot started
START_TIME = datetime.utcnow()

def uptime():
    """Return bot uptime as a string"""
    delta = datetime.utcnow() - START_TIME
    hours, remainder = divmod(delta.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
