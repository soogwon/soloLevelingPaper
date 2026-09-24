"""Translate a single parse revision in memory while preserving input chunks."""

from dataclasses import replace
from typing import Callable, Sequence
from uuid import uuid4

from solo_leveling.domain.models import Chunk, TranslationRevision
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationFailureCode,
    TranslationRequest, TranslationSettings, require_text,
)
from .ports import TranslationProvider, TranslationProviderUnavailable


class TranslationService:
    def __init__(
        self, provider: TranslationProvider,
        revision_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.provider = provider
        self.revision_id_factory = revision_id_factory or (lambda: str(uuid4()))

    def translate(
        self, chunks: Sequence[Chunk], settings: TranslationSettings,
    ) -> TranslationBatchResult:
        if not isinstance(settings, TranslationSettings):
            raise ValueError("settings must be TranslationSettings")
        inputs = tuple(chunks)
        if not inputs or any(not isinstance(c, Chunk) for c in inputs):
            raise ValueError("a nonempty sequence of Chunk is required")
        snapshots = tuple(replace(c) for c in inputs)
        # Validate the entire batch before the first provider call.
        requests = tuple(TranslationRequest(
            c.chunk_id, c.parse_revision_id, c.original_text, settings.target_language,
        ) for c in snapshots)
        parse_id = requests[0].parse_revision_id
        if any(r.parse_revision_id != parse_id for r in requests):
            raise ValueError("all chunks must belong to one parse revision")
        if len({r.chunk_id for r in requests}) != len(requests):
            raise ValueError("duplicate chunk ID")
        revision_id = self.revision_id_factory()
        require_text(revision_id, "translation_revision_id")
        if any(c.translation_revision_id == revision_id for c in snapshots):
            raise ValueError("retranslation requires a new revision ID")
        revision = TranslationRevision(revision_id, parse_id, settings.provider, settings.model)
        results = []
        for request in requests:
            try:
                text = self.provider.translate(request, settings)
            except TranslationProviderUnavailable:
                results.append(ChunkTranslationResult(
                    request, None, TranslationFailureCode.PROVIDER_UNAVAILABLE,
                ))
                continue
            if not isinstance(text, str):
                raise TypeError("translation provider must return str")
            if not text.strip():
                results.append(ChunkTranslationResult(
                    request, None, TranslationFailureCode.EMPTY_TRANSLATION,
                ))
            else:
                results.append(ChunkTranslationResult(request, text))
        return TranslationBatchResult(revision, settings, tuple(results))
