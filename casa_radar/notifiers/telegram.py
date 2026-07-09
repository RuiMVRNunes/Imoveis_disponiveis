"""Telegram channel (BotFather bot + sendMessage). The most reliable channel;
recommended in the README even though WhatsApp stays on."""

from __future__ import annotations

import html as html_lib
import logging
import os

import httpx

from ..core.config import ChannelConfig
from .base import Notifier, NotifyError

log = logging.getLogger("casa_radar.notifiers.telegram")

MAX_CHARS = 4096


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, channel: ChannelConfig) -> None:
        self.channel = channel
        self.token = os.environ.get("TELEGRAM_TOKEN", "")
        # TELEGRAM_CHAT_ID may hold several ids (comma/space separated) so both
        # phones (e.g. me + my wife) get every message. A group chat id also works.
        raw = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.chat_ids = [c.strip() for c in raw.replace(";", ",").replace(" ", ",").split(",") if c.strip()]

    def is_enabled(self) -> bool:
        if not self.channel.enabled:
            return False
        if not self.token or not self.chat_ids:
            log.warning("telegram: ativo no config mas faltam TELEGRAM_TOKEN/TELEGRAM_CHAT_ID")
            return False
        return True

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        # HTML parse mode (safer than Markdown: no escaping surprises in titles)
        body = f"<b>{html_lib.escape(subject)}</b>\n{html_lib.escape(text)}" if subject else html_lib.escape(text)
        if len(body) > MAX_CHARS:
            body = body[: MAX_CHARS - 2].rstrip() + " …"
        errors = []
        for chat_id in self.chat_ids:
            try:
                response = httpx.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": body,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                    timeout=30.0,
                )
                payload = response.json()
                if not payload.get("ok", False):
                    errors.append(f"{chat_id}: {payload}")
            except httpx.HTTPError as exc:
                errors.append(f"{chat_id}: {exc}")
        # Only fail if EVERY recipient failed (one bad id shouldn't block the other).
        if errors and len(errors) == len(self.chat_ids):
            raise NotifyError("telegram: envio falhou: " + "; ".join(errors)[:200])
        for err in errors:
            log.error("telegram: um destinatário falhou (%s)", err)
