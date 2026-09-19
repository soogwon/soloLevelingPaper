"""Create new successful Chunk views, never overwrite stored translations.

Returned chunks retain their IDs. They must NOT be blindly upserted into the
current one-translation-per-chunk schema; revision-aware persistence is pending.
"""

from dataclasses import replace
from typing import Sequence

from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationBatchResult


def translated_chunks(
    chunks: Sequence[Chunk], batch: TranslationBatchResult,
) -> tuple[Chunk, ...]:
    batch.validate()
    sources = {}
    for chunk in chunks:
        if not isinstance(chunk, Chunk) or chunk.chunk_id in sources:
            raise ValueError("invalid or duplicate source chunk")
        sources[chunk.chunk_id] = chunk
    if set(sources) != {item.request.chunk_id for item in batch.items}:
        raise ValueError("source chunks and batch results must match exactly")
    output = []
    for item in batch.items:
        source = sources[item.request.chunk_id]
        if (source.parse_revision_id, source.original_text) != (
            item.request.parse_revision_id, item.request.original_text,
        ):
            raise ValueError("source revision or original text changed")
        if source.translation_revision_id == batch.revision.translation_revision_id:
            raise ValueError("new revision must not overwrite an existing revision")
        if item.succeeded:
            output.append(replace(
                source, text=item.text,
                translation_revision_id=batch.revision.translation_revision_id,
            ))
    return tuple(output)
