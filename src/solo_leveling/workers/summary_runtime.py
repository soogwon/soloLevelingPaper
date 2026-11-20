"""명시적으로 승인한 정책·본문별 상한으로 요약 큐를 조립한다. 기본 비활성이다."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit, unquote, parse_qsl

from solo_leveling.application.summary.remote_contract import PROMPT_VERSIONS, _load as load
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from .verification_bootstrap import ApprovedPayloadBounds
from .remote_summary import PersistedSummaryExecutor, RemoteSummaryRunner


def require(ok):
    if not ok:
        raise ValueError('summary_runtime_config_invalid')


@dataclass(frozen=True, repr=False)
class SummaryPlan:
    raw: dict
    policy: RemoteExecutionPolicy


def load_plan(path):
    from pathlib import Path
    with Path(path).open('rb') as stream:
        raw_bytes = stream.read(1048577)
    require(len(raw_bytes) <= 1048576)
    raw = load(raw_bytes.decode('utf-8'))
    require(type(raw) is dict and set(raw) == {'schema_version','database_name','schema_name','database_role',
        'policy','generation','window_key','requests','prompt_version'})
    require(type(raw['schema_version']) is int and raw['schema_version']==1
            and raw['prompt_version'] in PROMPT_VERSIONS)
    for field in ('database_name','schema_name','database_role'):
        require(type(raw[field]) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',raw[field]))
    require(type(raw['window_key']) is str and 1 <= len(raw['window_key'].strip()) <= 200)
    policy = RemoteExecutionPolicy(**raw['policy'])
    require(policy.max_calls == 1 and type(raw['generation']) is dict
            and set(raw['generation']) == {'model','timeout_seconds','max_output_tokens'})
    settings = OpenAIGenerationSettings('configuration-only', **raw['generation'])
    require(settings.timeout_seconds <= policy.stage_timeout_seconds
            and settings.max_output_tokens == policy.max_output_tokens)
    bounds = ApprovedPayloadBounds(settings.model, settings.max_output_tokens, raw['requests'])
    require(bounds.max_requests == 1 and bounds.max_input <= policy.max_input_tokens
            and bounds.max_output == policy.max_output_tokens)
    return SummaryPlan(raw, policy)


def create_runner(pool, plan, api_key, *, post=None):
    settings = OpenAIGenerationSettings(api_key, **plan.raw['generation'], allow_external_api=True)
    def execute(intent, policy, allowed):
        require(policy == plan.policy)
        from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
        request = PostgresSummaryResults(pool).input(intent)
        require(request is not None and request.unpack()[2]['prompt_version'] == plan.raw['prompt_version'])
        # 카운터는 실행마다 새로 만들고 실제 예약·사용량은 영속 원장에서 공유한다.
        bounds = ApprovedPayloadBounds(settings.model, settings.max_output_tokens, plan.raw['requests'])
        executor = PersistedSummaryExecutor(pool, settings=settings, token_upper_bound=bounds,
            window_key=plan.raw['window_key'], post=post)
        return executor(intent, policy, allowed)
    return RemoteSummaryRunner(pool, execute, policy_version=plan.policy.version, window_key=plan.raw['window_key'])


def _pool(dsn, schema):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    return ConnectionPool(dsn, open=False, min_size=1, max_size=4, timeout=10,
        kwargs={'row_factory':dict_row,'connect_timeout':5,'options':f'-c search_path={schema}'})


@contextmanager
def build(plan, source, *, pool_factory=_pool, post=None):
    require(source.get('SUMMARY_WORKER_EXECUTION_ENABLED') == 'true')
    dsn, key = source.get('SUMMARY_WORKER_DATABASE_URL'), source.get('SUMMARY_WORKER_OPENAI_API_KEY')
    require(type(dsn) is str and type(key) is str and bool(key.strip()))
    url = urlsplit(dsn)
    require(url.scheme in ('postgres','postgresql') and url.hostname and url.password
            and unquote(url.username or '') == plan.raw['database_role']
            and unquote(url.path.lstrip('/')) == plan.raw['database_name'] and not url.fragment)
    query = parse_qsl(url.query, keep_blank_values=True)
    require(len(query)<=1 and all(k=='sslmode' for k,v in query))
    require(dict(query).get('sslmode') == 'verify-full' or url.hostname in ('localhost','127.0.0.1','::1')
            and dict(query).get('sslmode') in (None,'disable'))
    pool = pool_factory(dsn, plan.raw['schema_name'])
    try:
        pool.open(wait=True, timeout=10)
        with pool.connection() as conn:
            identity = conn.execute('SELECT current_database() AS database, current_schema() AS schema').fetchone()
            require(identity == {'database':plan.raw['database_name'],'schema':plan.raw['schema_name']})
            role = conn.execute('SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user').fetchone()
            require(role and role['rolname']==plan.raw['database_role'] and not any(
                role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit')))
        yield create_runner(pool, plan, key, post=post)
    finally:
        pool.close()


@contextmanager
def factory():
    try:
        plan = load_plan(os.environ.get('SUMMARY_WORKER_CONFIG',''))
        with build(plan, os.environ) as runner:
            yield runner
    except Exception:
        raise RuntimeError('summary_runtime_failed') from None
