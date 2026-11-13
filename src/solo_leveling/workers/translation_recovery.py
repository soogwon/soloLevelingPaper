"""명시적 접수만 실행하는 로컬 M1 번역 복구 서비스 (#222).

MCP는 이 경계를 사용한다. DB 쓰기는 A의 접수·예약·저장·게시 API만 사용하며,
외부 처리 중 트랜잭션을 유지하지 않는다. 생성/조회가 context를 바꾸지 않는다.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import sys
from threading import Lock
from uuid import uuid4

from solo_leveling.diagnostics import stage, traced
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError, ResourceNotFoundError
from solo_leveling.domain.models import (
    EmbeddingSet, JobStage, JobStatus, ProcessingJob, TranslationRecoveryDisposition as Disposition,
)
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.domain.translation_recovery import RecoveryConfigurationError, RecoveryResult
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.storage.vector_store import (
    get_client, close_client, ChromaCloseResult, upsert_chunk_embeddings, verify_chunk_embeddings,
)


def _event(code):
    try:
        print(json.dumps({'event': 'translation_recovery', 'code': code}), file=sys.stderr, flush=True)
    except Exception:
        pass


def _input(version_id, base_embedding_set_id, request_key):
    for value in (version_id, base_embedding_set_id, request_key):
        if (type(value) is not str or not value.strip() or value != value.strip()
                or len(value) > 200 or any(ord(c) < 32 for c in value)):
            raise InvalidArgumentError('복구 입력값을 확인해주세요.')
    value = ['translation-recovery-v1', version_id, base_embedding_set_id]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class _Work:
    version_id: str
    base: dict


class TranslationRecoveryService:
    def __init__(self, db_path, chroma_dir, *, translation_service, translation_settings,
                 embedder, chroma_client=None, provider_available=True):
        self.db_path, self.chroma_dir = db_path, chroma_dir
        self.translation_service, self.translation_settings = translation_service, translation_settings
        self.embedder, self.client = embedder, chroma_client
        self.provider_available = provider_available is True
        self._lock = Lock()
        self._pending = {}
        # DB의 interrupted는 외부 writer 종료 증명이 아니다. 이 인스턴스가 실행 종료를
        # 확인한 미게시 작업만 명시적 정리를 허용한다. 재시작 뒤에는 임의 복구하지 않는다.
        self._quiescent = set()
        self._cleanup_unconfirmed = set()

    def _request(self, *, request_key=None, job_id=None):
        # 조회 전용. 요청 키 조회를 접수 함수로 대신해 새 작업을 만들지 않는다.
        field, value = ('request_key', request_key) if request_key is not None else ('job_id', job_id)
        with closing(repo.get_connection(self.db_path)) as conn:
            row = conn.execute(
                f'SELECT * FROM translation_recovery_requests WHERE {field}=?', (value,)).fetchone()
            return dict(row) if row else None

    def _result(self, request):
        version, base_id, job_id = (request[k] for k in ('version_id', 'base_embedding_set_id', 'job_id'))
        base = repo.get_search_index(self.db_path, version, embedding_set_id=base_id)
        job = repo.get_job(self.db_path, job_id)
        if base is None or job is None:
            return RecoveryResult('UNKNOWN', version, base_id, job_id,
                                  reason_code='RECOVERY_STATE_UNAVAILABLE')
        target = len(repo.get_translation_recovery_targets(self.db_path, base['translation_revision_id']))
        published = repo.get_search_index_for_job(self.db_path, job_id)
        if published is not None:
            if (published['version_id'] != version
                    or published['parse_revision_id'] != base['parse_revision_id']):
                return RecoveryResult('UNKNOWN', version, base_id, job_id,
                                      reason_code='RECOVERY_STATE_UNAVAILABLE')
            remaining = len(repo.get_translation_recovery_targets(
                self.db_path, published['translation_revision_id']))
            recovered = target - remaining
            if recovered < 1 or recovered + remaining != target:
                return RecoveryResult('UNKNOWN', version, base_id, job_id,
                                      reason_code='RECOVERY_STATE_UNAVAILABLE')
            return RecoveryResult('COMPLETED', version, base_id, job_id, published['embedding_set_id'],
                                  'FULL' if remaining == 0 else 'PARTIAL', target_count=target,
                                  recovered_count=recovered, remaining_failed_count=remaining,
                                  result_available=True)
        status = {JobStatus.PROCESSING: 'IN_PROGRESS', JobStatus.QUEUED: 'IN_PROGRESS',
                  JobStatus.INTERRUPTED: 'INTERRUPTED', JobStatus.FAILED: 'FAILED'}.get(job.status, 'UNKNOWN')
        codes = ('RECOVERY_NO_PROGRESS', 'RECOVERY_BASE_CHANGED', 'RECOVERY_TRANSLATION_FAILED',
                 'RECOVERY_EMBEDDING_FAILED', 'RECOVERY_INDEX_FAILED', 'RECOVERY_SUBMISSION_FAILED')
        code = next((c for c in codes if c in job.limitations), None)
        no_progress = code == 'RECOVERY_NO_PROGRESS'
        return RecoveryResult(status, version, base_id, job_id,
            outcome='NO_PROGRESS' if no_progress else None, reason_code=code,
            target_count=target, recovered_count=0 if no_progress else None,
            remaining_failed_count=target if no_progress else None)

    def lookup(self, version_id, base_embedding_set_id, request_key):
        fingerprint = _input(version_id, base_embedding_set_id, request_key)
        request = self._request(request_key=request_key)
        if request is None:
            raise ResourceNotFoundError('복구 요청을 찾을 수 없습니다.')
        if (request['version_id'], request['base_embedding_set_id'], request['input_hash']) != (
                version_id, base_embedding_set_id, fingerprint):
            return RecoveryResult('REQUEST_CONFLICT', version_id, base_embedding_set_id)
        return self._result(request)

    def preview(self, version_id, base_embedding_set_id=None, request_key='preview'):
        """외부 호출·작업 생성 없이 지정한 기본 색인의 복구 가능 수만 조회한다."""
        _input(version_id, 'current' if base_embedding_set_id is None else base_embedding_set_id, request_key)
        base = repo.get_search_index(self.db_path, version_id)
        if base is None or (base_embedding_set_id is not None and base['embedding_set_id'] != base_embedding_set_id):
            return {'status': 'BASE_CHANGED', 'target_count': None, 'translated_count': None,
                    'base_embedding_set_id': base_embedding_set_id}
        base_id = base['embedding_set_id']
        count = len(repo.get_translation_recovery_targets(self.db_path, base['translation_revision_id']))
        if not count:
            return {'status': 'NO_TARGET', 'target_count': 0, 'translated_count': base['chunk_count'],
                    'base_embedding_set_id': base_id}
        try:
            self._validate_base(base)
        except RecoveryConfigurationError:
            return {'status': 'UNAVAILABLE', 'target_count': count, 'translated_count': base['chunk_count'],
                    'base_embedding_set_id': base_id}
        return {'status': 'AVAILABLE', 'target_count': count, 'translated_count': base['chunk_count'],
                'base_embedding_set_id': base_id}

    def prepare(self, version_id, base_embedding_set_id, request_key):
        fingerprint = _input(version_id, base_embedding_set_id, request_key)
        with self._lock:
            if self._request(request_key=request_key) is not None:
                return self.lookup(version_id, base_embedding_set_id, request_key)
            base = repo.get_search_index(self.db_path, version_id, embedding_set_id=base_embedding_set_id)
            current = repo.get_search_index(self.db_path, version_id)
            if base is None or current is None or current['embedding_set_id'] != base_embedding_set_id:
                return RecoveryResult('BASE_CHANGED', version_id, base_embedding_set_id)
            target = len(repo.get_translation_recovery_targets(self.db_path, base['translation_revision_id']))
            if target:
                self._validate_base(base)
            registration = repo.prepare_translation_recovery(self.db_path,
                ProcessingJob(str(uuid4()), version_id, JobStatus.PROCESSING, JobStage.TRANSLATE),
                request_key, fingerprint, base_embedding_set_id)
            if registration.disposition == Disposition.ACCEPTED:
                self._pending[registration.job_id] = _Work(version_id, base)
                return RecoveryResult('ACCEPTED', version_id, base_embedding_set_id,
                                      registration.job_id, target_count=target)
            if registration.job_id:
                return self.lookup(version_id, base_embedding_set_id, request_key)
            return RecoveryResult(registration.disposition.value, version_id, base_embedding_set_id,
                                  target_count=target)

    def _validate_base(self, base):
        metadata = repo.get_translation_metadata(self.db_path, base['parse_revision_id'],
                                                  base['translation_revision_id'])
        if metadata is None:
            raise RecoveryConfigurationError()
        settings = TranslationSettings(*(metadata[k] for k in
            ('provider', 'model', 'prompt_version', 'target_language')))
        if (not self.provider_available or settings != self.translation_settings or not callable(self.embedder)
                or not callable(getattr(self.translation_service, 'translate', None))
                or type(base['dimension']) is not int or base['dimension'] < 1):
            raise RecoveryConfigurationError()
        successful = repo.get_translated_chunks(self.db_path, base['parse_revision_id'], base['translation_revision_id'])
        originals = {c.chunk_id for c in repo.get_chunks_by_parse_revision(self.db_path, base['parse_revision_id'])}
        failures = {r['chunk_id'] for r in repo.get_translation_failures(self.db_path, base['translation_revision_id'])}
        with closing(repo.get_connection(self.db_path)) as conn:
            snapshot = conn.execute('SELECT source FROM search_index_chunk_snapshots WHERE embedding_set_id=?',
                                    (base['embedding_set_id'],)).fetchone()
            ids = {r[0] for r in conn.execute('SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id=?',
                                            (base['embedding_set_id'],))}
        if (snapshot is None or len(ids) != base['chunk_count'] or ids != {c.chunk_id for c in successful}
                or ids & failures or ids | failures != originals):
            raise RecoveryConfigurationError()

    @traced('translation_recovery', request=True)
    def run(self, job_id):
        # 같은 프로세스에서 신규 접수가 발급한 실행권은 한 번만 소비한다.
        # 시작 시 interrupted 확정은 기존 런타임 책임이며 이 서비스가 활성 잠금을 빼앗지 않는다.
        with self._lock:
            work = self._pending.pop(job_id, None)
        if work is None:
            request = self._request(job_id=job_id)
            if request is None:
                raise ResourceNotFoundError('복구 요청을 찾을 수 없습니다.')
            return self._result(request)
        try:
            return self._run_owned(job_id, work)
        finally:
            # _run_owned의 모든 벡터 작업과 소유 클라이언트 정리가 끝난 뒤에만 기억한다.
            try:
                with self._lock:
                    safe = job_id not in self._cleanup_unconfirmed
                    self._cleanup_unconfirmed.discard(job_id)
                if safe and repo.get_unpublished_recovery_embedding_set(self.db_path, job_id) is not None:
                    with self._lock:
                        self._quiescent.add(job_id)
            except Exception:
                _event('RECOVERY_STATE_UNAVAILABLE')

    def _run_owned(self, job_id, work):
        request = self._request(job_id=job_id)
        if request is None:
            raise ResourceNotFoundError('복구 요청을 찾을 수 없습니다.')
        base, version = work.base, work.version_id
        client, owns = self.client, False
        code = 'RECOVERY_TRANSLATION_FAILED'
        try:
            job = repo.get_job(self.db_path, job_id)
            if job is None or job.status != JobStatus.PROCESSING:
                return self._result(request)
            current = repo.get_search_index(self.db_path, version)
            if current is None or current['embedding_set_id'] != base['embedding_set_id']:
                code = 'RECOVERY_BASE_CHANGED'
                raise RuntimeError(code)
            self._validate_base(base)
            failed = {r['chunk_id'] for r in repo.get_translation_recovery_targets(
                self.db_path, base['translation_revision_id'])}
            chunks = [c for c in repo.get_chunks_by_parse_revision(self.db_path, base['parse_revision_id'])
                      if c.chunk_id in failed]
            with stage('recovery_translation'):
                batch = self.translation_service.translate(chunks, self.translation_settings)
                repo.save_recovery_translation(self.db_path, job_id, batch)
            if batch.success_count == 0:
                repo.fail_translation_recovery(self.db_path, job_id, ['RECOVERY_NO_PROGRESS'])
                return self._result(request)
            translated = repo.get_translated_chunks(self.db_path, base['parse_revision_id'],
                                                    batch.revision.translation_revision_id)
            code = 'RECOVERY_EMBEDDING_FAILED'
            with stage('recovery_embedding'):
                vectors = self.embedder([c.text for c in translated], model_name=base['model_name'])
            if (not isinstance(vectors, list) or len(vectors) != len(translated)
                    or any(not isinstance(v, list) or len(v) != base['dimension']
                           or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)
                           for v in vectors)):
                raise RuntimeError(code)
            embedding = EmbeddingSet(str(uuid4()), batch.revision.translation_revision_id,
                                     base['model_name'], base['dimension'])
            code = 'RECOVERY_INDEX_FAILED'
            repo.reserve_recovery_embedding_set(self.db_path, job_id, embedding.embedding_set_id)
            if client is None:
                client, owns = get_client(self.chroma_dir), True
            ids, texts = [c.chunk_id for c in translated], [c.text for c in translated]
            with closing(repo.get_connection(self.db_path)) as conn:
                paper_id = conn.execute('SELECT paper_id FROM paper_versions WHERE version_id=?',
                                        (version,)).fetchone()[0]
            with stage('recovery_index'):
                upsert_chunk_embeddings(client, ids, vectors, embedding.embedding_set_id, paper_id, version, texts)
                verify_chunk_embeddings(client, ids, texts, embedding.embedding_set_id, paper_id, version, base['dimension'])
                base_job = repo.get_job(self.db_path, base['job_id'])
                if base_job is None:
                    raise RecoveryConfigurationError()
                # 번역 복구가 빈 페이지 등 원래의 추출 제한까지 해결한 것으로 보이면 안 된다.
                limitations = [value for value in base_job.limitations
                    if isinstance(value, str) and value != 'translation_failed'
                    and not value.startswith('translation_failed:')]
                limitations.extend(f"translation_failed:{r['chunk_id']}:{r['failure_code']}" for r in
                    repo.get_translation_recovery_targets(
                        self.db_path, batch.revision.translation_revision_id))
                repo.publish_recovered_index(self.db_path, job_id, embedding, ids, limitations)
            return self._result(request)
        except Exception:
            # 커밋 후 응답 오류와 미게시 실패를 구분한다. DB 조회 실패면 게시·실패를 추측하지 않는다.
            try:
                if repo.get_search_index_for_job(self.db_path, job_id) is not None:
                    return self._result(request)
                current = repo.get_search_index(self.db_path, version)
                if current is not None and current['embedding_set_id'] != base['embedding_set_id']:
                    code = 'RECOVERY_BASE_CHANGED'
                job = repo.get_job(self.db_path, job_id)
                if job is not None and job.status == JobStatus.PROCESSING:
                    repo.fail_translation_recovery(self.db_path, job_id, [code])
                _event(code)
                return self._result(request)
            except Exception:
                _event('RECOVERY_STATE_UNAVAILABLE')
                return RecoveryResult('UNKNOWN', version, base['embedding_set_id'], job_id,
                                      reason_code='RECOVERY_STATE_UNAVAILABLE')
        finally:
            if owns:
                result = close_client(client)
                if result in (ChromaCloseResult.FAILED, ChromaCloseResult.UNSUPPORTED):
                    with self._lock:
                        self._cleanup_unconfirmed.add(job_id)
                    _event(result.value)

    def cancel_before_start(self, job_id):
        """호스트 종료·제출 실패 시 아직 소비하지 않은 로컬 실행권만 취소한다."""
        with self._lock:
            work = self._pending.pop(job_id, None)
        if work is None:
            return False
        try:
            repo.fail_translation_recovery(self.db_path, job_id, ['RECOVERY_SUBMISSION_FAILED'])
            return True
        except Exception:
            _event('RECOVERY_STATE_UNAVAILABLE')
            return False

    def cleanup(self, job_id):
        """운영자의 명시적 미게시 정리. 실패/중단 확정 전 또는 DB 불명 시 삭제하지 않는다."""
        client, owns = self.client, False
        try:
            with self._lock:
                if job_id not in self._quiescent:
                    return 'INDEX_CLEANUP_NOT_ALLOWED'
            request = self._request(job_id=job_id)
            job = repo.get_job(self.db_path, job_id)
            if request is None or job is None or job.status not in (JobStatus.FAILED, JobStatus.INTERRUPTED):
                return 'INDEX_CLEANUP_NOT_ALLOWED'
            candidate = repo.get_unpublished_recovery_embedding_set(self.db_path, job_id)
            if candidate is None:
                return 'INDEX_CLEANUP_NOT_REQUIRED'
            if client is None:
                client, owns = get_client(self.chroma_dir), True
            # 실패/중단 상태에서는 A의 게시가 거부된다. 삭제 직전 예약과 미게시를 다시 확인한다.
            if (repo.get_unpublished_recovery_embedding_set(self.db_path, job_id) != candidate
                    or repo.get_search_index_for_job(self.db_path, job_id) is not None):
                return 'INDEX_CLEANUP_NOT_ALLOWED'
            names = [entry if isinstance(entry, str) else entry.name for entry in client.list_collections()]
            name = f'chunks-{candidate}'
            if name in names:
                client.delete_collection(name=name)
                remaining = [entry if isinstance(entry, str) else entry.name for entry in client.list_collections()]
                if name in remaining:
                    _event('INDEX_CLEANUP_FAILED')
                    return 'INDEX_CLEANUP_FAILED'
            with self._lock:
                self._quiescent.discard(job_id)
            return 'INDEX_CLEANUP_COMPLETED'
        except Exception:
            _event('INDEX_CLEANUP_FAILED')
            return 'INDEX_CLEANUP_FAILED'
        finally:
            if owns:
                close_client(client)
