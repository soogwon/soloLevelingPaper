"""새 동의에만 포함하는 논문 상태 전송. 승인 시점 CAS 기준을 재시도 중 유지한다."""
import json
from datetime import datetime
from uuid import uuid4
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


def payload(conn, paper_id, source_id):
    row = conn.execute('SELECT * FROM paper_reading_states WHERE paper_id=?', (paper_id,)).fetchone()
    if row is None:
        raise KeyError('논문 상태를 찾을 수 없습니다.')
    return dict(source_instance_id=source_id, status=row['status'], source_revision=row['revision'],
                basis_version_id=row['basis_version_id'], action=row['action'])


def queue(conn, account, consent, paper_id, source_id, now):
    from .paper_metadata import _payload_hash
    body = payload(conn, paper_id, source_id)
    previous = conn.execute("SELECT server_revision FROM server_object_mappings WHERE account_id=? "
        "AND source_instance_id=? AND object_type='reading_state' AND local_object_id=?",
        (account, source_id, paper_id)).fetchone()
    operation = str(uuid4())
    body.update(operation_id=operation, expected_revision=previous['server_revision'] if previous else 0)
    conn.execute('''INSERT INTO sync_outbox (operation_id, account_id, consent_scope_id, object_type,
        local_object_id, operation, payload_hash, status, created_at, updated_at)
        VALUES (?,?,?,'reading_state',?,'upsert',?,'pending',?,?)''',
        (operation, account, consent, paper_id, _payload_hash(payload(conn, paper_id, source_id)), now, now))
    conn.execute('INSERT INTO reading_state_sync_requests VALUES (?,?)', (operation, json.dumps(body)))


def sync(service, account, consent, source, server_paper):
    from .paper_metadata import _finish, _iso, _payload_hash, _is_transport_failure
    conn = get_connection(service._db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''SELECT o.*, c.revoked_at, r.payload_json FROM sync_outbox o
            JOIN server_sync_consents c USING(consent_scope_id)
            JOIN reading_state_sync_requests r USING(operation_id)
            WHERE o.account_id=? AND o.consent_scope_id=? AND o.object_type='reading_state'
              AND o.status IN ('pending','retryable_failure','syncing')''', (account, consent)).fetchone()
        if row is None:
            return
        if row['revoked_at'] is not None:
            _finish(conn, row, 'action_required', 'consent_revoked', service._now())
            return
        if _payload_hash(payload(conn, row['local_object_id'], source)) != row['payload_hash']:
            _finish(conn, row, 'conflict', 'payload_changed', service._now())
            return
        body = json.loads(row['payload_json'])
        operation, attempt = row['operation_id'], row['attempt_count']+1
        conn.execute("UPDATE sync_outbox SET status='syncing', attempt_count=?, updated_at=? WHERE operation_id=?",
                     (attempt, _iso(service._now()), operation))
        conn.commit()
    finally:
        conn.close()
    def fail(status, code, retry=False):
        service._record_failure(operation, account, attempt, status, code, retry)
    try:
        response = service._client.request('PUT', f'/v1/mcp/papers/{server_paper}/reading-state',
                                           json=body, expected_connection_id=account)
    except ServerConnectionRequired:
        fail('action_required', 'reauthentication_required')
        return
    except Exception as error:
        if not _is_transport_failure(error):
            raise
        fail('retryable_failure', 'server_unavailable', True)
        return
    try:
        if response.status_code == 409:
            fail('conflict', 'reading_state_conflict')
            return
        if response.status_code >= 500:
            fail('retryable_failure', 'server_unavailable', True)
            return
        if response.status_code != 200:
            fail('action_required', 'invalid_reading_state')
            return
        result = response.json()
        if (result['paper_id'] != server_paper or result['status'] != body['status']
                or result['basis_version_id'] != body['basis_version_id']
                or type(result['source_revision']) is not int
                or result['source_revision'] != body['source_revision']
                or type(result['revision']) is not int or result['revision'] != body['expected_revision']+1
                or datetime.fromisoformat(result['updated_at'].replace('Z','+00:00')).tzinfo is None):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        fail('action_required', 'invalid_server_response')
        return
    finally:
        response.close()
    conn = get_connection(service._db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT status, attempt_count FROM sync_outbox WHERE operation_id=?', (operation,)).fetchone()
        if current['status'] != 'syncing' or current['attempt_count'] != attempt:
            return
        conn.execute('''INSERT INTO server_object_mappings VALUES (?,?,'reading_state',?,?,?,?)
            ON CONFLICT(account_id, source_instance_id, object_type, local_object_id)
            DO UPDATE SET server_object_id=excluded.server_object_id,
                server_revision=excluded.server_revision, synced_at=excluded.synced_at''',
            (account, source, row['local_object_id'], server_paper, result['revision'], _iso(service._now())))
        conn.execute("UPDATE sync_outbox SET status='synced', next_attempt_at=NULL, last_error_code=NULL, updated_at=? "
                     "WHERE operation_id=?", (_iso(service._now()), operation))
        conn.commit()
    finally:
        conn.close()
