"""Notifier registry. Channels are independent: one failing never stops the
others (the runner catches per-channel exceptions)."""

from __future__ import annotations

import logging

from ..core.config import AppConfig
from .base import Notifier
from .email import EmailNotifier
from .telegram import TelegramNotifier
from .whatsapp import WhatsAppNotifier

log = logging.getLogger("casa_radar.notifiers")


def build_notifiers(config: AppConfig) -> list[Notifier]:
    candidates: list[Notifier] = [
        EmailNotifier(config.notifications.email),
        WhatsAppNotifier(config.notifications.whatsapp),
        TelegramNotifier(config.notifications.telegram),
    ]
    active = []
    for notifier in candidates:
        if notifier.is_enabled():
            active.append(notifier)
        else:
            log.debug("notifiers: canal '%s' inativo", notifier.name)
    return active
