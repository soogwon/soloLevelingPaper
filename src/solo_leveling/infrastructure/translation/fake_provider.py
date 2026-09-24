"""Deterministic fixture provider; never calls the network or copies input."""

from typing import Mapping

from solo_leveling.application.translation.ports import TranslationProviderUnavailable
from solo_leveling.domain.translation import TranslationRequest, TranslationSettings


class FakeTranslationProvider:
    def __init__(self, translations: Mapping[str, str], failures: tuple[str, ...] = ()) -> None:
        self.translations = dict(translations)
        self.failures = frozenset(failures)
        self.calls: list[TranslationRequest] = []

    def translate(self, request: TranslationRequest, settings: TranslationSettings) -> str:
        if settings.provider != "fake":
            raise ValueError("fake provider requires provider='fake'")
        self.calls.append(request)
        if request.chunk_id in self.failures:
            raise TranslationProviderUnavailable("simulated upstream failure")
        if request.original_text not in self.translations:
            raise ValueError("missing fake translation fixture")
        return self.translations[request.original_text]
