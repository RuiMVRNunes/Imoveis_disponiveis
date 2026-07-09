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
        # Several recipients (me + my wife). Each phone has its OWN apikey.
        # Two ways to add people, both supported and combined:
        #  - comma-separated in CALLMEBOT_PHONE / CALLMEBOT_APIKEY, or
        #  - numbered extras CALLMEBOT_PHONE_2/_3 + CALLMEBOT_APIKEY_2/_3
        #    (lets you add someone without touching the existing secrets).
        phones = _split(os.environ.get("CALLMEBOT_PHONE", ""))
        apikeys = _split(os.environ.get("CALLMEBOT_APIKEY", ""))
        for n in (2, 3, 4):
            phones += _split(os.environ.get(f"CALLMEBOT_PHONE_{n}", ""))
            apikeys += _split(os.environ.get(f"CALLMEBOT_APIKEY_{n}", ""))
        self.recipients = list(zip(phones, apikeys))
        if len(phones) != len(apikeys):
            log.warning(
                "whatsapp: nº de CALLMEBOT_PHONE (%d) != CALLMEBOT_APIKEY (%d); "
                "só uso os pares completos", len(phones), len(apikeys)
            )

    def is_enabled(self) -> bool:
        if not self.channel.enabled:
            return False
        if not self.recipients:
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
        errors = []
        for phone, apikey in self.recipients:
            try:
                response = httpx.get(
                    API_URL,
                    params={"phone": phone, "apikey": apikey, "text": body},
                    timeout=30.0,
                )
                response.raise_for_status()
                # CallMeBot returns HTTP 200 with an error string on bad keys.
                if "error" in response.text.lower() and "message queued" not in response.text.lower():
                    errors.append(f"{phone}: {response.text[:120]}")
            except httpx.HTTPError as exc:
                errors.append(f"{phone}: {exc}")
        # Only fail if EVERY recipient failed (one bad number shouldn't block the other).
        if errors and len(errors) == len(self.recipients):
            raise NotifyError("whatsapp: envio falhou: " + "; ".join(errors)[:200])
        for err in errors:
            log.error("whatsapp: um destinatário falhou (%s)", err)


def _split(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
