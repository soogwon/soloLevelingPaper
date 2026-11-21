from types import SimpleNamespace

from fastapi.testclient import TestClient

from solo_leveling.interfaces.http.account_consents import NOTICE_VERSION
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal

AUTH = {'Authorization': 'Bearer test'}


def status(complete=True):
    return dict(notice_version=NOTICE_VERSION, complete=complete, items=[
        dict(purpose='storage', required=True, notice='보관', state='current' if complete else 'missing',
             revision=1 if complete else 0, granted_at=None, revoked_at=None),
        dict(purpose='ai_provider', required=False, notice='전송', state='missing', revision=0,
             granted_at=None, revoked_at=None)])


class Consents:
    def __init__(self):
        self.calls, self.error = [], None

    def _run(self, name, *args):
        self.calls.append((name, *args))
        if self.error:
            raise self.error
        return status()

    def view(self, *args):
        return self._run('view', *args)

    def grant(self, *args):
        return self._run('grant', *args)

    def revoke(self, *args):
        return self._run('revoke', *args)


def app_for(credential='web_session'):
    principal = AuthenticatedPrincipal('owner', credential, 'device' if credential == 'device' else None)
    consents = Consents()
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: principal),
                     device_store=SimpleNamespace(),
                     device_registry=SimpleNamespace(is_active=lambda *_: True),
                     account_consents=consents)
    return app, consents


def test_web_session_reads_grants_and_revokes_account_consents():
    app, consents = app_for()
    with TestClient(app) as client:
        read = client.get('/v1/account/consents', headers=AUTH)
        granted = client.post('/v1/account/consents', headers=AUTH,
                              json={'notice_version': NOTICE_VERSION, 'purposes': ['storage', 'ai_provider']})
        revoked = client.post('/v1/account/consents/ai_provider/revoke', headers=AUTH)
    assert [r.status_code for r in (read, granted, revoked)] == [200, 200, 200]
    assert read.headers['cache-control'] == 'no-store' and read.json()['complete'] is True
    assert consents.calls == [('view', 'owner'), ('grant', 'owner', ['storage', 'ai_provider'], NOTICE_VERSION),
                              ('revoke', 'owner', 'ai_provider')]


def test_device_tokens_and_unknown_purposes_are_rejected():
    app, consents = app_for('device')
    with TestClient(app) as client:
        assert client.get('/v1/account/consents', headers=AUTH).status_code == 403
    app, consents = app_for()
    with TestClient(app) as client:
        bad = client.post('/v1/account/consents', headers=AUTH,
                          json={'notice_version': NOTICE_VERSION, 'purposes': ['marketing']})
        extra = client.post('/v1/account/consents', headers=AUTH,
                            json={'notice_version': NOTICE_VERSION, 'purposes': ['storage'], 'all': True})
    assert bad.status_code == 422 and extra.status_code == 422 and consents.calls == []


def test_store_errors_map_to_stable_codes():
    app, consents = app_for()
    with TestClient(app) as client:
        consents.error = ValueError('account_consent_notice_changed')
        changed = client.post('/v1/account/consents', headers=AUTH,
                              json={'notice_version': 'old', 'purposes': ['storage']})
        consents.error = RuntimeError('db down')
        down = client.get('/v1/account/consents', headers=AUTH)
    assert changed.status_code == 409 and changed.json()['detail']['code'] == 'account_consent_notice_changed'
    assert down.status_code == 503 and down.json()['detail']['code'] == 'account_consent_unavailable'
