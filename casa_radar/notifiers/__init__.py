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
    factories = (
        lambda: EmailNotifier(config.notifications.email),
        lambda: WhatsAppNotifier(config.notifications.whatsapp),
        lambda: TelegramNotifier(config.notifications.telegram),
    )
    active = []
    for factory in factories:
        # Channel isolation starts at construction: bad env for one channel
        # (e.g. a malformed secret) must never take the others down.
        try:
            notifier = factory()
        except Exception as exc:
            log.error("notifiers: canal ignorado por configuração inválida: %s", exc)
            continue
        if notifier.is_enabled():
            active.append(notifier)
        else:
            log.debug("notifiers: canal '%s' inativo", notifier.name)
    return active
