"""A의 재파싱 저장 계약을 사용하는 명시적 B 실행 서비스 (#116).

일반 등록/MCP에 자동 연결하지 않는다. 로컬 단일 런타임에서 ACCEPTED 작업만
실행하며, 재시작·다른 인스턴스가 기존 작업을 임의로 이어 실행하지 않는다.
"""
from contextlib import closing, contextmanager, nullcontext
from dataclasses import asdict, dataclass
from functools import wraps
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
from threading import RLock
from uuid import uuid4

from solo_leveling.domain.models import (
    JobStage, JobStatus, ParseRevision, ProcessingJob, ReparseDisposition,
    ReparseRegistration, EmbeddingSet,
)
from solo_leveling.domain.reparse import ReparseExecutionError, ReparseFailure, ReparseResult
from solo_leveling.domain.translation import TranslationSettings, require_text
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.parsing.chunker import chunk_pages_with_sections
from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages
from solo_leveling.infrastructure.storage.vector_store import (
    ChromaCloseResult, close_client, get_client, upsert_chunk_embeddings, verify_chunk_embeddings,
)


def _safe_boundary(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ReparseExecutionError:
            raise
        except Exception:
            raise ReparseExecutionError('REPARSE_STATE_UNAVAILABLE') from None
    return call


@dataclass(frozen=True)
class _Work:
    request_key: str
    version_id: str
    base_parse_revision_id: str
    base_embedding_set_id: str
    paper_id: str
    file_hash: str
    stored_path: str
    job_id: str
    input_hash: str


class _Failed(Exception):
    def __init__(self, code):
        self.code = code.value


class _Inactive(Exception):
    pass


class _CheckedProvider:
    def __init__(self, service, work, provider):
        self.service, self.work, self.provider = service, work, provider

    def translate(self, request, settings):
        # 배치 안의 다음 청크와 재시도에서도 실행 조건을 다시 확인한다.
        self.service._checkpoint(self.work)
        return self.provider.translate(request, settings)


class ReparseService:
    def __init__(self, db_path, chroma_dir, managed_pdf_root, *, translation_service,
                 translation_settings: TranslationSettings, embedder, embedding_model: str,
                 parser=extract_pages, parser_version='pdf-parser-current-v1',
                 provider_available=False, allow_partial=False, chroma_client=None):
        self.db_path = str(Path(db_path).resolve())
        if not Path(self.db_path).is_file():
            raise ReparseExecutionError('REPARSE_DATABASE_UNAVAILABLE')
        if not isinstance(translation_settings, TranslationSettings) or not callable(embedder):
            raise ReparseExecutionError('REPARSE_CONFIGURATION_UNAVAILABLE')
        if not callable(getattr(translation_service, 'with_revision_id', None)) or not callable(parser):
            raise ReparseExecutionError('REPARSE_CONFIGURATION_UNAVAILABLE')
        for value in (embedding_model, parser_version):
            require_text(value, 'reparse setting')
        if type(provider_available) is not bool or type(allow_partial) is not bool:
            raise ReparseExecutionError('REPARSE_CONFIGURATION_UNAVAILABLE')
        self.chroma_dir, self.pdf_root = str(chroma_dir), Path(managed_pdf_root).resolve()
        self.translation, self.settings = translation_service, translation_settings
        self.embedder, self.embedding_model = embedder, embedding_model
        self.parser, self.parser_version = parser, parser_version
        self.provider_available, self.allow_partial = provider_available, allow_partial
        self.client = chroma_client
        self._lock, self._work, self._running = RLock(), {}, set()
        self._attempted = set()
        self._unreleased_clients = []

    @contextmanager
    def _read(self):
        # 조회로 DB를 만들지 않고 각 읽기를 동일한 스냅샷에 고정한다.
        with closing(sqlite3.connect(Path(self.db_path).as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            yield conn

    @staticmethod
    def _inputs(version_id, base_parse_revision_id, base_embedding_set_id, request_key):
        for name, value in (('version_id', version_id), ('base_parse_revision_id', base_parse_revision_id),
                            ('base_embedding_set_id', base_embedding_set_id), ('request_key', request_key)):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ReparseExecutionError('REPARSE_INVALID_ARGUMENT')

    def _registration(self, request_key):
        # A의 상태 계약을 읽기 전용 연결에서 해석한다. 조회에는 쓰기 연결을 열지 않는다.
        with self._read() as conn:
            return self._read_registration(conn, request_key)

    @staticmethod
    def _read_registration(conn, request_key):
        request = conn.execute('SELECT * FROM reparse_requests WHERE request_key=?', (request_key,)).fetchone()
        if request is None:
            return None
        job = conn.execute('SELECT status FROM processing_jobs WHERE job_id=?', (request['job_id'],)).fetchone()
        published = conn.execute('SELECT parse_revision_id,embedding_set_id FROM search_indexes WHERE job_id=?',
                                 (request['job_id'],)).fetchone()
        if published:
            status = ReparseDisposition.COMPLETED
        elif job and job['status'] in ('queued', 'processing'):
            status = ReparseDisposition.IN_PROGRESS
        elif job and job['status'] == 'interrupted':
            status = ReparseDisposition.INTERRUPTED
        else:
            status = ReparseDisposition.FAILED
        return ReparseRegistration(status, request_key, request['version_id'],
            request['base_parse_revision_id'], request['base_embedding_set_id'], request['job_id'],
            published['parse_revision_id'] if published else request['new_parse_revision_id'],
            published['embedding_set_id'] if published else None)

    @_safe_boundary
    def lookup(self, version_id, base_parse_revision_id, base_embedding_set_id, request_key):
        self._inputs(version_id, base_parse_revision_id, base_embedding_set_id, request_key)
        # 같은 키여도 다른 버전의 작업 정보를 노출하지 않는다.
        with self._read() as conn:
            registration = self._read_registration(conn, request_key)
            if registration is None:
                return None
            if (registration.version_id, registration.base_parse_revision_id, registration.base_embedding_set_id) != (
                    version_id, base_parse_revision_id, base_embedding_set_id):
                return ReparseResult(ReparseDisposition.REQUEST_CONFLICT, version_id,
                                     base_parse_revision_id, base_embedding_set_id)
            return self._result(registration, conn=conn)

    def _result(self, registration, *, conn=None):
        if registration is None:
            raise ReparseExecutionError('REPARSE_STATE_UNAVAILABLE')
        values = dict(status=registration.disposition, version_id=registration.version_id,
                      base_parse_revision_id=registration.base_parse_revision_id,
                      base_embedding_set_id=registration.base_embedding_set_id,
                      job_id=registration.job_id, new_parse_revision_id=registration.new_parse_revision_id,
                      new_embedding_set_id=registration.new_embedding_set_id)
        with (self._read() if conn is None else nullcontext(conn)) as conn:
            if registration.disposition == ReparseDisposition.COMPLETED:
                index = conn.execute('''SELECT s.* FROM search_indexes s
                    JOIN parse_revisions p ON p.parse_revision_id=s.parse_revision_id AND p.version_id=s.version_id
                    JOIN translation_revisions t ON t.translation_revision_id=s.translation_revision_id
                        AND t.parse_revision_id=p.parse_revision_id
                    JOIN embedding_sets e ON e.embedding_set_id=s.embedding_set_id
                        AND e.translation_revision_id=t.translation_revision_id
                    JOIN processing_jobs j ON j.job_id=s.job_id AND j.version_id=s.version_id AND j.status='ready'
                    WHERE s.job_id=? AND s.embedding_set_id=? AND s.version_id=? AND s.parse_revision_id=?''',
                    (registration.job_id, registration.new_embedding_set_id,
                     registration.version_id, registration.new_parse_revision_id)).fetchone()
                if index is None:
                    raise ReparseExecutionError('REPARSE_STATE_UNAVAILABLE')
                failed = conn.execute('SELECT COUNT(*) FROM translation_results WHERE translation_revision_id=? '
                    'AND failure_code IS NOT NULL', (index['translation_revision_id'],)).fetchone()[0]
                values.update(translation_revision_id=index['translation_revision_id'],
                              chunk_count=index['chunk_count'], failed_count=failed, result_available=True)
            elif registration.job_id:
                job = conn.execute('SELECT limitations FROM processing_jobs WHERE job_id=?',
                                   (registration.job_id,)).fetchone()
                codes = json.loads(job['limitations']) if job else []
                values['reason_code'] = next((code.value for code in ReparseFailure if code.value in codes), None)
        return ReparseResult(**values)

    def _fingerprint(self, version_id, file_hash, base_parse, base_index):
        return hashlib.sha256(json.dumps({
            'version_id': version_id, 'file_hash': file_hash,
            'base_parse': base_parse, 'base_index': base_index,
            'parser': self.parser_version, 'translation': asdict(self.settings),
            'retry_policy': (asdict(self.translation.retry_policy)
                             if getattr(self.translation, 'retry_policy', None) is not None else None),
            'embedding_model': self.embedding_model, 'allow_partial': self.allow_partial,
        }, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    @_safe_boundary
    def prepare(self, version_id, base_parse_revision_id, base_embedding_set_id, request_key):
        self._inputs(version_id, base_parse_revision_id, base_embedding_set_id, request_key)
        with self._lock:
            with self._read() as conn:
                version = conn.execute('SELECT * FROM paper_versions WHERE version_id=?', (version_id,)).fetchone()
                if version is None:
                    raise ReparseExecutionError('REPARSE_VERSION_NOT_FOUND')
                existing = conn.execute('SELECT 1 FROM reparse_requests WHERE request_key=?', (request_key,)).fetchone()
            # 외부 전송 허용이 바뀌어도 이미 접수한 결과의 재조회는 허용한다.
            if not existing and not self.provider_available:
                raise ReparseExecutionError('REPARSE_PROVIDER_UNAVAILABLE')
            fingerprint = self._fingerprint(version_id, version['file_hash'],
                                            base_parse_revision_id, base_embedding_set_id)
            registration = repo.prepare_reparse(self.db_path,
                ProcessingJob(str(uuid4()), version_id, JobStatus.PROCESSING, JobStage.PARSE),
                request_key, fingerprint, base_parse_revision_id, base_embedding_set_id)
            if registration.disposition == ReparseDisposition.ACCEPTED:
                self._work[registration.job_id] = _Work(request_key, version_id, base_parse_revision_id,
                    base_embedding_set_id, version['paper_id'], version['file_hash'],
                    version['stored_path'], registration.job_id, fingerprint)
            # 충돌 시 입력 쪽 식별자만 반환한다.
            if registration.disposition == ReparseDisposition.REQUEST_CONFLICT:
                return ReparseResult(registration.disposition, version_id, base_parse_revision_id, base_embedding_set_id)
            return self._result(registration)

    def _checkpoint(self, work):
        registration = self._registration(work.request_key)
        if registration is None or registration.job_id != work.job_id or registration.disposition != ReparseDisposition.IN_PROGRESS:
            raise _Inactive()
        if (registration.version_id, registration.base_parse_revision_id, registration.base_embedding_set_id) != (
                work.version_id, work.base_parse_revision_id, work.base_embedding_set_id):
            raise _Inactive()
        with self._read() as conn:
            version = conn.execute('SELECT file_hash,stored_path FROM paper_versions WHERE version_id=?',
                                   (work.version_id,)).fetchone()
            request = conn.execute('SELECT input_hash FROM reparse_requests WHERE request_key=?',
                                   (work.request_key,)).fetchone()
            if (version is None or tuple(version) != (work.file_hash, work.stored_path)
                    or request is None or request['input_hash'] != work.input_hash):
                raise _Failed(ReparseFailure.FILE_MISMATCH)
        if not self.provider_available:
            raise _Failed(ReparseFailure.PROVIDER_UNAVAILABLE)
        if self._fingerprint(work.version_id, work.file_hash, work.base_parse_revision_id,
                             work.base_embedding_set_id) != work.input_hash:
            raise _Failed(ReparseFailure.CONFIGURATION_CHANGED)
        index = repo.get_search_index(self.db_path, work.version_id)
        if index is None or (index['parse_revision_id'], index['embedding_set_id']) != (
                work.base_parse_revision_id, work.base_embedding_set_id):
            raise _Failed(ReparseFailure.BASE_CHANGED)

    @contextmanager
    def _snapshot(self, work):
        try:
            source = (self.pdf_root / work.stored_path).resolve(strict=True)
            if not source.is_relative_to(self.pdf_root) or not source.is_file():
                raise OSError()
            with tempfile.TemporaryDirectory(prefix='solo-reparse-') as folder:
                copy = Path(folder) / 'source.pdf'
                digest = hashlib.sha256()
                with source.open('rb') as original, copy.open('xb') as target:
                    while block := original.read(1024 * 1024):
                        digest.update(block)
                        target.write(block)
                if digest.hexdigest() != work.file_hash:
                    raise _Failed(ReparseFailure.FILE_MISMATCH)
                yield str(copy)
        except OSError:
            raise _Failed(ReparseFailure.FILE_UNAVAILABLE) from None

    @_safe_boundary
    def run(self, job_id):
        with self._lock:
            work = self._work.get(job_id)
            if work is None:
                raise ReparseExecutionError('REPARSE_NOT_OWNED')
            if job_id in self._running or job_id in self._attempted:
                return self.lookup(work.version_id, work.base_parse_revision_id, work.base_embedding_set_id, work.request_key)
            if self._registration(work.request_key).disposition != ReparseDisposition.IN_PROGRESS:
                return self.lookup(work.version_id, work.base_parse_revision_id, work.base_embedding_set_id, work.request_key)
            self._running.add(job_id)
            # DB 상태가 불명확하게 끝나도 같은 실행권으로 유료 단계를 다시 시작하지 않는다.
            self._attempted.add(job_id)
        client, owns_client = self.client, False
        try:
            self._checkpoint(work)
            with self._snapshot(work) as source:
                pages = self.parser(source)
            self._checkpoint(work)
            if not pages:
                raise _Failed(ReparseFailure.EMPTY_EXTRACTION)
            revision = ParseRevision(str(uuid4()), work.version_id, len(pages))
            chunks, sections = chunk_pages_with_sections(revision.parse_revision_id, pages)
            if not chunks:
                raise _Failed(ReparseFailure.EMPTY_EXTRACTION)
            repo.save_reparse_result(self.db_path, job_id, revision, chunks, sections)
            self._checkpoint(work)
            translation_id = str(uuid4())
            translation = self.translation.with_revision_id(translation_id)
            translation.provider = _CheckedProvider(self, work, translation.provider)
            batch = translation.translate(chunks, self.settings)
            batch.validate()
            expected = {c.chunk_id: c.original_text for c in chunks}
            if (batch.settings != self.settings or batch.revision.parse_revision_id != revision.parse_revision_id
                    or batch.revision.translation_revision_id != translation_id
                    or {i.request.chunk_id: i.request.original_text for i in batch.items} != expected):
                raise _Failed(ReparseFailure.INVALID_TRANSLATION)
            self._checkpoint(work)
            repo.save_translation_batch(self.db_path, batch)
            if not batch.success_count:
                raise _Failed(ReparseFailure.TRANSLATION_FAILED)
            if batch.failure_count and not self.allow_partial:
                raise _Failed(ReparseFailure.PARTIAL_NOT_ALLOWED)
            translated = repo.get_translated_chunks(self.db_path, revision.parse_revision_id, translation_id)
            self._checkpoint(work)
            texts = [c.text for c in translated]
            vectors = self.embedder(texts, model_name=self.embedding_model)
            self._checkpoint(work)
            if (not isinstance(vectors, list) or len(vectors) != len(texts) or not vectors
                    or not isinstance(vectors[0], list) or not vectors[0]
                    or any(not isinstance(v, list) or len(v) != len(vectors[0])
                           or any(type(x) not in (int, float) or not math.isfinite(x) for x in v) for v in vectors)):
                raise _Failed(ReparseFailure.INVALID_EMBEDDING)
            embedding = EmbeddingSet(str(uuid4()), translation_id, self.embedding_model, len(vectors[0]))
            if client is None:
                client, owns_client = get_client(self.chroma_dir), True
            # 새 컬렉션만 사용하며, 실패해도 삭제 API는 호출하지 않는다.
            name = f'chunks-{embedding.embedding_set_id}'
            with self._read() as conn:
                if conn.execute('SELECT 1 FROM embedding_sets WHERE embedding_set_id=?',
                                (embedding.embedding_set_id,)).fetchone():
                    raise _Failed(ReparseFailure.FAILED)
            existing_names = {item if isinstance(item, str) else item.name for item in client.list_collections()}
            if name in existing_names:
                raise _Failed(ReparseFailure.FAILED)
            ids = [c.chunk_id for c in translated]
            upsert_chunk_embeddings(client, ids, vectors, embedding.embedding_set_id,
                work.paper_id, work.version_id, texts, collection_name=name)
            verify_chunk_embeddings(client, ids, texts, embedding.embedding_set_id,
                work.paper_id, work.version_id, embedding.dimension, collection_name=name)
            self._checkpoint(work)
            missing = sorted(set(range(1, len(pages) + 1)) - {c.pdf_page for c in chunks})
            shown = ','.join(map(str, missing[:20]))
            extra = f'+{len(missing) - 20}' if len(missing) > 20 else ''
            limitations = ([f'empty_pages:{shown}{extra}'] if missing else [])
            limitations += [f'translation_failed:{i.request.chunk_id}:{i.failure_code.value}'
                            for i in batch.items if not i.succeeded]
            repo.publish_reparsed_index(self.db_path, job_id, embedding, ids, limitations)
        except _Inactive:
            pass
        except BaseException as error:
            # 게시 후 응답만 유실됐으면 완료 결과를 우선하며 예외 본문은 저장하지 않는다.
            state = self._registration(work.request_key)
            if state and state.disposition == ReparseDisposition.IN_PROGRESS:
                code = error.code if isinstance(error, _Failed) else ReparseFailure.FAILED.value
                repo.fail_reparse(self.db_path, job_id, [code])
            if not isinstance(error, Exception):
                raise
        finally:
            try:
                if owns_client:
                    outcome = close_client(client)
                    if outcome not in (ChromaCloseResult.CLOSED, ChromaCloseResult.ALREADY_CLOSED):
                        from solo_leveling.diagnostics import report_runtime_cleanup
                        # 종료 미확정 자원의 마지막 참조를 버리거나 공유 클라이언트를 닫지 않는다.
                        with self._lock:
                            self._unreleased_clients.append(client)
                        report_runtime_cleanup(outcome.value)
            finally:
                with self._lock:
                    self._running.discard(job_id)
        return self.lookup(work.version_id, work.base_parse_revision_id, work.base_embedding_set_id, work.request_key)
