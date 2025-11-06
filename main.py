import os
import ssl
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
import json
import websockets
from datetime import datetime
import asyncio
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional
import logging
from pathlib import Path
import backoff
from dataclasses import dataclass
import signal
import sys
import time
import argparse

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.console import Group

from imapclient import IMAPClient
import pyzmail
import threading
import queue

from tgbot.bot import TelegramBot
from openai import AzureOpenAI

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
LOCAL_TZ = ZoneInfo("Africa/Johannesburg")
WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=85155"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
MAX_RETRIES = 5
TIMEOUT = 30
DEFAULT_CHECK_INTERVAL = 60
DEFAULT_GRANULARITY = 300
DEFAULT_SYMBOL = os.getenv("SYMBOL", "R_100")
APP_VERSION = "1.0.3"  # Updated version with fixes and improvements
STATUS_UPDATE_INTERVAL = 3600  # Send status every hour (in seconds)
TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", 0))  # Ensure set in .env




@dataclass
class CandleData:
    """Immutable candle data structure"""
    open_time: int 
    time_str: str 
    open: float
    high: float
    low: float
    close: float
    is_closed: bool = False
    
    def update(self, high: float, low: float, close: float):
        """Update forming candle values"""
        self.high = max(self.high, high)
        self.low = min(self.low, low)
        self.close = close
    
    def get_type(self) -> str:
        if self.close > self.open:
            return "bullish"
        elif self.close < self.open:
            return "bearish"
        return "neutral(doji)"


class ConfigurationError(Exception):
    """Raised when configuration is invalid"""
    pass


class EmailSender:
    """Thread-safe email sender with retry logic"""
    
    def __init__(self):
        self.sender = self._get_env_var("EMAIL_SENDER")
        self.app_password = self._get_env_var("EMAIL_APP_PASSWORD")
        self.context = ssl.create_default_context()
        self._validate_email()
    
    @staticmethod
    def _get_env_var(key: str) -> str:
        """Safely retrieve environment variable"""
        value = os.getenv(key)
        if not value:
            raise ConfigurationError(f"Missing required environment variable: {key}")
        return value.strip()  # Strip any whitespace
    
    def _validate_email(self):
        """Basic email validation"""
        if '@' not in self.sender or '.' not in self.sender.split('@')[-1]:
            raise ConfigurationError(f"Invalid email address: {self.sender}")
    
    @backoff.on_exception(
        backoff.expo,
        (smtplib.SMTPException, ConnectionError),
        max_tries=MAX_RETRIES,
        max_time=60
    )
    def send_email(
        self, 
        receiver: str, 
        subject: str, 
        html_content: str, 
        text_fallback: str = "Your email client does not support HTML"
    ) -> bool:
        """Send email with retry logic"""
        if not receiver or '@' not in receiver or '.' not in receiver.split('@')[-1]:
            logger.error(f"Invalid receiver email: {receiver}")
            return False
        
        # Sanitize subject
        subject = ''.join(c for c in subject if c.isprintable())[:200]
        
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = receiver
        msg["Subject"] = subject
        msg.set_content(text_fallback)
        msg.add_alternative(html_content, subtype="html")

        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=self.context, timeout=TIMEOUT) as smtp:
                smtp.login(self.sender, self.app_password)
                smtp.send_message(msg)
            
            logger.info(f"Email sent successfully to {receiver}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Authentication failed: {e}")
            raise ConfigurationError("Invalid email credentials")
        
        except smtplib.SMTPConnectError as e:
            logger.error(f"Connection error: {e}")
            raise ConnectionError("Cannot connect to SMTP server")
        
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        
        except Exception as e:
            logger.error(f"Unexpected error sending email: {e}", exc_info=True)
            return False


class CandleMonitor:
    """WebSocket-based candle data monitor with live Rich display"""
    
    def __init__(self, symbol: str, granularity: int = DEFAULT_GRANULARITY, alert_queue: Optional[queue.Queue] = None):
        self.symbol = symbol
        self.granularity = granularity
        self.ws_url = WS_URL
        self.closed_candles: List[CandleData] = []  
        self.forming_candle: Optional[CandleData] = None
        self.next_close_time: int = 0
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        self._display_task: Optional[asyncio.Task] = None
        self.alert_queue = alert_queue  # Queue for sending updates to Telegram
        self._validate_inputs()
    
    def _validate_inputs(self):
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("Symbol must be a non-empty string")
        
        valid_granularities = {60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400}
        if self.granularity not in valid_granularities:
            raise ValueError(f"Invalid granularity. Must be one of: {valid_granularities}")
    
    async def get_initial_candles(self, count: int = 3) -> Optional[List[CandleData]]:
        ws = None
        try:
            ws = await asyncio.wait_for(websockets.connect(self.ws_url), timeout=TIMEOUT)
            request = {
                "ticks_history": self.symbol,
                "style": "candles",
                "granularity": self.granularity,
                "count": count + 1,  # Fetch extra to capture potential forming candle
                "end": "latest"
            }
            await asyncio.wait_for(ws.send(json.dumps(request)), timeout=TIMEOUT)
            response = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"API error: {data['error']['message']}")
                return None
            if "candles" not in data:
                logger.warning(f"No candle data returned: {data}")
                return None
            
            raw_candles = data["candles"]
            candles = self._parse_candles(raw_candles)
            if not candles:
                return None
            
            # Sort by open_time just in case
            candles.sort(key=lambda c: c.open_time)
            
            current_epoch = int(time.time())
            
            async with self._lock:
                # Check if the last candle is still forming
                if candles and candles[-1].open_time + self.granularity > current_epoch:
                    self.forming_candle = candles.pop()
                    self.forming_candle.is_closed = False
                    self.next_close_time = self.forming_candle.open_time + self.granularity
                
                # Set closed candles to last 3
                self.closed_candles = candles[-3:] if candles else []
                for c in self.closed_candles:
                    c.is_closed = True
            
            logger.info(f"Fetched {len(candles) + (1 if self.forming_candle else 0)} initial candles for {self.symbol}")
            return candles + ([self.forming_candle] if self.forming_candle else [])
            
        except Exception as e:
            logger.error(f"Error fetching initial candles: {e}", exc_info=True)
            return None
        finally:
            if ws:
                await ws.close()

    async def start_monitoring(self):
        self.running = True
        retry_count = 0
        
        while self.running:
            try:
                if not self.closed_candles and self.forming_candle is None:
                    console.print("[cyan]Fetching initial candles...[/cyan]")
                    initial = await self.get_initial_candles()
                    if not initial:
                        console.print("[red]Failed to fetch initial candles, retrying in 10s...[/red]")
                        await asyncio.sleep(10)
                        continue
                    console.print(f"[green]Initialized with {len(self.closed_candles)} closed candles[/green]")
                    if self.forming_candle:
                        console.print(f"[green]Initialized forming candle at {self.forming_candle.time_str}[/green]")
                    
                    if self.alert_queue:
                        self.alert_queue.put(f"Initialized monitoring for {self.symbol}")
                
                    logger.debug(f"initial candles: {initial}")
                
                console.print(f"[cyan]Connecting to WebSocket for live {self.symbol} updates...[/cyan]")
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    subscribe_request = {
                        "ticks_history": self.symbol,
                        "style": "candles",
                        "granularity": self.granularity,
                        "count": 1,
                        "end": "latest",
                        "subscribe": 1
                    }
                    await ws.send(json.dumps(subscribe_request))
                    console.print("[green]Subscribed to candle stream[/green]")
                    retry_count = 0
                    
                    # Start live display task
                    self._display_task = asyncio.create_task(self._live_display())
                    
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            if data.get("msg_type") == "ohlc":
                                await self._process_ohlc_update(data["ohlc"])
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid JSON: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}", exc_info=True)
            
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket closed: {e}")
                if self.alert_queue:
                    self.alert_queue.put(f"WebSocket connection closed: {str(e)}. Reconnecting...")
            except Exception as e:
                logger.error(f"Monitoring error: {e}", exc_info=True)
                if self.alert_queue:
                    self.alert_queue.put(f"Monitoring error: {str(e)}")
            
            finally:
                self.ws = None
                if self._display_task:
                    self._display_task.cancel()
                    try:
                        await self._display_task
                    except asyncio.CancelledError:
                        pass
                    self._display_task = None
            
            if self.running:
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    console.print("[red]Max retries reached, stopping monitor[/red]")
                    if self.alert_queue:
                        self.alert_queue.put("Max retries reached, stopping monitor")
                    self.running = False
                    break
                wait_time = min(2 ** retry_count, 60)
                console.print(f"[yellow]Reconnecting in {wait_time}s...[/yellow]")
                await asyncio.sleep(wait_time)
        
    async def _process_ohlc_update(self, ohlc_data: Dict):
        open_time = ohlc_data.get("open_time")
        if open_time is None:
            logger.warning("Missing open_time in ohlc data")
            return
        
        timestamp = datetime.fromtimestamp(open_time, tz=ZoneInfo("UTC")).astimezone(LOCAL_TZ)
        time_str = timestamp.strftime("%Y-%m-%d %H:%M")
        current_epoch = ohlc_data.get("epoch", int(time.time()))
        
        new_candle = CandleData(
            open_time=open_time,
            time_str=time_str,
            open=float(ohlc_data["open"]),
            high=float(ohlc_data["high"]),
            low=float(ohlc_data["low"]),
            close=float(ohlc_data["close"])
        )
        
        async with self._lock:
            if self.forming_candle is None:
                self.forming_candle = new_candle
                self.next_close_time = open_time + self.granularity
            elif open_time == self.forming_candle.open_time:
                self.forming_candle.update(
                    high=float(ohlc_data["high"]),
                    low=float(ohlc_data["low"]),
                    close=float(ohlc_data["close"])
                )
            else:
                # New candle started, previous is closed
                if self.forming_candle:
                    self.forming_candle.is_closed = True
                    close_msg = (
                        f"CANDLE CLOSED\n"
                        f"Time: {self.forming_candle.time_str}\n"
                        f"O: {self.forming_candle.open:.4f} H: {self.forming_candle.high:.4f} "
                        f"L: {self.forming_candle.low:.4f} C: {self.forming_candle.close:.4f}\n"
                        f"Type: {self.forming_candle.get_type().upper()}"
                    )
                    console.print(Panel.fit(
                        f"[bold green]{close_msg}[/bold green]",
                        title="Candle Closed",
                        border_style="green"
                    ))
                    if self.alert_queue:
                        self.alert_queue.put(close_msg)
                    self.closed_candles.append(self.forming_candle)
                    if len(self.closed_candles) > 3:
                        self.closed_candles.pop(0)
                
                self.forming_candle = new_candle
                self.next_close_time = open_time + self.granularity
        
    def _parse_candles(self, raw_candles: List[Dict]) -> List[CandleData]:
        candles = []
        for c in raw_candles:
            try:
                epoch = c["epoch"]
                timestamp = datetime.fromtimestamp(epoch, tz=ZoneInfo("UTC")).astimezone(LOCAL_TZ)
                candles.append(CandleData(
                    open_time=epoch,
                    time_str=timestamp.strftime("%Y-%m-%d %H:%M"),
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    is_closed=True
                ))
            except KeyError as e:
                logger.error(f"Missing key in candle data: {e}")
            except Exception as e:
                logger.error(f"Error parsing candle: {e}")
        return candles

    async def get_closed_candles(self) -> List[CandleData]:
        async with self._lock:
            return self.closed_candles.copy()

    async def _live_display(self):
        """Live updating display task"""
        # Initial content
        table, forming_panel, countdown_panel = await self._build_display_components()
        full_display = Panel(
            Group(
                table,
                "\n",
                forming_panel,
                "\n",
                countdown_panel
            ),
            title=f"{self.symbol} - Granularity: {self.granularity//60}m",
            border_style="white"
        )

        with Live(full_display, console=console, refresh_per_second=1) as live:
            while self.running:
                table, forming_panel, countdown_panel = await self._build_display_components()
                full_display = Panel(
                    Group(
                        table,
                        "\n",
                        forming_panel,
                        "\n",
                        countdown_panel
                    ),
                    title=f"{self.symbol} - Granularity: {self.granularity//60}m",
                    border_style="white"
                )
                live.update(full_display)
                await asyncio.sleep(1)

    async def _build_display_components(self) -> tuple[Table, Panel, Panel]:
        async with self._lock:
            time_left = max(0, self.next_close_time - int(time.time()))
            minutes, seconds = divmod(time_left, 60)
            
            # Build table for closed candles
            table = Table(title="Last Closed Candles")
            table.add_column("Time")
            table.add_column("O")
            table.add_column("H")
            table.add_column("L")
            table.add_column("C")
            table.add_column("Type")
            for c in self.closed_candles:
                table.add_row(
                    c.time_str,
                    f"{c.open:.4f}",
                    f"{c.high:.4f}",
                    f"{c.low:.4f}",
                    f"{c.close:.4f}",
                    c.get_type().upper()
                )
            
            # Forming candle panel
            forming_str = "No forming candle"
            if self.forming_candle:
                forming_str = (
                    f"Time: {self.forming_candle.time_str}\n"
                    f"O: {self.forming_candle.open:.4f} H: {self.forming_candle.high:.4f} "
                    f"L: {self.forming_candle.low:.4f} C: {self.forming_candle.close:.4f}\n"
                    f"Type: {self.forming_candle.get_type().upper()}"
                )
            forming_panel = Panel(
                forming_str,
                title="Current Forming Candle",
                border_style="blue"
            )
            
            # Countdown panel
            countdown_panel = Panel(
                f"[cyan]Time left to close:[/cyan] {minutes}m {seconds}s\n"
                f"[bold red]Candle closed?[/bold red] {time_left == 0}",
                title="Forming Candle Countdown",
                border_style="yellow"
            )
        
        return table, forming_panel, countdown_panel

    @staticmethod
    def analyze_pattern(candles: List[CandleData]) -> Optional[str]:
        if len(candles) < 3:
            return None
        last_three = candles[-3:]
        types = [c.get_type() for c in last_three]
        if all(t == "bullish" for t in types):
            return "bullish"
        elif all(t == "bearish" for t in types):
            return "bearish"
        return None  # No signal

    def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.ws:
            asyncio.create_task(self.ws.close())


class ChikoEmail:
    def __init__(self, email, app_password, imap_host='imap.gmail.com', folder='INBOX', telegram_bot: Optional[TelegramBot] = None, alert_queue: Optional[queue.Queue] = None):
        self.email = email
        self.password = app_password
        self.imap_host = imap_host
        self.folder = folder
        self.client = None
        self.running = False
        self.telegram_bot = telegram_bot
        self.alert_queue = alert_queue
        self.last_status_time = time.time()

    def connect(self):
        """Connects to the IMAP server and selects folder."""
        try:
            self.client = IMAPClient(self.imap_host, ssl=True)
            self.client.login(self.email, self.password)
            self.client.select_folder(self.folder)
            logger.info(f"Connected to {self.folder}")
            if self.alert_queue:
                self.alert_queue.put("Connected to IMAP server")
        except Exception as e:
            logger.error(f"IMAP connection failed: {e}")
            if self.alert_queue:
                self.alert_queue.put(f"IMAP connection failed: {str(e)}")
            raise

    def read_unseen(self):
        """Fetch all unread emails."""
        try:
            messages = self.client.search(['UNSEEN'])
            emails = []
            for msgid in messages:
                raw_msg = self.client.fetch([msgid], ['BODY[]', 'FLAGS'])
                message = pyzmail.PyzMessage.factory(raw_msg[msgid][b'BODY[]'])
                subject = message.get_subject()
                from_email = message.get_addresses('from')
                body = self._get_email_body(message)
                emails.append({
                    'from': from_email,
                    'subject': subject,
                    'body': body
                })
            return emails
        except Exception as e:
            logger.error(f"Error reading unseen emails: {e}")
            if self.alert_queue:
                self.alert_queue.put(f"Error reading emails: {str(e)}")
            return []

    def _get_email_body(self, message):
        """Helper to extract text from email."""
        if message.text_part:
            return message.text_part.get_payload().decode(message.text_part.charset or 'utf-8', errors='replace')
        elif message.html_part:
            return message.html_part.get_payload().decode(message.html_part.charset or 'utf-8', errors='replace')
        return ""

    def listen_new_emails(self, callback):
        """
        Continuously listen for new emails.
        `callback` is a function that takes one argument: the email dict.
        """
        if not self.client:
            self.connect()

        self.running = True
        logger.info("Listening for new emails...")

        while self.running:
            try:
                self.client.idle()
                responses = self.client.idle_check(timeout=30)
                self.client.idle_done()
                new_emails_found = False
                for resp in responses:
                    if b'EXISTS' in resp:
                        new_emails = self.read_unseen()
                        for email_data in new_emails:
                            callback(email_data)
                        new_emails_found = True
                
                # Periodic status if no new emails
                current_time = time.time()
                if not new_emails_found and (current_time - self.last_status_time) > STATUS_UPDATE_INTERVAL:
                    if self.alert_queue:
                        self.alert_queue.put("No new emails received in the last hour. System is monitoring.")
                    self.last_status_time = current_time
                
            except Exception as e:
                logger.error(f"IMAP listener error: {e}")
                if self.alert_queue:
                    self.alert_queue.put(f"IMAP listener error: {str(e)}. Reconnecting...")
                time.sleep(5)
                self.connect()  # reconnect if disconnected

    def stop_listening(self):
        """Stops the live email listener."""
        self.running = False
        if self.client:
            try:
                self.client.idle_done()
                self.client.logout()
                logger.info("Disconnected from IMAP server")
            except Exception as e:
                logger.error(f"Error during IMAP logout: {e}")


class AlertManager:
    """Manages trading alerts and notifications"""
    
    def __init__(self, email_sender: EmailSender, telegram_bot: Optional[TelegramBot] = None, template_dir: str = "templates"):
        self.email_sender = email_sender
        self.telegram_bot = telegram_bot
        self.template_dir = Path(template_dir)
        self.receiver = self._get_env_var("RECEIVER_EMAIL")
        self.username = os.getenv("USERNAME", "Sir")
        self.last_alert_pattern: Optional[str] = None
        self.last_alert_epoch: Optional[int] = None
        self._lock = asyncio.Lock()  # For thread-safety in alert sending
        self._validate_template_dir()
    
    @staticmethod
    def _get_env_var(key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise ConfigurationError(f"Missing {key}")
        return value.strip()
    
    def _validate_template_dir(self):
        if not self.template_dir.is_dir():
            logger.info(f"Creating template directory: {self.template_dir}")
            self.template_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_template(self, template_name: str) -> Optional[str]:
        template_path = self.template_dir / template_name
        if not template_path.exists():
            logger.warning(f"Template not found: {template_path}, using fallback")
            return None
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error loading template: {e}")
            return None
    
    async def should_send_alert(self, signal: str, candles: List[CandleData]) -> bool:
        async with self._lock:
            if not candles:
                return False
            latest_epoch = candles[-1].open_time
            if self.last_alert_pattern == signal and self.last_alert_epoch == latest_epoch:
                return False
            return True

    async def send_telegram_alert(self, message: str):
        if self.telegram_bot and TELEGRAM_ADMIN_CHAT_ID:
            try:
                await self.telegram_bot.app.bot.send_message(
                    chat_id=TELEGRAM_ADMIN_CHAT_ID,
                    text=message
                )
                console.print(f"[green]Telegram alert sent: {message}[/green]")
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")
    
    async def send_alert(
        self, 
        signal: str, 
        symbol: str, 
        candles: List[CandleData]
    ) -> bool:
        async with self._lock:
            if not await self.should_send_alert(signal, candles):
                logger.info("Skipping duplicate alert")
                return False
            
            template = self._load_template("ai_noticed_alert.html")
            if not template:
                template = self._create_fallback_template()
            
            candle_info = "<br>".join([
                f"Candle {i+1} ({c.time_str}): Open={c.open:.4f}, High={c.high:.4f}, "
                f"Low={c.low:.4f}, Close={c.close:.4f} | Type={c.get_type().upper()}"
                for i, c in enumerate(candles[-3:])
            ])
            
            action = "BUY" if signal == "bullish" else "SELL"
            message = (
                f"TRADING SIGNAL DETECTED<br><br>"
                f"Pattern: 3 consecutive {signal.upper()} candles on {symbol}<br>"
                f"Suggested action: Place a {action} trade<br>"
                f"Time: {candles[-1].time_str}<br><br>"
                f"Candle Details:<br>{candle_info}"
            )
            
            html = template.replace("{{USERNAME}}", self.username)
            html = html.replace("{{MESSAGE_CONTENT}}", message)
            
            subject = f"{signal.capitalize()} Signal - {symbol} ({datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')})"
            
            text_fallback = message.replace("<br>", "\n")
            
            success = self.email_sender.send_email(
                receiver=self.receiver,
                subject=subject,
                html_content=html,
                text_fallback=text_fallback
            )
            
            if success:
                self.last_alert_pattern = signal
                self.last_alert_epoch = candles[-1].open_time
                console.print(f"[green]Email alert sent for {signal.upper()} pattern![/green]")
                
                # Send Telegram alert
                alert_message = f"🚨 {signal.upper()} signal detected on {symbol} at {candles[-1].time_str}\nAction: {action}"
                asyncio.create_task(self.send_telegram_alert(alert_message))
            
            return success
    
    @staticmethod
    def _create_fallback_template() -> str:
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }
                .alert { 
                    background: white; 
                    padding: 20px; 
                    border-radius: 8px; 
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    max-width: 600px;
                    margin: 0 auto;
                }
                .header { 
                    color: #2c3e50; 
                    border-bottom: 2px solid #3498db; 
                    padding-bottom: 10px; 
                }
                .content { 
                    margin-top: 20px; 
                    line-height: 1.6; 
                    color: #34495e; 
                }
            </style>
        </head>
        <body>
            <div class="alert">
                <h2 class="header">Trading Alert for {{USERNAME}}</h2>
                <div class="content">{{MESSAGE_CONTENT}}</div>
            </div>
        </body>
        </html>
        """


class TradingMonitorApp:
    """Main application orchestrator"""
    
    def __init__(self, args: argparse.Namespace, telegram_bot: Optional[TelegramBot] = None, alert_queue: Optional[queue.Queue] = None):
        self.running = True
        self.symbol = args.symbol
        self.granularity = args.granularity
        self.check_interval = args.check_interval
        self._setup_signal_handlers()
        self._load_environment()
        
        self.email_sender = EmailSender()
        self.candle_monitor = CandleMonitor(
            symbol=self.symbol,
            granularity=self.granularity,
            alert_queue=alert_queue
        )
        self.alert_manager = AlertManager(self.email_sender, telegram_bot=telegram_bot)
        self.last_status_time = time.time()
    
    def _setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received, cleaning up...")
        self.running = False
        self.candle_monitor.stop()
    
    def _load_environment(self):
        load_dotenv()
    
    async def run(self):
        logger.info("=" * 60)
        logger.info(f"Trading Monitor v{APP_VERSION} Started")
        logger.info(f"Monitoring: {self.symbol}")
        logger.info(f"Timeframe: {self.granularity}s ({self.granularity//60} minutes)")
        logger.info(f"Analysis Interval: {self.check_interval}s")
        logger.info("=" * 60)
        
        try:
            monitor_task = asyncio.create_task(self.candle_monitor.start_monitoring())
            
            while self.running:
                try:
                    candles = await self.candle_monitor.get_closed_candles()
                    if len(candles) >= 3:
                        signal = self.candle_monitor.analyze_pattern(candles)
                        if signal:
                            console.print(f"[magenta]Pattern detected: {signal.upper()}[/magenta]")
                            success = await self.alert_manager.send_alert(  # Made async
                                signal=signal,
                                symbol=self.symbol,
                                candles=candles
                            )
                            if not success:
                                logger.error("Failed to send alert")
                    
                    # Periodic status update
                    current_time = time.time()
                    if (current_time - self.last_status_time) > STATUS_UPDATE_INTERVAL:
                        status_msg = f"System status: Running, monitoring {self.symbol}. No new patterns detected."
                        asyncio.create_task(self.alert_manager.send_telegram_alert(status_msg))
                        self.last_status_time = current_time
                    
                    await asyncio.sleep(self.check_interval)
                
                except Exception as e:
                    logger.error(f"Error in analysis loop: {e}", exc_info=True)
                    await asyncio.sleep(10)
            
            logger.info("Cancelling monitoring task...")
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            
        except Exception as e:
            logger.error(f"Critical error: {e}", exc_info=True)
        
        logger.info("=" * 60)
        logger.info("Trading Monitor Stopped")
        logger.info("=" * 60)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trading Monitor Application")
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL, help="Trading symbol")
    parser.add_argument("--granularity", type=int, default=DEFAULT_GRANULARITY, help="Candle granularity in seconds")
    parser.add_argument("--check-interval", type=int, default=DEFAULT_CHECK_INTERVAL, help="Analysis check interval in seconds")
    return parser.parse_args()


def handle_email(email_data):
    logger.info(f"New email from {email_data['from']}: {email_data['subject']}")
    logger.info(f"Body: {email_data['body']}")
    # Here you can parse commands, e.g., BUY BTC, STOP, etc.
    # For now, just log; extend for command processing if needed


def start_chiko_email(telegram_bot=None, alert_queue=None):
    chiko = ChikoEmail(
        os.getenv("EMAIL_SENDER"), 
        os.getenv("EMAIL_APP_PASSWORD"),
        telegram_bot=telegram_bot,
        alert_queue=alert_queue
    )
    
    # Connect first
    chiko.connect()
    
    # Read unread emails first
    unread = chiko.read_unseen()
    for e in unread:
        handle_email(e)

    # Start listener
    chiko.listen_new_emails(handle_email)


async def telegram_alert_dispatcher(alert_queue: queue.Queue, alert_manager: AlertManager):
    """Dispatcher to send queued alerts via Telegram"""
    while True:
        try:
            message = alert_queue.get(timeout=1)
            if message:
                await alert_manager.send_telegram_alert(message)
        except queue.Empty:
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in alert dispatcher: {e}")


async def main_async():
    alert_queue = queue.Queue()  # Shared queue for alerts/updates
    
    bot = TelegramBot()
    
    args = parse_arguments()

    app = TradingMonitorApp(args, telegram_bot=bot, alert_queue=alert_queue)

    # Start email listener in thread
    email_thread = threading.Thread(
        target=start_chiko_email,
        kwargs={'telegram_bot': bot, 'alert_queue': alert_queue},
        daemon=True
    )
    email_thread.start()
    
    # Start alert dispatcher
    dispatcher_task = asyncio.create_task(telegram_alert_dispatcher(alert_queue, app.alert_manager))
    
    try:
        await asyncio.gather(
            bot.start(),   # Telegram bot runs non-blocking
            app.run()      # Trading monitor runs
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)