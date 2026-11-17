"""네트워크·모델 없이 staging 경계를 검증하는 합성 결과."""
from uuid import uuid4
from solo_leveling.domain.models import PaperSection
from solo_leveling.domain.remote_processing import (
    ProcessingSource, ProcessingResult, ProcessingLimits, ProcessingChunk, ParsedDocument,
    ProcessedTranslation, EmbeddingSpec, EmbeddingBatch,
)
from solo_leveling.domain.translation import TranslationSettings

LIMITS = ProcessingLimits(100_000, 10, 100)
SETTINGS = TranslationSettings('synthetic', 'synthetic-model', 'v1')
SPEC = EmbeddingSpec('synthetic', 'revision', 'synthetic', 'revision', 'mean', 'l2', 128, 2,
                     'cosine', 'float32', 'pytorch', 'test', 'b' * 64)


def result(source=None):
    uid = lambda: str(uuid4())
    source = source or ProcessingSource('synthetic-owner', uid(), uid(), 'v1', uid(), uid(), uid(),
                                        'a' * 64, 100, uid())
    chunk = ProcessingChunk(uid(), source.parse_revision_id, 0, 'Synthetic text.', 1, 'section', 'i')
    section = PaperSection('section', source.parse_revision_id, 'Synthetic', 1, 1, 1, 1,
                           start_chunk_index=0, end_chunk_index=0)
    return ProcessingResult(source, ParsedDocument(2, (chunk,), (section,)), SETTINGS,
        (ProcessedTranslation(chunk.chunk_id, '합성 문장.', None),),
        EmbeddingBatch(SPEC, (chunk.chunk_id,), ((1.0, -0.0),)), (2,), 'prepared')
