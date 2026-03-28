"""
chiko_email.py
~~~~~~~~~~~~~~
ChikoEmail – IMAP IDLE listener that watches the inbox and forwards
new email notifications to the alert queue (→ Telegram).
"""
from __future__ import annotations

import logging
import os
import queue
import time
from typing import Callable, Optional

from imapclient import IMAPClient
import pyzmail

logger = logging.getLogger(__name__)

STATUS_UPDATE_INTERVAL = 3600  # seconds between "still-alive" messages


class ChikoEmail:
    """
    Connects to an IMAP server via IDLE and calls `callback(email_dict)`
    for each new unread email.

    email_dict keys: from, subject, body
    """

    def __init__(
        self,
        email: str,
        app_password: str,
        imap_host: str = "imap.gmail.com",
        folder: str = "INBOX",
        alert_queue: Optional[queue.Queue] = None,
    ) -> None:
        self.email       = email
        self.password    = app_password
        self.imap_host   = imap_host
        self.folder      = folder
        self.alert_queue = alert_queue

        self.client: Optional[IMAPClient] = None
        self.running: bool                = False
        self._last_status: float          = time.time()

    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        self.client = IMAPClient(self.imap_host, ssl=True)
        self.client.login(self.email, self.password)
        self.client.select_folder(self.folder)
        logger.info("IMAP connected to %s / %s", self.imap_host, self.folder)
        self._notify("✅ IMAP connected – watching inbox")

    def _notify(self, text: str) -> None:
        if self.alert_queue:
            self.alert_queue.put(text)

    # ------------------------------------------------------------------ #
    def read_unseen(self) -> list[dict]:
        if not self.client:
            return []
        try:
            msg_ids = self.client.search(["UNSEEN"])
            emails  = []
            for mid in msg_ids:
                raw = self.client.fetch([mid], ["BODY[]", "FLAGS"])
                msg = pyzmail.PyzMessage.factory(raw[mid][b"BODY[]"])
                emails.append({
                    "from":    msg.get_addresses("from"),
                    "subject": msg.get_subject(),
                    "body":    self._body(msg),
                })
            return emails
        except Exception as exc:
            logger.error("read_unseen error: %s", exc)
            return []

    def _body(self, msg) -> str:
        if msg.text_part:
            return msg.text_part.get_payload().decode(
                msg.text_part.charset or "utf-8", errors="replace"
            )
        if msg.html_part:
            return msg.html_part.get_payload().decode(
                msg.html_part.charset or "utf-8", errors="replace"
            )
        return ""

    # ------------------------------------------------------------------ #
    def listen(self, callback: Callable[[dict], None]) -> None:
        """
        Blocking IMAP IDLE loop.  Run in a daemon thread.
        Reconnects automatically on failure.
        """
        if not self.client:
            self.connect()

        # Drain any unread emails on startup
        for email in self.read_unseen():
            callback(email)

        self.running = True
        logger.info("IMAP IDLE listener started")

        while self.running:
            try:
                self.client.idle()
                responses = self.client.idle_check(timeout=29)
                self.client.idle_done()

                new_found = False
                for resp in responses:
                    if b"EXISTS" in resp:
                        for email in self.read_unseen():
                            callback(email)
                        new_found = True

                now = time.time()
                if not new_found and (now - self._last_status) > STATUS_UPDATE_INTERVAL:
                    self._notify("💤 No new emails in last hour – IMAP still monitoring")
                    self._last_status = now

            except Exception as exc:
                logger.error("IMAP IDLE error: %s", exc)
                self._notify(f"⚠️ IMAP error: {exc}. Reconnecting in 5s…")
                time.sleep(5)
                try:
                    self.connect()
                except Exception as reconn_exc:
                    logger.error("IMAP reconnect failed: %s", reconn_exc)
                    time.sleep(30)

    def stop(self) -> None:
        self.running = False
        if self.client:
            try:
                self.client.idle_done()
                self.client.logout()
            except Exception:
                pass
        logger.info("IMAP listener stopped")