"""동의 웹과 MCP 계정 분리 및 풀 수명은 실제 연결 없이 검증한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.remote_reflection_settings import load_reflection_settings
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.devices import PostgresDeviceStore
from solo_leveling.interfaces.http import remote_mcp


def environment():
    return {'REMOTE_REFLECTIONS_ENABLED': 'true',
            'REMOTE_REFLECTION_WEB_DATABASE_URL': 'postgresql://web:synthetic@localhost/db',
            'REMOTE_REFLECTION_MCP_DATABASE_URL': 'postgresql://mcp:synthetic@localhost/db'}


def test_disabled_does_not_read_credentials():
    assert load_reflection_settings({}) is None
    assert load_reflection_settings({'REMOTE_REFLECTION_WEB_DATABASE_URL': 'invalid'}) is None


@pytest.mark.parametrize('key,value', [
    ('REMOTE_REFLECTIONS_ENABLED', 'yes'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'postgresql://mcp:synthetic@localhost/db'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'postgresql://%6dcp:synthetic@localhost/db'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'postgresql://web:synthetic@other/db'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'postgresql://web:synthetic@localhost/other'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'postgresql://web:synthetic@localhost/db?options=other'),
    ('REMOTE_REFLECTION_WEB_DATABASE_URL', 'secret-invalid'),
])
def test_invalid_configuration_has_fixed_error(key, value):
    env = environment(); env[key] = value
    with pytest.raises(ValueError, match='^reflection_settings_invalid$'):
        load_reflection_settings(env)


def test_separate_roles_hidden_in_repr():
    settings = load_reflection_settings(environment())
    assert 'synthetic' not in repr(settings)
    assert settings.web_dsn != settings.mcp_dsn


def test_disabled_mcp_rejects_reflection_enablement(monkeypatch):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match='^reflection_settings_invalid$'):
        create_app(device_store=NS())


def test_app_uses_separate_pools_and_closes_both(monkeypatch):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    stores = [NS(connection_pool=object(), open=Mock(), close=Mock()) for _ in range(2)]
    factory = Mock(side_effect=stores)
    monkeypatch.setattr(PostgresDeviceStore, 'from_dsn', factory)
    assemble = Mock(return_value=None)
    monkeypatch.setattr(remote_mcp, 'create_remote_mcp', assemble)
    app = create_app(device_store=NS(), remote_mcp_settings=NS(enabled=True, product_reads_enabled=True),
                     remote_product_queries=NS(pool=NS()))
    service = assemble.call_args.kwargs['reflection_service']
    assert service.pool is stores[1].connection_pool
    assert factory.call_args_list[0].args[0] == environment()['REMOTE_REFLECTION_WEB_DATABASE_URL']
    assert factory.call_args_list[1].args[0] == environment()['REMOTE_REFLECTION_MCP_DATABASE_URL']
    for store in stores:
        store.open.assert_not_called()
    with TestClient(app):
        for store in stores:
            store.open.assert_called_once()
    for store in stores:
        store.close.assert_called_once()


def test_second_pool_open_failure_closes_first(monkeypatch):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    first = NS(connection_pool=object(), open=Mock(), close=Mock())
    second = NS(connection_pool=object(), open=Mock(side_effect=RuntimeError('synthetic')), close=Mock())
    monkeypatch.setattr(PostgresDeviceStore, 'from_dsn', Mock(side_effect=[first, second]))
    monkeypatch.setattr(remote_mcp, 'create_remote_mcp', Mock(return_value=None))
    app = create_app(device_store=NS(), remote_mcp_settings=NS(enabled=True, product_reads_enabled=True),
                     remote_product_queries=NS(pool=NS()))
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    first.close.assert_called_once()
    second.close.assert_called_once()


def test_web_consent_uses_web_pool_not_mcp_pool(monkeypatch):
    from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
    from solo_leveling.interfaces.http.remote_reflection import PostgresRemoteReflection
    for key,value in environment().items():monkeypatch.setenv(key,value)
    stores=[NS(connection_pool=object(),open=Mock(),close=Mock()) for _ in range(2)]
    monkeypatch.setattr(PostgresDeviceStore,'from_dsn',Mock(side_effect=stores))
    assemble=Mock(return_value=None)
    monkeypatch.setattr(remote_mcp,'create_remote_mcp',assemble)
    captured=[]
    def consent(self,owner,context):
        captured.append((self.pool,owner,context));return {'storage':'missing'}
    monkeypatch.setattr(PostgresRemoteReflection,'consent',consent)
    app=create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),
        remote_product_queries=NS(),token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice','web_session',None)))
    with TestClient(app) as client:
        result=client.get('/v1/remote-reflections/00000000-0000-0000-0000-000000000592/consent',headers={'Authorization':'Bearer synthetic'})
    assert result.status_code==200 and captured[0][0] is stores[0].connection_pool
    assert assemble.call_args.kwargs['reflection_service'].pool is stores[1].connection_pool
