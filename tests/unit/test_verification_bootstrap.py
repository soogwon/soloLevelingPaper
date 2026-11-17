"""외부 연결 없이 bootstrap 설정·경계·자원 정리·전송 상한을 검증한다."""
from contextlib import contextmanager
from dataclasses import asdict
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from solo_leveling.workers import verification_bootstrap as bootstrap
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from tests.unit.test_processing_verification import config
from tests.fixtures.processing_result import SPEC


def payload(): return dict(model='synthetic-model', max_output_tokens=50, input='합성 원문', store=False)


def raw_config():
    return dict(schema_version=1, scope_sha256=bootstrap.scope_digest(config()),
        runtime_image='synthetic@sha256:' + 'a' * 64, runtime_lock_path='/synthetic/runtime-lock.json',
        model_lock_path='/synthetic/model-lock.json', model_dir='/synthetic/revision', embedding_spec=asdict(SPEC),
        limits=dict(max_pdf_bytes=100, max_pages=2, max_chunks=2),
        translation=dict(model='synthetic-model', timeout_seconds=5, max_output_tokens=50),
        batch_size=1, policy_version='approved-policy', window_key='approved-window',
        r2=dict(endpoint='https://' + 'a' * 32 + '.r2.cloudflarestorage.com', bucket='verification-only', region='auto'),
        requests=dict(max_requests=2, max_input_tokens=200, max_output_tokens=100,
                      payloads={payload_digest(payload()): 100}))


def plan(tmp_path, raw=None):
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(raw or raw_config()))
    return bootstrap.load_plan(config(), path)


def secrets():
    return dict(SLP_VERIFY_DATABASE_URL='postgresql://synthetic:secret@localhost/verification',
        SLP_VERIFY_R2_ACCESS_KEY_ID='synthetic-id', SLP_VERIFY_R2_SECRET_ACCESS_KEY='synthetic-secret',
        SLP_VERIFY_OPENAI_API_KEY='synthetic-api')


def test_approved_payload_bounds_reject_mutation_and_enforce_totals():
    bounds = bootstrap.ApprovedPayloadBounds('synthetic-model', 50, raw_config()['requests'])
    with pytest.raises(ValueError): bounds('other-model', payload())
    with pytest.raises(ValueError): bounds('synthetic-model', {**payload(), 'input': '다른 원문'})
    assert bounds('synthetic-model', payload()) == 100
    assert bounds('synthetic-model', payload()) == 100
    with pytest.raises(ValueError): bounds('synthetic-model', payload())


@pytest.mark.parametrize('kind', ['requests', 'input', 'output'])
def test_each_independent_cap_blocks_next_reservation(kind):
    settings = raw_config()['requests']
    settings[{'requests': 'max_requests', 'input': 'max_input_tokens', 'output': 'max_output_tokens'}[kind]] = {'requests': 1, 'input': 100, 'output': 50}[kind]
    bound = bootstrap.ApprovedPayloadBounds('synthetic-model', 50, settings)
    bound('synthetic-model', payload())
    with pytest.raises(ValueError): bound('synthetic-model', payload())


@pytest.mark.parametrize('change', ['scope', 'extra', 'endpoint', 'path', 'batch', 'model', 'limit', 'cap'])
def test_invalid_plan_never_opens_external_resources(tmp_path, change):
    raw = raw_config()
    if change == 'scope': raw['scope_sha256'] = 'f' * 64
    elif change == 'extra': raw['api_key'] = 'forbidden'
    elif change == 'endpoint': raw['r2']['endpoint'] = 'https://unapproved.example'
    elif change == 'path': raw['model_dir'] = 'relative/revision'
    elif change == 'batch': raw['batch_size'] = True
    elif change == 'model': raw['embedding_spec']['normalization'] = 'none'
    elif change == 'limit': raw['limits']['max_pages'] = 0
    else: raw['requests']['max_requests'] = 0
    with pytest.raises((ValueError, TypeError, RuntimeError)): plan(tmp_path, raw)


class Pool:
    def __init__(self, fail=None): self.fail, self.closed, self.opened = fail, False, False
    def open(self, **kwargs):
        assert kwargs == dict(wait=True, timeout=10)
        self.opened = True
        if self.fail == 'open': raise RuntimeError('비공개 DSN')
    def close(self):
        self.closed = True
        if self.fail == 'close': raise RuntimeError('비공개 종료')
    @contextmanager
    def connection(self):
        c = config()
        row = dict(db='other' if self.fail == 'scope' else c.database_name, schema=c.schema_name,
                   schemas=[c.schema_name], resolved_schemas=['pg_catalog', c.schema_name])
        yield NS(execute=lambda *_: NS(fetchone=lambda: row))


@pytest.mark.parametrize('failure', ['none', 'open', 'scope', 'client', 'body', 'client-close', 'close'])
def test_resources_close_on_setup_run_and_cleanup_failure(tmp_path, failure):
    pool = Pool(failure)
    client = Mock()
    if failure == 'client-close': client.close.side_effect = RuntimeError('비공개 R2')
    def create(*args):
        if failure == 'client': raise RuntimeError('비공개 자격')
        return client
    def run():
        with bootstrap.build(config(), plan(tmp_path), secrets(), pool_factory=lambda *args: pool,
                             client_factory=create, check=lambda _: None) as resources:
            assert resources.pool is pool
            assert resources.stage_options['retry_policy'].max_attempts == 1
            assert resources.stage_options['service_config'].allow_external_api
            if failure == 'body': raise RuntimeError('비공개 원문')
    if failure == 'none': run()
    else:
        with pytest.raises(RuntimeError): run()
    assert pool.closed
    if failure not in ('open', 'scope', 'client'): client.close.assert_called_once()


@pytest.mark.parametrize('change', ['missing', 'ambient-only', 'wrong-db', 'query-host', 'no-tls', 'preflight'])
def test_failures_before_network_do_not_create_clients(tmp_path, change):
    values = secrets()
    if change == 'missing': values.pop('SLP_VERIFY_OPENAI_API_KEY')
    elif change == 'ambient-only': values = dict(DATABASE_URL=values['SLP_VERIFY_DATABASE_URL'], OPENAI_API_KEY='synthetic')
    elif change == 'wrong-db': values['SLP_VERIFY_DATABASE_URL'] += '-wrong'
    elif change == 'query-host': values['SLP_VERIFY_DATABASE_URL'] += '?host=other'
    elif change == 'no-tls': values['SLP_VERIFY_DATABASE_URL'] = 'postgresql://u:p@db.example/verification'
    pool, client, check = Mock(), Mock(), Mock()
    if change == 'preflight': check.side_effect = ValueError('invalid artifact')
    with pytest.raises(ValueError):
        with bootstrap.build(config(), plan(tmp_path), values, pool_factory=pool, client_factory=client, check=check): pass
    pool.assert_not_called()
    client.assert_not_called()


def test_factory_hides_raw_errors_and_uses_dedicated_runtime_path(monkeypatch):
    monkeypatch.setenv('SLP_VERIFY_RUNTIME_CONFIG', '/missing-synthetic-file')
    with pytest.raises(RuntimeError, match='^verification_bootstrap_failed$') as error:
        with bootstrap.factory(config()): pass
    assert error.value.__cause__ is None


def test_real_artifact_preflight_rejects_change_before_connection(tmp_path, monkeypatch):
    from tests.unit.test_worker_image import synthetic_bundle
    folder, lock = synthetic_bundle(tmp_path)
    raw = raw_config()
    lock['model_id'] = raw['embedding_spec']['model_id']
    versions = dict(torch='synthetic-torch', **{'sentence-transformers': 'synthetic-st', 'transformers': 'synthetic-tf'})
    raw['embedding_spec'].update(model_revision=folder.name, tokenizer_revision=folder.name,
        artifact_sha256=lock['artifact_sha256'], runtime_version=';'.join(f'{k}={v}' for k, v in versions.items()))
    raw['model_dir'] = str(folder)
    for key, value in [('model_lock_path', lock), ('runtime_lock_path', {'packages': versions})]:
        target = tmp_path / (key + '.json')
        target.write_text(json.dumps(value))
        raw[key] = str(target)
    runtime = Mock()
    monkeypatch.setattr(bootstrap, 'runtime_check', runtime)
    value = plan(tmp_path, raw)
    bootstrap.preflight(value)
    runtime.assert_called_once()
    (folder / 'model.safetensors').write_bytes(b'changed')
    pool = Mock()
    with pytest.raises(ValueError):
        with bootstrap.build(config(), value, secrets(), pool_factory=pool): pass
    pool.assert_not_called()


def test_default_pool_starts_closed_with_fixed_schema_and_dict_rows(monkeypatch):
    import sys
    result = Mock()
    constructor = Mock(return_value=result)
    marker = object()
    monkeypatch.setitem(sys.modules, 'psycopg.rows', NS(dict_row=marker))
    monkeypatch.setitem(sys.modules, 'psycopg_pool', NS(ConnectionPool=constructor))
    assert bootstrap._pool('synthetic-dsn', 'verification_test') is result
    assert constructor.call_args.kwargs == dict(open=False, min_size=1, max_size=4, timeout=10,
        kwargs=dict(row_factory=marker, options='-c search_path=verification_test', connect_timeout=5))


def test_default_r2_disables_hidden_retries_and_uses_explicit_credentials(monkeypatch):
    import sys
    client, settings = Mock(), Mock(return_value='bounded-config')
    monkeypatch.setitem(sys.modules, 'boto3', NS(client=client))
    monkeypatch.setitem(sys.modules, 'botocore.config', NS(Config=settings))
    bootstrap._client(raw_config()['r2'], 'explicit-id', 'explicit-secret')
    settings.assert_called_once_with(signature_version='s3v4', connect_timeout=5, read_timeout=30,
                                     retries={'total_max_attempts': 1})
    assert client.call_args.kwargs['aws_access_key_id'] == 'explicit-id'
    assert client.call_args.kwargs['aws_secret_access_key'] == 'explicit-secret'


def test_offline_command_does_not_read_secrets_or_connect(tmp_path, monkeypatch, capsys):
    verification = tmp_path / 'verification.json'
    verification.write_text(json.dumps(asdict(config())))
    runtime = tmp_path / 'runtime.json'
    runtime.write_text(json.dumps(raw_config()))
    factory = Mock(side_effect=AssertionError('외부 연결 금지'))
    monkeypatch.setattr(bootstrap, 'factory', factory)
    assert bootstrap.main(['--config', str(verification), '--runtime-config', str(runtime)]) == 0
    factory.assert_not_called()
    assert json.loads(capsys.readouterr().out) == dict(status='bootstrap_config_valid')
