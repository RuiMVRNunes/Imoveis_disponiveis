"""WhatsApp channel via CallMeBot (free, one-time setup - see README)."""

from __future__ import annotations

import logging
import os

import httpx

from ..core.config import ChannelConfig
from .base import Notifier, NotifyError

log = logging.getLogger("casa_radar.notifiers.whatsapp")

API_URL = "https://api.callmebot.com/whatsapp.php"
MAX_CHARS = 1000  # CallMeBot messages get flaky past ~1k chars


class WhatsAppNotifier(Notifier):
    name = "whatsapp"

    def __init__(self, channel: ChannelConfig) -> None:
        self.channel = channel
        self.phone = os.environ.get("CALLMEBOT_PHONE", "")
        self.apikey = os.environ.get("CALLMEBOT_APIKEY", "")

    def is_enabled(self) -> bool:
        if not self.channel.enabled:
            return False
        if not self.phone or not self.apikey:
            log.warning(
                "whatsapp: ativo no config mas faltam CALLMEBOT_PHONE/CALLMEBOT_APIKEY"
            )
            return False
        return True

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        # WhatsApp stays compact: drop the dashboard link line (user preference).
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("Dashboard:")
        ).strip()
        body = f"{subject}\n\n{text}" if subject else text
        if len(body) > MAX_CHARS:
            body = body[: MAX_CHARS - 2].rstrip() + " …"
        try:
            response = httpx.get(
                API_URL,
                params={"phone": self.phone, "apikey": self.apikey, "text": body},
                timeout=30.0,
            )
            response.raise_for_status()
            # CallMeBot returns HTTP 200 with an error string on bad keys.
            if "error" in response.text.lower() and "message queued" not in response.text.lower():
                raise NotifyError(f"whatsapp: CallMeBot devolveu: {response.text[:200]}")
        except httpx.HTTPError as exc:
            raise NotifyError(f"whatsapp: envio falhou: {exc}") from exc
