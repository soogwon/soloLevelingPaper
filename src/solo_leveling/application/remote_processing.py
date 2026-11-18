"""검증된 PDF의 처리 단계만 실행한다. DB·R2·큐·모델 기본값·게시 기능은 없다."""

import hashlib
import math
from typing import Callable, Protocol

from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.domain.models import PaperSection
from solo_leveling.domain.translation import TranslationBatchResult, TranslationSettings
from solo_leveling.domain.remote_processing import (
    EmbeddingBatch, EmbeddingSpec, ParsedDocument, ProcessingChunk, ProcessingError,
    ProcessingLimits, ProcessingResult, ProcessingSource, ProcessedTranslation,
)


def _require(ok, code):
    if not ok:
        raise ProcessingError(code)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _positive(value):
    return type(value) is int and value > 0


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class ProcessingParser(Protocol):
    def parse(self, pdf: bytes, parse_revision_id: str) -> ParsedDocument: ...


class ProcessingTranslator(Protocol):
    def translate(self, source: ProcessingSource, chunks: tuple[ProcessingChunk, ...],
                  settings: TranslationSettings, check: Callable[[], None]) -> TranslationBatchResult: ...


class ProcessingEmbedder(Protocol):
    def embed(self, items: tuple[ProcessedTranslation, ...], spec: EmbeddingSpec) -> EmbeddingBatch: ...


def validate_processing_configuration(limits, translation_settings, embedding_spec):
    _require(isinstance(limits, ProcessingLimits) and all(_positive(v) for v in
             (limits.max_pdf_bytes, limits.max_pages, limits.max_chunks)), 'PROCESSING_CONFIG_INVALID')
    _require(isinstance(translation_settings, TranslationSettings), 'PROCESSING_CONFIG_INVALID')
    _require(isinstance(embedding_spec, EmbeddingSpec), 'PROCESSING_CONFIG_INVALID')
    spec = embedding_spec
    _require(all(_text(v) for v in (spec.model_id, spec.model_revision, spec.tokenizer_id,
             spec.tokenizer_revision, spec.pooling, spec.runtime_version))
             and _positive(spec.max_length) and _positive(spec.dimension) and _sha(spec.artifact_sha256)
             and spec.runtime == 'pytorch' and spec.precision == 'float32'
             and spec.distance == 'cosine' and spec.normalization in ('l2', 'none'),
             'PROCESSING_CONFIG_INVALID')


class RemoteProcessingStages:
    def __init__(self, parser: ProcessingParser, translator: ProcessingTranslator,
                 embedder: ProcessingEmbedder, *, limits: ProcessingLimits,
                 translation_settings: TranslationSettings, embedding_spec: EmbeddingSpec,
                 check_allowed: Callable[[ProcessingSource, str], bool]):
        validate_processing_configuration(limits, translation_settings, embedding_spec)
        spec = embedding_spec
        _require(callable(check_allowed) and callable(getattr(parser, 'parse', None))
                 and callable(getattr(translator, 'translate', None))
                 and callable(getattr(embedder, 'embed', None)), 'PROCESSING_CONFIG_INVALID')
        self.parser, self.translator, self.embedder = parser, translator, embedder
        self.limits, self.settings, self.spec = limits, translation_settings, spec
        self.check_allowed = check_allowed

    def _check(self, source, stage, check_allowed):
        try:
            allowed = check_allowed(source, stage)
        except Exception:
            raise ProcessingError('PROCESSING_CHECK_UNAVAILABLE') from None
        _require(allowed is True, 'PROCESSING_NOT_ALLOWED')

    @staticmethod
    def _document(document, source, limits):
        _require(isinstance(document, ParsedDocument) and _positive(document.page_count), 'PROCESSING_PARSE_INVALID')
        _require(document.page_count <= limits.max_pages, 'PROCESSING_LIMIT_EXCEEDED')
        _require(isinstance(document.chunks, tuple) and isinstance(document.sections, tuple), 'PROCESSING_PARSE_INVALID')
        _require(bool(document.chunks), 'PROCESSING_EMPTY_DOCUMENT')
        _require(len(document.chunks) <= limits.max_chunks, 'PROCESSING_LIMIT_EXCEEDED')
        ids, last_page = set(), 0
        section_ids = set()
        for ordinal, section in enumerate(document.sections, start=1):
            _require(isinstance(section, PaperSection) and section.parse_revision_id == source.parse_revision_id
                     and _text(section.section_id) and section.section_id not in section_ids
                     and _text(section.title) and type(section.ordinal) is int and section.ordinal == ordinal
                     and _positive(section.level)
                     and (section.parent_section_id is None or (_text(section.parent_section_id)
                          and section.parent_section_id in section_ids))
                     and _positive(section.start_pdf_page) and type(section.end_pdf_page) is int
                     and section.start_pdf_page <= section.end_pdf_page <= document.page_count,
                     'PROCESSING_PARSE_INVALID')
            section_ids.add(section.section_id)
        for index, chunk in enumerate(document.chunks):
            _require(isinstance(chunk, ProcessingChunk) and _text(chunk.chunk_id) and chunk.chunk_id not in ids
                     and chunk.parse_revision_id == source.parse_revision_id
                     and type(chunk.chunk_index) is int and chunk.chunk_index == index
                     and _text(chunk.original_text) and _positive(chunk.pdf_page)
                     and last_page <= chunk.pdf_page <= document.page_count
                     and (chunk.section_id is None or (_text(chunk.section_id) and chunk.section_id in section_ids))
                     and (chunk.printed_page_label is None or _text(chunk.printed_page_label)),
                     'PROCESSING_PARSE_INVALID')
            ids.add(chunk.chunk_id)
            last_page = chunk.pdf_page
        for section in document.sections:
            members = [c for c in document.chunks if c.section_id == section.section_id]
            expected = (members[0].chunk_index, members[-1].chunk_index) if members else (None, None)
            _require((section.start_chunk_index, section.end_chunk_index) == expected
                     and (not members or (type(section.start_chunk_index) is int
                                          and type(section.end_chunk_index) is int))
                     and all(section.start_pdf_page <= c.pdf_page <= section.end_pdf_page for c in members),
                     'PROCESSING_PARSE_INVALID')

    def run(self, source: ProcessingSource, pdf: bytes) -> ProcessingResult:
        # 같은 객체가 재설정돼도 이 실행의 정책·adapter·검사 포트는 바뀌지 않는다.
        parser, translator, embedder = self.parser, self.translator, self.embedder
        limits, settings, spec, check_allowed = self.limits, self.settings, self.spec, self.check_allowed

        def check(stage):
            self._check(source, stage, check_allowed)

        _require(isinstance(source, ProcessingSource), 'PROCESSING_INPUT_INVALID')
        _require(all(_text(v) for v in (source.owner_user_id, source.job_id, source.paper_id,
                 source.version_id, source.parse_revision_id, source.translation_revision_id,
                 source.embedding_set_id)) and _sha(source.pdf_sha256) and _positive(source.pdf_size)
                 and type(pdf) is bytes, 'PROCESSING_INPUT_INVALID')
        _require(len(pdf) <= limits.max_pdf_bytes, 'PROCESSING_LIMIT_EXCEEDED')
        _require(len(pdf) == source.pdf_size and hashlib.sha256(pdf).hexdigest() == source.pdf_sha256,
                 'PROCESSING_PDF_MISMATCH')
        _require(pdf.startswith(b'%PDF-'), 'PROCESSING_PDF_UNREADABLE')
        check('parse')
        try:
            document = parser.parse(pdf, source.parse_revision_id)
        except ProcessingError:
            raise
        except Exception:
            raise ProcessingError('PROCESSING_PARSE_FAILED') from None
        self._document(document, source, limits)
        check('translation')
        try:
            batch = translator.translate(source, document.chunks, settings,
                                              lambda: check('translation'))
        except (DispatchError, ProcessingError):
            raise
        except Exception:
            raise ProcessingError('PROCESSING_TRANSLATION_FAILED') from None
        try:
            _require(isinstance(batch, TranslationBatchResult), 'PROCESSING_TRANSLATION_INVALID')
            batch.validate()
            _require(batch.settings == settings
                     and batch.revision.translation_revision_id == source.translation_revision_id
                     and batch.revision.parse_revision_id == source.parse_revision_id
                     and len(batch.items) == len(document.chunks), 'PROCESSING_TRANSLATION_INVALID')
            for chunk, item in zip(document.chunks, batch.items):
                _require((item.request.chunk_id, item.request.parse_revision_id, item.request.original_text)
                         == (chunk.chunk_id, chunk.parse_revision_id, chunk.original_text),
                         'PROCESSING_TRANSLATION_INVALID')
            translations = tuple(
                ProcessedTranslation(i.request.chunk_id, i.text, i.failure_code, i.status)
                for i in batch.items)
        except (ValueError, AttributeError, TypeError):
            raise ProcessingError('PROCESSING_TRANSLATION_INVALID') from None
        present_pages = {c.pdf_page for c in document.chunks}
        empty_pages = tuple(p for p in range(1, document.page_count + 1) if p not in present_pages)
        if batch.failure_count:
            check('result')
            return ProcessingResult(source, document, settings, translations, None, empty_pages,
                                    'translation_incomplete')
        check('embedding')
        try:
            embeddings = embedder.embed(translations, spec)
        except ProcessingError:
            raise
        except Exception:
            raise ProcessingError('PROCESSING_EMBEDDING_FAILED') from None
        _require(isinstance(embeddings, EmbeddingBatch) and embeddings.spec == spec
                 and type(embeddings.chunk_ids) is tuple
                 and embeddings.chunk_ids == tuple(c.chunk_id for c in document.chunks)
                 and type(embeddings.vectors) is tuple and len(embeddings.vectors) == len(document.chunks),
                 'PROCESSING_EMBEDDING_INVALID')
        for vector in embeddings.vectors:
            _require(type(vector) is tuple and len(vector) == spec.dimension
                     and all(_finite(v) for v in vector),
                     'PROCESSING_EMBEDDING_INVALID')
            norm = math.hypot(*vector)
            _require(math.isfinite(norm) and norm > 0
                     and (spec.normalization != 'l2' or abs(norm - 1) <= 1e-5),
                     'PROCESSING_EMBEDDING_INVALID')
        check('result')
        return ProcessingResult(source, document, settings, translations, embeddings, empty_pages, 'prepared')
