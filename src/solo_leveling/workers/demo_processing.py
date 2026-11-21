"""승인 기간과 예산에 묶인 데모 PDF 소비자."""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.infrastructure.embeddings.token_windows import TokenWindowPolicy
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from solo_leveling.interfaces.cli.processing_verification import _unique
from solo_leveling.interfaces.http.object_storage import S3ObjectStorage
from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore, ProcessingJobLeaseLost, _job
from solo_leveling.workers.processing_composition import create_processing_worker
from solo_leveling.workers.processing_queue import QueueRunResult, run_processing_queue
from solo_leveling.workers.verification_bootstrap import _client, _pool, preflight, read_json, validate_plan


class DemoBudget:
    """전송 단가·입력 상한과 선택적 기간·건수.

    starts_at/expires_at는 둘 다 지정하거나 둘 다 비운다. 비우면 만료 없이 동작한다.
    max_jobs를 비우면 프로세스 건수 제한 없이 계속 대기열을 소비한다.
    """

    REQUIRED = {
        'input_token_limit', 'input_overhead_tokens',
        'input_microusd_per_million_tokens', 'output_microusd_per_million_tokens',
        'owner_budget_microusd', 'service_budget_microusd',
    }
    OPTIONAL = {'starts_at', 'expires_at', 'max_jobs'}

    def __init__(self, settings, model, output_limit):
        if (type(settings) is not dict or not self.REQUIRED <= set(settings)
                or not set(settings) <= self.REQUIRED | self.OPTIONAL
                or ('starts_at' in settings) != ('expires_at' in settings)):
            raise ValueError('demo_budget_invalid')
        amounts = {key: settings[key] for key in self.REQUIRED}
        if 'max_jobs' in settings:
            amounts['max_jobs'] = settings['max_jobs']
        if any(type(value) is not int or value <= 0 for value in amounts.values()):
            raise ValueError('demo_budget_invalid')
        if 'starts_at' in settings:
            if type(settings['starts_at']) is not str or type(settings['expires_at']) is not str:
                raise ValueError('demo_budget_invalid')
            self.starts_at = datetime.fromisoformat(settings['starts_at'])
            self.expires_at = datetime.fromisoformat(settings['expires_at'])
            if (self.starts_at.tzinfo is None or self.expires_at.tzinfo is None
                    or not self.starts_at < self.expires_at):
                raise ValueError('demo_budget_invalid')
        else:
            self.starts_at = self.expires_at = None
        self.max_jobs = settings.get('max_jobs')
        if ((self.max_jobs is not None and not 1 <= self.max_jobs <= 10000)
                or settings['owner_budget_microusd'] > settings['service_budget_microusd']
                or settings['input_overhead_tokens'] >= settings['input_token_limit']):
            raise ValueError('demo_budget_invalid')
        self.settings, self.model, self.output_limit = dict(settings), model, output_limit

    def active(self):
        if self.starts_at is None:
            return True
        return self.starts_at <= datetime.now(timezone.utc) < self.expires_at

    def upper_bound(self, model, payload):
        serialized = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        upper = len(serialized.encode('utf-8')) + self.settings['input_overhead_tokens']
        if (not self.active() or model != self.model or payload.get('model') != self.model
                or payload.get('max_output_tokens') != self.output_limit
                or upper > self.settings['input_token_limit']):
            raise ValueError('demo_payload_out_of_scope')
        return upper

    def allows_limits(self, limits):
        if not self.active():
            return False
        for row in limits:
            configured = (
                row['max_input_tokens'] * self.settings['input_microusd_per_million_tokens']
                + row['max_output_tokens'] * self.settings['output_microusd_per_million_tokens']
            )
            if configured > self.settings[f"{row['scope_type']}_budget_microusd"] * 1_000_000:
                return False
        return True


class DemoJobs(PostgresProcessingJobStore):
    """기존 대기 작업과 다른 worker의 작업을 건드리지 않는다."""

    def __init__(self, pool, budget, policy, window, *, automatic_only=False):
        super().__init__(pool)
        self.budget, self.policy, self.window = budget, policy, window
        self.current = None
        self.automatic_only = automatic_only

    def claim(self, worker_id, *, lease_seconds=60):
        self.current = None
        with self._pool.connection() as conn:
            row = conn.execute(
                """WITH candidate AS (
                       SELECT j.owner_user_id,j.job_id
                       FROM server_processing_jobs j
                       JOIN server_processing_job_authorizations a
                         ON a.owner_user_id=j.owner_user_id AND a.job_id=j.job_id
                       JOIN server_papers p ON p.owner_user_id=j.owner_user_id AND p.paper_id=j.paper_id
                       WHERE j.status='queued' AND j.attempt=0 AND j.job_kind='full_processing'
                         AND (%s::timestamptz IS NULL OR (j.created_at >= %s AND j.created_at < %s
                                                          AND CURRENT_TIMESTAMP < %s))
                         AND ((%s AND a.processing_policy_version=%s AND a.processing_window_key=%s)
                              OR (NOT %s AND a.processing_policy_version IS NULL))
                         AND p.deleted_at IS NULL
                         AND EXISTS (SELECT 1 FROM server_dispatch_controls
                                     WHERE control_id='provider_dispatch' AND dispatch_enabled)
                         AND EXISTS (
                             SELECT 1 FROM server_dispatch_budget_limits
                             WHERE scope_type='owner' AND scope_id=j.owner_user_id
                               AND policy_version=%s AND window_key=%s)
                         AND EXISTS (
                             SELECT 1 FROM server_dispatch_budget_limits
                             WHERE scope_type='service' AND scope_id='service'
                               AND policy_version=%s AND window_key=%s)
                       ORDER BY j.created_at,j.job_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED
                   )
                   UPDATE server_processing_jobs j SET status='running',attempt=1,retryable=false,
                       lease_owner=%s,lease_token=%s,heartbeat_at=CURRENT_TIMESTAMP,
                       lease_expires_at=CURRENT_TIMESTAMP+make_interval(secs => %s),
                       updated_at=CURRENT_TIMESTAMP
                   FROM candidate c WHERE j.owner_user_id=c.owner_user_id AND j.job_id=c.job_id
                   RETURNING j.*""",
                (self.budget.starts_at, self.budget.starts_at, self.budget.expires_at, self.budget.expires_at,
                 self.automatic_only, self.policy, self.window, self.automatic_only,
                 self.policy, self.window, self.policy, self.window, worker_id, str(uuid4()), lease_seconds),
            ).fetchone()
        self.current = None if row is None else _job(row)
        return self.current

    def settle_failed(self):
        if self.current is None:
            return False
        job = self.current
        try:
            self.fail(job.owner_user_id, job.job_id, job.lease_token,
                      error_code='demo_processing_failed', retryable=False)
        except ProcessingJobLeaseLost:
            # 부분 번역은 저장과 동시에 기존 실행을 이미 실패로 확정한다.
            with self._pool.connection() as conn:
                terminal = conn.execute(
                    "SELECT status,attempt FROM server_processing_jobs WHERE owner_user_id=%s AND job_id=%s",
                    (job.owner_user_id, job.job_id),
                ).fetchone()
            return (terminal is not None and terminal['attempt'] == job.attempt
                    and terminal['status'] in ('failed', 'cancelled'))
        return True


class DemoStop:
    def __init__(self, signal_event, budget):
        self.event, self.budget = signal_event, budget

    def is_set(self):
        return self.event.is_set() or not self.budget.active()

    def wait(self, seconds):
        if self.budget.expires_at is None:
            return self.event.wait(seconds) or self.is_set()
        remaining = (self.budget.expires_at - datetime.now(timezone.utc)).total_seconds()
        return self.event.wait(max(0, min(remaining, seconds))) or self.is_set()


def run(config_path, stop, *, environment=None, pool_factory=_pool, client_factory=_client,
        verify=preflight, worker_factory=create_processing_worker):
    source = os.environ if environment is None else environment
    inline = source.get('SLP_DEMO_PROCESSING_CONFIG_JSON', '')
    if inline:
        if config_path or len(inline.encode('utf-8')) > 65536:
            raise ValueError('demo_config_invalid')
        raw = json.loads(inline, object_pairs_hook=_unique)
    else:
        if not config_path or not Path(config_path).is_absolute():
            raise ValueError('demo_config_invalid')
        raw = read_json(config_path)
    budget_settings = raw.pop('demo_budget')
    automatic_only = source.get('SLP_AUTOMATIC_PDF_PROCESSING_ENABLED') == 'true'
    if automatic_only != raw['policy_version'].startswith('pdf-auto-'):
        raise ValueError('automatic_pdf_policy_mismatch')
    schema = raw.pop('database_schema')
    database = raw.pop('database_name')
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,62}', schema):
        raise ValueError('demo_database_invalid')
    plan = validate_plan(None, raw, demo=True)
    translation = raw['translation']
    budget = DemoBudget(budget_settings, plan.translation.model, translation['max_output_tokens'])
    if budget.starts_at is None and not automatic_only:
        # 데모 정책은 접수 정책이 없는 과거 작업까지 가져갈 수 있어 기간을 반드시 둔다.
        raise ValueError('demo_budget_invalid')
    if (source.get('SLP_DEMO_PROCESSING_ENABLED') != 'true' or not budget.active()
            or source.get('WORKER_BASE_REFERENCE') != raw['runtime_image']):
        raise ValueError('demo_processing_disabled')
    verify(plan)
    # 누락된 자격도 형식 오류와 같은 고정 사유로 보고한다.
    dsn, key, secret, api_key = (source.get(name, '') for name in (
        'SLP_DEMO_DATABASE_URL', 'SLP_DEMO_R2_ACCESS_KEY_ID',
        'SLP_DEMO_R2_SECRET_ACCESS_KEY', 'SLP_DEMO_OPENAI_API_KEY',
    ))
    url = urlsplit(dsn)
    if (url.scheme not in ('postgresql', 'postgres') or not url.hostname or not url.username or not url.password
            or url.path != '/' + database or url.query != 'sslmode=verify-full' or url.fragment
            or not all(value.strip() for value in (key, secret, api_key))):
        raise ValueError('demo_credentials_invalid')
    service = OpenAITranslationConfig(api_key, plan.translation, translation['timeout_seconds'],
                                      translation['max_output_tokens'], True, False)
    retry = TranslationRetryPolicy(max_attempts=1, max_batch_retries=0,
        chunk_retry_window_seconds=translation['timeout_seconds'] + 1,
        batch_retry_window_seconds=translation['timeout_seconds'] + 1)
    split = raw.get('embedding_split_policy')
    fragment_policy = None if split is None else TokenWindowPolicy(
        split['payload_tokens'], split['overlap_tokens'], split['version'],
    )
    with ExitStack() as resources:
        pool = pool_factory(dsn, schema)
        resources.callback(pool.close)
        pool.open(wait=True, timeout=10)
        with pool.connection() as conn:
            identity = conn.execute('SELECT current_database() AS db,current_schema() AS schema').fetchone()
            if (identity['db'], identity['schema']) != (database, schema):
                raise ValueError('demo_database_invalid')
        client = client_factory(raw['r2'], key, secret)
        resources.callback(client.close)
        jobs = DemoJobs(pool, budget, raw['policy_version'], raw['window_key'], automatic_only=automatic_only)
        worker = worker_factory(
            pool, S3ObjectStorage(client, raw['r2']['bucket']),
            worker_id='demo-' + str(uuid4()), lease_seconds=90, max_staging_bytes=25_000_000,
            jobs=jobs,
            service_config=service, model_dir=Path(raw['model_dir']), embedding_spec=plan.spec,
            limits=plan.limits, retry_policy=retry, policy_version=raw['policy_version'],
            window_key=raw['window_key'], token_upper_bound=budget.upper_bound,
            validate_limits=lambda limits: not stop.is_set() and budget.allows_limits(limits),
            allow_partial=automatic_only, batch_size=raw['batch_size'], fragment_policy=fragment_policy,
        )
        guard = DemoStop(stop, budget)
        if budget.max_jobs is not None:
            return run_processing_queue(worker, guard, max_jobs=budget.max_jobs, settle_failed=jobs.settle_failed)
        return run_continuously(worker, guard, settle_failed=jobs.settle_failed)


def run_continuously(worker, stop, *, settle_failed, batch=10000, failure_pause_seconds=30.0,
                     queue=run_processing_queue):
    """건수 제한 없이 대기열을 계속 소비한다.

    실패한 작업은 queue가 재시도 없이 종결하고 attempt=0 조건으로 다시 가져가지 않으므로,
    잠시 쉰 뒤 다음 작업을 확인해도 같은 작업의 외부 호출이 반복되지 않는다.
    """
    processed = 0
    while True:
        result = queue(worker, stop, max_jobs=batch, settle_failed=settle_failed)
        processed += result.processed
        if result.status == 'stopped' or stop.is_set():
            return QueueRunResult('stopped', processed)
        if result.status in ('failed', 'not_published') and stop.wait(failure_pause_seconds):
            return QueueRunResult('stopped', processed)


# 자식 출력은 버려지므로 종료 코드로만 원인을 구분한다. 원문·자격·예외 내용은 담지 않는다.
EXIT_CODES = {
    'demo_config_invalid': 3, 'demo_budget_invalid': 3, 'demo_database_invalid': 3,
    'automatic_pdf_policy_mismatch': 3, 'demo_processing_disabled': 4, 'demo_credentials_invalid': 5,
}
EXIT_QUEUE_FAILED = 6


def exit_code(error):
    if isinstance(error, ValueError) and len(error.args) == 1 and error.args[0] in EXIT_CODES:
        return EXIT_CODES[error.args[0]]
    if isinstance(error, (KeyError, json.JSONDecodeError)):
        return EXIT_CODES['demo_config_invalid']
    return 1


def main():
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        result = run(os.environ.get('SLP_DEMO_PROCESSING_CONFIG', ''), stop)
        return 0 if result.status in ('stopped', 'limit_reached') else EXIT_QUEUE_FAILED
    except Exception as error:
        return exit_code(error)


if __name__ == '__main__':
    raise SystemExit(main())
