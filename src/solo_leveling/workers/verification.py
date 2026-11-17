"""지정된 첫 작업 하나만 실행하는 내부 검증 경계."""
from contextlib import contextmanager
from dataclasses import dataclass, fields
import re
from uuid import UUID, uuid4

from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore, _job


@dataclass(frozen=True, repr=False)
class VerificationConfig:
    database_name: str
    schema_name: str
    owner_user_id: str
    job_id: str
    paper_id: str
    version_id: str
    payload_hash: str
    worker_id: str
    lease_seconds: int
    max_staging_bytes: int

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in ('lease_seconds', 'max_staging_bytes'):
                if type(value) is not int or value < 1: raise ValueError('verification_config_invalid')
            elif type(value) is not str or not value.strip() or len(value) > 512:
                raise ValueError('verification_config_invalid')
        for name in ('job_id', 'paper_id'):
            if str(UUID(getattr(self, name))) != getattr(self, name): raise ValueError('verification_config_invalid')
        if (not re.fullmatch('[0-9a-f]{64}', self.payload_hash)
                or not re.fullmatch('[a-z][a-z0-9_]*', self.schema_name)
                or self.schema_name in ('public', 'information_schema') or self.schema_name.startswith('pg_')
                or not 3 <= self.lease_seconds <= 3600):
            raise ValueError('verification_config_invalid')


class IsolatedPool:
    """첫 연결뿐 아니라 모든 대여 연결에서 DB·schema를 검사한다."""
    def __init__(self, pool, config): self.pool, self.config = pool, config

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            row = conn.execute('SELECT current_database() AS db, current_schema() AS schema, current_schemas(false) AS schemas, current_schemas(true) AS resolved_schemas').fetchone()
            if row != {'db': self.config.database_name, 'schema': self.config.schema_name, 'schemas': [self.config.schema_name],
                       'resolved_schemas': ['pg_catalog', self.config.schema_name]}:
                raise RuntimeError('verification_database_mismatch')
            yield conn


class VerificationJobStore(PostgresProcessingJobStore):
    def __init__(self, pool, config):
        super().__init__(pool)
        self.config = config

    def claim(self, worker_id, *, lease_seconds=60):
        c = self.config
        if worker_id != c.worker_id or lease_seconds != c.lease_seconds:
            raise ValueError('verification_config_invalid')
        # 전역 candidate/만료 작업 정리를 사용하지 않으며 재실행도 자동 허용하지 않는다.
        with self._pool.connection() as conn:
            row = conn.execute('''UPDATE server_processing_jobs
                SET status='running',attempt=1,retryable=false,lease_owner=%s,lease_token=%s,
                    lease_expires_at=clock_timestamp()+make_interval(secs => %s),
                    heartbeat_at=clock_timestamp(),updated_at=clock_timestamp(),error_code=NULL,finished_at=NULL
                WHERE owner_user_id=%s AND job_id=%s AND paper_id=%s AND server_version_id=%s
                  AND payload_hash=%s AND job_kind='full_processing' AND status='queued' AND attempt=0
                  AND EXISTS (SELECT 1 FROM server_papers p WHERE p.owner_user_id=server_processing_jobs.owner_user_id
                    AND p.paper_id=server_processing_jobs.paper_id AND p.deleted_at IS NULL)
                RETURNING *''', (worker_id, str(uuid4()), lease_seconds, c.owner_user_id, c.job_id,
                    c.paper_id, c.version_id, c.payload_hash)).fetchone()
        return None if row is None else _job(row)


def run_once(config, resources):
    """resources는 운영자가 검토한 context manager가 공급하는 내부 의존성이다."""
    from solo_leveling.workers.processing_composition import create_processing_worker
    pool = IsolatedPool(resources.pool, config)
    options = dict(resources.stage_options)
    # bootstrap이 전역 queue나 worker 설정을 덮어쓰지 못하게 한다.
    forbidden = {'jobs', 'pool', 'objects', 'worker_id', 'lease_seconds', 'max_staging_bytes'}
    if forbidden.intersection(options): raise ValueError('verification_config_invalid')
    worker = create_processing_worker(pool, resources.objects, worker_id=config.worker_id,
        lease_seconds=config.lease_seconds, max_staging_bytes=config.max_staging_bytes,
        jobs=VerificationJobStore(pool, config), **options)
    result = worker.run_once()
    if result is None: return 'not_claimed'
    return 'published' if result.published is not None else 'not_published'
