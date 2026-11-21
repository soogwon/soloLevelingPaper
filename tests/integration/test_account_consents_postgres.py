"""실제 PostgreSQL에서 계정 단위 동의의 항목 분리·재동의·철회와 확인 함수를 검증한다."""
# ruff: noqa: F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, new_id  # noqa: F401
from solo_leveling.interfaces.http.account_consents import (
    NOTICE_VERSION,
    PostgresAccountConsents,
    account_consent_current,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요")


def owner(db):
    value = new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 사용자')", (value,))
    return value


def states(view):
    return {item['purpose']: item['state'] for item in view['items']}


def current(pool, user, purpose):
    with pool.connection() as conn:
        return account_consent_current(conn, user, purpose)


def test_required_and_optional_items_are_recorded_separately(pool, db):
    user, store = owner(db), PostgresAccountConsents(pool)
    first = store.view(user)
    assert first['complete'] is False and states(first) == {'storage': 'missing', 'ai_provider': 'missing'}
    assert [item['required'] for item in first['items']] == [True, False]

    view = store.grant(user, ['storage'], NOTICE_VERSION)
    assert view['complete'] is True and states(view) == {'storage': 'current', 'ai_provider': 'missing'}
    assert current(pool, user, 'storage') and not current(pool, user, 'ai_provider')

    again = store.grant(user, ['storage', 'ai_provider', 'storage'], NOTICE_VERSION)
    assert states(again) == {'storage': 'current', 'ai_provider': 'current'}
    assert [item['revision'] for item in again['items']] == [1, 1]
    assert current(pool, user, 'ai_provider')


def test_revoke_and_regrant_create_new_revision_and_other_owner_is_isolated(pool, db):
    user, other, store = owner(db), owner(db), PostgresAccountConsents(pool)
    store.grant(user, ['storage', 'ai_provider'], NOTICE_VERSION)
    revoked = store.revoke(user, 'ai_provider')
    assert states(revoked)['ai_provider'] == 'revoked' and revoked['complete'] is True
    assert not current(pool, user, 'ai_provider')
    assert store.revoke(user, 'ai_provider') == revoked
    regranted = store.grant(user, ['ai_provider'], NOTICE_VERSION)
    assert regranted['items'][1]['revision'] == 2 and current(pool, user, 'ai_provider')
    assert not current(pool, other, 'storage') and states(store.view(other))['storage'] == 'missing'

    store.revoke(user, 'storage')
    assert store.view(user)['complete'] is False and not current(pool, user, 'storage')


def test_old_notice_is_stale_and_not_current(pool, db):
    user, store = owner(db), PostgresAccountConsents(pool)
    db('''INSERT INTO server_account_consents(owner_user_id,purpose,revision,consent_id,notice_version)
          VALUES (%s,'storage',1,%s,'account-consent-v0')''', (user, new_id()))
    assert states(store.view(user))['storage'] == 'stale' and not current(pool, user, 'storage')
    view = store.grant(user, ['storage'], NOTICE_VERSION)
    assert states(view)['storage'] == 'current' and view['items'][0]['revision'] == 2


def test_invalid_input_is_rejected(pool, db):
    user, store = owner(db), PostgresAccountConsents(pool)
    with pytest.raises(ValueError, match='account_consent_notice_changed'):
        store.grant(user, ['storage'], 'account-consent-v0')
    with pytest.raises(ValueError, match='account_consent_purpose_invalid'):
        store.grant(user, ['marketing'], NOTICE_VERSION)
    with pytest.raises(ValueError, match='account_consent_purpose_invalid'):
        store.revoke(user, 'marketing')


def test_restricted_role_checks_current_consent_only_through_function(pool, db):
    import psycopg
    from psycopg.rows import dict_row

    user, store = owner(db), PostgresAccountConsents(pool)
    store.grant(user, ['storage'], NOTICE_VERSION)
    role = 'acct_check_' + new_id().replace('-', '')[:12]
    with pool.connection() as conn:
        schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
        conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
    try:
        with psycopg.connect(os.environ['SOLO_TEST_POSTGRES_DSN'], row_factory=dict_row) as conn:
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute(f'SET ROLE "{role}"')
            assert account_consent_current(conn, user, 'storage') is True
            assert account_consent_current(conn, user, 'ai_provider') is False
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('SELECT * FROM server_account_consents')
    finally:
        with pool.connection() as conn:
            conn.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM "{role}"')
            conn.execute(f'DROP ROLE "{role}"')
