"""단발 검증 worker의 명시적 설정·자격·연결 수명을 조립한다."""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from types import MappingProxyType
from urllib.parse import urlsplit, unquote, parse_qsl

from solo_leveling.application.remote_processing import validate_processing_configuration
from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.domain.remote_processing import EmbeddingSpec, ProcessingLimits
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, PROMPT_VERSION
from solo_leveling.interfaces.cli.processing_verification import _unique
from solo_leveling.interfaces.http.object_storage import S3ObjectStorage
from solo_leveling.workers.image_preflight import artifact_check, runtime_check
from solo_leveling.workers.verification import IsolatedPool


def require(ok):
    if not ok: raise ValueError('verification_bootstrap_invalid')


def scope_digest(config):
    return hashlib.sha256(json.dumps(asdict(config), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    path = Path(path)
    require(path.is_absolute())
    with path.open('rb') as stream: raw = stream.read(1_048_577)
    require(len(raw) <= 1_048_576)
    return json.loads(raw, object_pairs_hook=_unique)


def positive(value): return type(value) is int and value > 0


class ApprovedPayloadBounds:
    """운영자가 검토한 정확한 payload별 상한만 사용한다. 토큰 추정기는 아니다."""
    def __init__(self, model, output_tokens, settings):
        require(type(settings) is dict and set(settings) == {
            'max_requests', 'max_input_tokens', 'max_output_tokens', 'payloads'})
        require(all(positive(settings[k]) for k in ('max_requests', 'max_input_tokens', 'max_output_tokens')))
        rows = settings['payloads']
        require(type(rows) is dict and bool(rows) and all(type(k) is str and
            re.fullmatch('[0-9a-f]{64}', k) and positive(v) for k, v in rows.items()))
        require(positive(output_tokens) and output_tokens <= settings['max_output_tokens']
                and all(v <= settings['max_input_tokens'] for v in rows.values()))
        self.model, self.output = model, output_tokens
        self.rows = MappingProxyType(dict(rows))
        self.max_requests, self.max_input, self.max_output = (
            settings['max_requests'], settings['max_input_tokens'], settings['max_output_tokens'])
        self.calls = self.input_tokens = self.output_tokens = 0
        self.lock = Lock()

    def __call__(self, model, payload):
        require(model == self.model and type(payload) is dict and payload.get('model') == self.model
                and type(payload.get('max_output_tokens')) is int and payload['max_output_tokens'] == self.output)
        upper = self.rows.get(payload_digest(payload))
        require(upper is not None)
        with self.lock:
            require(self.calls < self.max_requests and self.input_tokens + upper <= self.max_input
                    and self.output_tokens + self.output <= self.max_output)
            # 뒤의 영속 예약이 거절되어도 이 단발 실행의 한도는 되돌리지 않는다.
            self.calls += 1
            self.input_tokens += upper
            self.output_tokens += self.output
        return upper


@dataclass(frozen=True, repr=False)
class Plan:
    raw: dict
    spec: EmbeddingSpec
    limits: ProcessingLimits
    translation: TranslationSettings


def load_plan(config, path):
    raw = read_json(path)
    require(type(raw) is dict and set(raw) == {'schema_version', 'scope_sha256', 'runtime_image',
        'runtime_lock_path', 'model_lock_path', 'model_dir', 'embedding_spec', 'limits', 'translation',
        'batch_size', 'policy_version', 'window_key', 'r2', 'requests'})
    require(type(raw['schema_version']) is int and raw['schema_version'] == 1 and raw['scope_sha256'] == scope_digest(config))
    require(type(raw['runtime_image']) is str and re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', raw['runtime_image']))
    for name in ('runtime_lock_path', 'model_lock_path', 'model_dir'):
        require(type(raw[name]) is str and Path(raw[name]).is_absolute())
    for name in ('policy_version', 'window_key'):
        require(type(raw[name]) is str and bool(raw[name].strip()))
    require(positive(raw['batch_size']))
    tr = raw['translation']
    require(type(tr) is dict and set(tr) == {'model', 'timeout_seconds', 'max_output_tokens'})
    translation = TranslationSettings('openai', tr['model'], PROMPT_VERSION)
    # 자격을 읽지 않고도 설정의 타입·유한성·제공자 계약을 검사한다.
    OpenAITranslationConfig('configuration-check-only', translation, tr['timeout_seconds'], tr['max_output_tokens'])
    spec, limits = EmbeddingSpec(**raw['embedding_spec']), ProcessingLimits(**raw['limits'])
    validate_processing_configuration(limits, translation, spec)
    require(spec.normalization == 'l2' and spec.model_id == spec.tokenizer_id
            and spec.model_revision == spec.tokenizer_revision == Path(raw['model_dir']).name)
    r2 = raw['r2']
    require(type(r2) is dict and set(r2) == {'endpoint', 'bucket', 'region'})
    require(type(r2['endpoint']) is str and re.fullmatch(r'https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com', r2['endpoint']))
    require(type(r2['bucket']) is str and re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', r2['bucket']))
    require(r2['region'] == 'auto')
    ApprovedPayloadBounds(translation.model, tr['max_output_tokens'], raw['requests'])
    return Plan(raw, spec, limits, translation)


def preflight(plan):
    raw = plan.raw
    lock = read_json(raw['model_lock_path'])
    require(lock.get('revision') == plan.spec.model_revision and lock.get('model_id') == plan.spec.model_id
            and lock.get('artifact_sha256') == plan.spec.artifact_sha256)
    artifact_check(Path(raw['model_dir']), lock)
    runtime = read_json(raw['runtime_lock_path'])
    runtime_check(runtime, raw['runtime_image'])
    versions = runtime['packages']
    actual = f"torch={versions['torch']};sentence-transformers={versions['sentence-transformers']};transformers={versions['transformers']}"
    require(plan.spec.runtime_version == actual)


def _pool(dsn, schema):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    return ConnectionPool(dsn, open=False, min_size=1, max_size=4, timeout=10,
        kwargs={'row_factory': dict_row, 'options': f'-c search_path={schema}', 'connect_timeout': 5})


def _client(r2, key, secret):
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=r2['endpoint'], region_name=r2['region'],
        aws_access_key_id=key, aws_secret_access_key=secret,
        config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=30,
                      retries={'total_max_attempts': 1}))


@dataclass(frozen=True, repr=False)
class Resources:
    pool: object
    objects: object
    stage_options: dict


@contextmanager
def build(config, plan, source, *, pool_factory=_pool, client_factory=_client, check=preflight):
    # 외부 자원은 설정·파일 검사·자격 존재 검사를 모두 마친 뒤에만 연다.
    require(plan.raw['scope_sha256'] == scope_digest(config))
    require(source.get('SLP_VERIFY_ALLOW_EXTERNAL_API') == 'true'
            and source.get('SLP_VERIFY_LOCAL_ONLY') == 'false'
            and source.get('WORKER_BASE_REFERENCE') == plan.raw['runtime_image'])
    check(plan)
    names = ('SLP_VERIFY_DATABASE_URL', 'SLP_VERIFY_R2_ACCESS_KEY_ID',
             'SLP_VERIFY_R2_SECRET_ACCESS_KEY', 'SLP_VERIFY_OPENAI_API_KEY')
    values = [source.get(k) for k in names]
    require(all(type(v) is str and bool(v.strip()) for v in values))
    dsn, key, secret, api_key = values
    url = urlsplit(dsn)
    require(url.scheme in ('postgres', 'postgresql') and url.hostname and url.username and url.password
            and unquote(url.path.lstrip('/')) == config.database_name and not url.fragment)
    query = parse_qsl(url.query, keep_blank_values=True)
    require(len(query) <= 1 and all(k == 'sslmode' for k, _ in query))
    sslmode = dict(query).get('sslmode')
    require(sslmode == 'verify-full' or url.hostname in ('localhost', '127.0.0.1', '::1')
            and sslmode in (None, 'disable'))
    raw, tr = plan.raw, plan.raw['translation']
    service = OpenAITranslationConfig(api_key, plan.translation, tr['timeout_seconds'], tr['max_output_tokens'], True, False)
    bounds = ApprovedPayloadBounds(plan.translation.model, tr['max_output_tokens'], raw['requests'])
    retry = TranslationRetryPolicy(max_attempts=1, max_batch_retries=0,
        chunk_retry_window_seconds=tr['timeout_seconds'] + 1, batch_retry_window_seconds=tr['timeout_seconds'] + 1)
    options = dict(service_config=service, model_dir=Path(raw['model_dir']), embedding_spec=plan.spec,
        limits=plan.limits, retry_policy=retry, policy_version=raw['policy_version'], window_key=raw['window_key'],
        token_upper_bound=bounds, batch_size=raw['batch_size'])
    with ExitStack() as stack:
        pool = pool_factory(dsn, config.schema_name)
        stack.callback(pool.close)
        pool.open(wait=True, timeout=10)
        with IsolatedPool(pool, config).connection(): pass
        client = client_factory(raw['r2'], key, secret)
        stack.callback(client.close)
        yield Resources(pool, S3ObjectStorage(client, raw['r2']['bucket']), options)


@contextmanager
def factory(config):
    """기존 검증 CLI의 --runtime에서 명시적으로 연결을 시작하는 포트."""
    try:
        path = os.environ.get('SLP_VERIFY_RUNTIME_CONFIG', '')
        plan = load_plan(config, path)
        with build(config, plan, os.environ) as resources:
            yield resources
    except Exception:
        raise RuntimeError('verification_bootstrap_failed') from None


def main(argv=None):
    """자격·DB·R2를 읽지 않는 bootstrap 사전 검사 명령."""
    from argparse import ArgumentParser
    from solo_leveling.interfaces.cli.processing_verification import load_config
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--runtime-config', required=True)
    parser.add_argument('--check-files', action='store_true', help='모델 파일과 Linux 설치 metadata도 오프라인 검사')
    args = parser.parse_args(argv)
    try:
        plan = load_plan(load_config(args.config), args.runtime_config)
        if args.check_files: preflight(plan)
    except Exception:
        print(json.dumps({'status': 'bootstrap_invalid'}))
        return 1
    print(json.dumps({'status': 'bootstrap_files_verified' if args.check_files else 'bootstrap_config_valid'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
