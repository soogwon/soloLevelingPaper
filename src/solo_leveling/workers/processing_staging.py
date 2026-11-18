"""현재 실행 세대의 검증된 전체 결과를 비공개 staging에 불변 저장한다."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from uuid import UUID

from solo_leveling.application.remote_processing import RemoteProcessingStages, validate_processing_configuration
from solo_leveling.domain.remote_processing import (
    EmbeddingBatch, EmbeddingSpec, ProcessingError, ProcessingLimits, ProcessingResult,
    ProcessingSource, ProcessedTranslation, ProcessingChunk, ParsedDocument,
)
from solo_leveling.domain.translation import TranslationSettings, TranslationStatus
from solo_leveling.domain.models import PaperSection
from solo_leveling.workers.processing_authorization import _AUTH_SOURCE, _IDENTITY, _USAGE_BLOCKED, _params


class ProcessingStagingError(RuntimeError):
    """원문/번역/DB 예외를 포함하지 않는 고정 내부 오류."""


@dataclass(frozen=True, repr=False)
class StagedProcessingResult:
    payload_sha256: str
    replayed: bool


def _require(ok):
    if not ok:
        raise ProcessingStagingError('processing_staging_invalid')


def _uuid(value):
    return type(value) is str and str(UUID(value)) == value


def _source(source):
    _require(type(source) is ProcessingSource)
    _require(all(_uuid(v) for v in (source.job_id, source.paper_id, source.lease_token,
        source.parse_revision_id, source.translation_revision_id, source.embedding_set_id)))
    _require(all(type(v) is str and v.strip() for v in (source.owner_user_id, source.version_id)))
    _require(type(source.pdf_size) is int and source.pdf_size > 0
             and type(source.pdf_sha256) is str and len(source.pdf_sha256) == 64
             and all(c in '0123456789abcdef' for c in source.pdf_sha256))


def _encode(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class PostgresProcessingResultStaging:
    def __init__(self, pool, *, limits, translation_settings, embedding_spec, max_result_bytes):
        _require(type(limits) is ProcessingLimits and all(type(v) is int and v > 0 for v in
            (limits.max_pdf_bytes, limits.max_pages, limits.max_chunks)))
        _require(type(translation_settings) is TranslationSettings and type(embedding_spec) is EmbeddingSpec
                 and type(max_result_bytes) is int and max_result_bytes > 0)
        validate_processing_configuration(limits, translation_settings, embedding_spec)
        self.pool, self.limits = pool, limits
        self.settings, self.spec, self.max_bytes = translation_settings, embedding_spec, max_result_bytes

    def _payload(self, result):
        try:
            _require(type(result) is ProcessingResult and result.status == 'prepared')
            source = result.source
            _source(source)
            _require(source.pdf_size <= self.limits.max_pdf_bytes and result.translation_settings == self.settings)
            RemoteProcessingStages._document(result.document, source, self.limits)
            chunks = result.document.chunks
            ids = tuple(c.chunk_id for c in chunks)
            _require(all(_uuid(value) for value in ids))
            _require(type(result.translations) is tuple and len(result.translations) == len(chunks))
            for chunk, item in zip(chunks, result.translations):
                _require(type(item) is ProcessedTranslation and item.chunk_id == chunk.chunk_id
                         and item.failure_code is None and isinstance(item.status, TranslationStatus)
                         and type(item.text) is str and item.text.strip())
            batch = result.embeddings
            _require(type(batch) is EmbeddingBatch and batch.spec == self.spec and batch.chunk_ids == ids
                     and type(batch.chunk_ids) is tuple and type(batch.vectors) is tuple
                     and len(batch.vectors) == len(chunks) and type(self.spec.dimension) is int
                     and 1 <= self.spec.dimension <= 4096 and self.spec.normalization == 'l2'
                     and self.spec.distance == 'cosine' and self.spec.precision == 'float32')
            for row in batch.vectors:
                _require(type(row) is tuple and len(row) == self.spec.dimension
                         and all(type(v) in (int, float) and math.isfinite(v) for v in row))
                _require(abs(math.hypot(*row) - 1) <= 1e-5)
            present = {c.pdf_page for c in chunks}
            _require(type(result.empty_pages) is tuple and all(type(p) is int for p in result.empty_pages)
                     and result.empty_pages == tuple(p for p in range(1, result.document.page_count + 1) if p not in present))
            payload = {'schema_version': 2, 'result': asdict(result)}
            # jsonb는 음의 0을 보존하지 않으므로 저장 전 동일한 수치 표현으로 고정한다.
            payload['result']['embeddings']['vectors'] = [
                [0.0 if v == 0 else float(v) for v in row] for row in batch.vectors]
            encoded = _encode(payload)
            _require(len(encoded) <= self.max_bytes)
            return payload, encoded, hashlib.sha256(encoded).hexdigest()
        except (ProcessingStagingError, ProcessingError, ValueError, TypeError, AttributeError, OverflowError):
            raise ProcessingStagingError('processing_staging_invalid') from None

    def _lock(self, conn, source):
        # 삭제와 같은 논문→job 순서. job을 독점해 저장과 새 provider 예약을 직렬화한다.
        paper = conn.execute('''SELECT paper_id FROM server_papers
            WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL FOR SHARE''',
            (source.owner_user_id, source.paper_id)).fetchone()
        if paper is None:
            raise ProcessingStagingError('processing_staging_not_allowed')
        job = conn.execute('''SELECT job_id FROM server_processing_jobs
            WHERE owner_user_id=%s AND job_id=%s AND status='running' AND lease_token=%s
              AND lease_expires_at>clock_timestamp() FOR UPDATE''',
            (source.owner_user_id, source.job_id, source.lease_token)).fetchone()
        if job is None:
            raise ProcessingStagingError('processing_staging_not_allowed')
        row = conn.execute((_AUTH_SOURCE + _IDENTITY).replace('CURRENT_TIMESTAMP', 'clock_timestamp()') +
            ' FOR SHARE OF a,i,u,sb,ab,sc,ac', _params(source)).fetchone()
        blocked = conn.execute(_USAGE_BLOCKED, (source.owner_user_id, source.job_id)).fetchone()
        if row is None or blocked is not None:
            raise ProcessingStagingError('processing_staging_not_allowed')
        return dict(row)

    @staticmethod
    def _live(conn, source):
        row = conn.execute('''SELECT job_id FROM server_processing_jobs
            WHERE owner_user_id=%s AND job_id=%s AND lease_token=%s AND status='running'
              AND lease_expires_at>clock_timestamp()''',
            (source.owner_user_id, source.job_id, source.lease_token)).fetchone()
        if row is None:
            raise ProcessingStagingError('processing_staging_not_allowed')

    def save(self, result):
        payload, _, _ = self._payload(result)
        source = result.source
        try:
            with self.pool.connection() as conn:
                payload['authorization'] = self._lock(conn, source)
                encoded = _encode(payload)
                _require(len(encoded) <= self.max_bytes)
                digest = hashlib.sha256(encoded).hexdigest()
                old = conn.execute('''SELECT payload_sha256, payload FROM server_processing_result_staging
                    WHERE owner_user_id=%s AND job_id=%s AND source_lease_token=%s''',
                    (source.owner_user_id, source.job_id, source.lease_token)).fetchone()
                if old is not None:
                    if old['payload_sha256'] != digest or _encode(old['payload']) != encoded:
                        raise ProcessingStagingError('processing_staging_conflict')
                else:
                    conn.execute('''INSERT INTO server_processing_result_staging
                        (owner_user_id,paper_id,server_version_id,job_id,source_lease_token,
                         payload_version,payload_sha256,payload)
                         VALUES (%s,%s,%s,%s,%s,2,%s,%s::jsonb)''',
                        (source.owner_user_id, source.paper_id, source.version_id, source.job_id,
                         source.lease_token, digest, encoded.decode('utf-8')))
                self._live(conn, source)
            return StagedProcessingResult(digest, old is not None)
        except ProcessingStagingError:
            raise
        except Exception:
            raise ProcessingStagingError('processing_staging_unavailable') from None

    def load(self, source):
        """현재 권한이 있는 내부 worker용 조회. 공개 API나 다른 세대 재사용 경로가 아니다."""
        try:
            _source(source)
            with self.pool.connection() as conn:
                authorization = self._lock(conn, source)
                data = self.read_locked(conn, source, authorization)
                self._live(conn, source)
                return data
        except ProcessingStagingError:
            raise
        except Exception:
            raise ProcessingStagingError('processing_staging_unavailable') from None

    def read_locked(self, conn, source, authorization):
        """호출자가 같은 transaction에서 권한 행을 잠근 뒤 사용하는 내부 포트."""
        row = conn.execute('''SELECT payload_version,payload_sha256,payload FROM server_processing_result_staging
            WHERE owner_user_id=%s AND job_id=%s AND source_lease_token=%s''',
            (source.owner_user_id, source.job_id, source.lease_token)).fetchone()
        if row is None:
            return None
        data = row['payload']
        encoded = _encode(data)
        version = data.get('schema_version')
        _require(len(encoded) <= self.max_bytes and type(version) is int and version in (1, 2)
                 and row['payload_version'] == version and data.get('authorization') == authorization
                 and data['result']['source'] == asdict(source)
                 and data['result']['translation_settings'] == asdict(self.settings)
                 and data['result']['embeddings']['spec'] == asdict(self.spec)
                 and hashlib.sha256(encoded).hexdigest() == row['payload_sha256'])
        return data

    def restore(self, data):
        """staging JSON을 타입 자료로 복원하고 게시 직전 전체 결과를 다시 검사한다."""
        try:
            version, raw = data['schema_version'], data['result']
            document = raw['document']
            embeddings = raw['embeddings']
            result = ProcessingResult(
                ProcessingSource(**raw['source']),
                ParsedDocument(document['page_count'], tuple(ProcessingChunk(**c) for c in document['chunks']),
                               tuple(PaperSection(**s) for s in document['sections'])),
                TranslationSettings(**raw['translation_settings']),
                tuple(ProcessedTranslation(
                    t['chunk_id'], t['text'], t['failure_code'],
                    TranslationStatus(t.get('status', TranslationStatus.TRANSLATED.value)))
                    for t in raw['translations']),
                EmbeddingBatch(EmbeddingSpec(**embeddings['spec']), tuple(embeddings['chunk_ids']),
                               tuple(tuple(row) for row in embeddings['vectors'])),
                tuple(raw['empty_pages']), raw['status'],
            )
            payload, _, _ = self._payload(result)
            expected = raw
            if version == 1:
                expected = dict(raw)
                expected['translations'] = [
                    {**item, 'status': TranslationStatus.TRANSLATED.value}
                    for item in raw['translations']]
            _require(set(data) == {'schema_version', 'authorization', 'result'}
                     and _encode(payload['result']) == _encode(expected))
            return result
        except Exception:
            raise ProcessingStagingError('processing_staging_invalid') from None
