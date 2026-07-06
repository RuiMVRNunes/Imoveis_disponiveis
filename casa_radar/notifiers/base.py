"""Notifier interface."""

from __future__ import annotations

import abc


class NotifyError(Exception):
    """A channel failed to deliver; other channels must keep going."""


class Notifier(abc.ABC):
    name: str = ""

    @abc.abstractmethod
    def is_enabled(self) -> bool:
        """Enabled in config AND has the secrets it needs in the environment."""

    @abc.abstractmethod
    def send(self, subject: str, text: str, html: str | None = None) -> None:
        """Deliver a message. ``text`` is mandatory; ``html`` is used by
        channels that support rich formatting (email). Raises NotifyError."""
