"""In-memory translation contracts; success does not imply indexed/ready."""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata

from solo_leveling.domain.models import TranslationRevision


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


class TranslationFailureCode(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EMPTY_TRANSLATION = "empty_translation"
    UNCHANGED_TRANSLATION = "unchanged_translation"
    TARGET_LANGUAGE_MISMATCH = "target_language_mismatch"


def translation_quality_failure(
        original_text: str, translated_text: str) -> TranslationFailureCode | None:
    """한국어 번역 결과의 명백한 미번역만 보수적으로 판정한다."""
    original = ' '.join(unicodedata.normalize('NFKC', original_text).split())
    translated = ' '.join(unicodedata.normalize('NFKC', translated_text).split())
    words = re.findall(r"[A-Za-z]{2,}", original)
    visible = re.sub(r'\s+', '', original)
    alpha_ratio = sum(map(len, words)) / max(1, len(visible))
    prose = len(words) >= 2 and sum(map(len, words)) >= 10 and alpha_ratio >= .65
    original_has_korean = re.search(r'[\uac00-\ud7a3]', original) is not None
    translated_has_korean = re.search(r'[\uac00-\ud7a3]', translated) is not None
    if original.casefold() == translated.casefold():
        if original_has_korean or not prose:
            return None
        return TranslationFailureCode.UNCHANGED_TRANSLATION
    if prose and not translated_has_korean:
        return TranslationFailureCode.TARGET_LANGUAGE_MISMATCH
    return None


@dataclass(frozen=True)
class TranslationSettings:
    provider: str
    model: str
    prompt_version: str
    target_language: str = "ko"

    def __post_init__(self) -> None:
        for name in ("provider", "model", "prompt_version"):
            require_text(getattr(self, name), name)
        if self.target_language != "ko":
            raise ValueError("only Korean translation is supported in M1")


@dataclass(frozen=True)
class TranslationRequest:
    chunk_id: str
    parse_revision_id: str
    original_text: str
    target_language: str = "ko"

    def __post_init__(self) -> None:
        for name in ("chunk_id", "parse_revision_id", "original_text"):
            require_text(getattr(self, name), name)
        if self.target_language != "ko":
            raise ValueError("only Korean translation is supported in M1")


@dataclass(frozen=True)
class ChunkTranslationResult:
    request: TranslationRequest
    text: str | None
    failure_code: TranslationFailureCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, TranslationRequest):
            raise ValueError("request must be TranslationRequest")
        if self.failure_code is None:
            require_text(self.text, "text")
        elif not isinstance(self.failure_code, TranslationFailureCode) or self.text is not None:
            raise ValueError("failure requires a failure code and text=None")

    @property
    def succeeded(self) -> bool:
        return self.failure_code is None


@dataclass(frozen=True)
class TranslationBatchResult:
    revision: TranslationRevision
    settings: TranslationSettings
    items: tuple[ChunkTranslationResult, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        # TranslationRevision is an existing mutable model: check again on use.
        if not isinstance(self.revision, TranslationRevision) or not isinstance(self.settings, TranslationSettings):
            raise ValueError("invalid revision or settings")
        require_text(self.revision.translation_revision_id, "translation_revision_id")
        require_text(self.revision.parse_revision_id, "parse_revision_id")
        if (self.revision.provider, self.revision.model) != (self.settings.provider, self.settings.model):
            raise ValueError("revision provider/model differs from settings")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("batch requires a nonempty tuple of results")
        ids = set()
        for item in self.items:
            if not isinstance(item, ChunkTranslationResult):
                raise ValueError("invalid chunk result")
            if item.request.parse_revision_id != self.revision.parse_revision_id:
                raise ValueError("mixed parse revisions")
            if item.request.target_language != self.settings.target_language:
                raise ValueError("mixed target languages")
            if item.request.chunk_id in ids:
                raise ValueError("duplicate chunk ID")
            ids.add(item.request.chunk_id)

    @property
    def success_count(self) -> int:
        return sum(item.succeeded for item in self.items)

    @property
    def failure_count(self) -> int:
        return len(self.items) - self.success_count
