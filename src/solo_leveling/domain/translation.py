"""In-memory translation contracts; success does not imply indexed/ready."""

from dataclasses import dataclass
from enum import Enum

from solo_leveling.domain.models import TranslationRevision


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


class TranslationFailureCode(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EMPTY_TRANSLATION = "empty_translation"


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
