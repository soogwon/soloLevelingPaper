"""Synchronous provider port; no retries are performed by the service."""

from typing import Protocol

from solo_leveling.domain.translation import TranslationRequest, TranslationSettings


class TranslationProviderUnavailable(Exception):
    """Expected upstream failure; raw exception text must not enter results."""


class TranslationProvider(Protocol):
    def translate(self, request: TranslationRequest, settings: TranslationSettings) -> str:
        """Return translated text or raise TranslationProviderUnavailable.

        Unexpected exceptions and non-string responses are programming/adapter
        errors and must propagate rather than being hidden as per-chunk failures.
        """
        ...
