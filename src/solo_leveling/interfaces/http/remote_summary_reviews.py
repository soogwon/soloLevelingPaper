"""외부 전송 없이 요약 범위·상한을 준비하고 별도 목적 동의 후 접수한다."""
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import json

from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.summary.remote_contract import NOTICE_VERSION, OPERATION, RemoteSummaryInput
from .remote_executions import PostgresRemoteExecutionStore, RemoteExecutionConflict
from .remote_summary_sources import PostgresSummaryPreparation
from .remote_summary_intake import PostgresSummaryIntake


class PostgresSummaryReviews:
    def __init__(self, pool, *, settings, policy, window_key, review_seconds, web_origin):
        url = urlsplit(web_origin)
        if (type(policy) is not RemoteExecutionPolicy or policy.max_calls != 1
                or type(review_seconds) is not int or not 60 <= review_seconds <= 86400
                or not isinstance(window_key, str) or not window_key.strip()
                or len(window_key) > 200 or url.scheme != 'https' or not url.hostname
                or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('summary_review_config_invalid')
        PostgresSummaryIntake(pool, settings=settings)
        if settings['budget_policy_version'] != policy.version:
            raise ValueError('summary_review_policy_invalid')
        self.pool, self.settings, self.policy = pool, dict(settings), policy
        self.window, self.seconds, self.origin = window_key, review_seconds, web_origin.rstrip('/')

    def prepare(self, owner, key, context, *, scope='paper', section_id=None):
        key, context = str(UUID(key)), str(UUID(context))
        with self.pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'{owner}\x1fsummary-review\x1f{key}',))
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            request = PostgresSummaryPreparation(scoped, **self.settings).prepare(
                owner, context, scope=scope, section_id=section_id)
            if request is None:
                return None
            old = conn.execute('SELECT input_hash FROM server_summary_reviews WHERE owner_user_id=%s AND request_key=%s', (owner, key)).fetchone()
            if old is not None:
                if old['input_hash'] != request.input_hash:
                    raise RemoteExecutionConflict('summary_review_conflict')
            else:
                conn.execute('''INSERT INTO server_summary_reviews
                    (owner_user_id,request_key,context_id,input_hash,input_snapshot,policy_snapshot,
                     window_key,notice_version,expires_at)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,clock_timestamp()+make_interval(secs=>%s))''',
                    (owner,key,context,request.input_hash,request.encoded,json.dumps(self.policy.snapshot()),
                     self.window,NOTICE_VERSION,self.seconds))
        return dict(request_key=key, review_url=f'{self.origin}/#/summaries/{key}', status='review_required')

    def _row(self, conn, owner, key):
        return conn.execute('''SELECT r.*,clock_timestamp()<r.expires_at AS active
            FROM server_summary_reviews r JOIN server_execution_contexts c
              ON c.owner_user_id=r.owner_user_id AND c.context_id=r.context_id
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE r.owner_user_id=%s AND r.request_key=%s AND p.deleted_at IS NULL
            FOR UPDATE OF r''', (owner,str(UUID(key)))).fetchone()

    def _active(self, row):
        if (not row['active'] or row['notice_version'] != NOTICE_VERSION
                or row['policy_snapshot'] != self.policy.snapshot() or row['window_key'] != self.window
                or any(row['input_snapshot'][k] != v for k,v in self.settings.items())):
            raise ValueError('summary_review_expired_or_changed')

    def view(self, owner, key):
        with self.pool.connection() as conn:
            row = self._row(conn,owner,key)
            if row is None:
                return None
            notice = RemoteSummaryInput(json.dumps(row['input_snapshot'])).transfer_notice()
            consents = conn.execute('''SELECT consent_id,revoked_at FROM server_remote_consents
                WHERE owner_user_id=%s AND consent_id IN (%s,%s)''',
                (owner,row['storage_consent_id'],row['ai_consent_id'])).fetchall()
            states = {r['consent_id']: 'revoked' if r['revoked_at'] else 'current' for r in consents}
            try:
                self._active(row)
                active = True
            except ValueError:
                active = False
            execution = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn))).lookup(owner, OPERATION, str(row['request_key']))
            return dict(**notice,request_key=str(row['request_key']),policy=row['policy_snapshot'],
                        execution_id=execution.execution_id if execution else None,
                        state=execution.state if execution else None,
                        active=active,expires_at=row['expires_at'].isoformat(),
                        storage=states.get(row['storage_consent_id'],'missing'),
                        ai_provider=states.get(row['ai_consent_id'],'missing'))

    def grant(self, owner, key, purpose, notice):
        if purpose not in ('storage','ai_provider') or notice != NOTICE_VERSION:
            raise ValueError('summary_notice_changed')
        column = 'storage_consent_id' if purpose == 'storage' else 'ai_consent_id'
        with self.pool.connection() as conn:
            row = self._row(conn,owner,key)
            if row is None:
                return None
            self._active(row)
            if row[column] is None:
                cid = str(uuid4())
                conn.execute('''INSERT INTO server_remote_consents
                    (consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (%s,%s,%s,%s,%s)''',
                    (cid,owner,purpose,row['input_hash'],NOTICE_VERSION))
                conn.execute(f'UPDATE server_summary_reviews SET {column}=%s WHERE owner_user_id=%s AND request_key=%s', (cid,owner,key))
        return self.view(owner,key)

    def revoke(self, owner, key, purpose):
        if purpose not in ('storage','ai_provider'):
            raise ValueError('summary_purpose_invalid')
        with self.pool.connection() as conn:
            row = self._row(conn,owner,key)
            if row is None:
                return None
            cid = row['storage_consent_id' if purpose == 'storage' else 'ai_consent_id']
            conn.execute('''UPDATE server_remote_consents SET revoked_at=COALESCE(revoked_at,clock_timestamp())
                WHERE owner_user_id=%s AND consent_id=%s''', (owner,cid))
        return self.view(owner,key)

    def submit(self, owner, key):
        key = str(UUID(key))
        with self.pool.connection() as conn:
            row = self._row(conn,owner,key)
            if row is None:
                return None
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            old = PostgresRemoteExecutionStore(scoped).lookup(owner,OPERATION,key)
            if old is not None:
                return dict(execution_id=old.execution_id,state=old.state,request_key=key)
            self._active(row)
            if not row['storage_consent_id'] or not row['ai_consent_id']:
                raise ValueError('summary_consent_required')
            saved = PostgresSummaryIntake(scoped,settings=self.settings).submit(owner,key,
                RemoteSummaryInput(json.dumps(row['input_snapshot'])),row['storage_consent_id'],
                row['ai_consent_id'],policy=self.policy,window_key=self.window)
            if saved is None:
                raise ValueError('summary_admission_denied')
            return dict(execution_id=saved.execution_id,state=saved.state,request_key=key)

    def result(self, owner, key):
        from .remote_summary_results import PostgresSummaryResults
        with self.pool.connection() as conn:
            row = self._row(conn, owner, key)
            if row is None:
                return None
            context = str(row['context_id'])
        return PostgresSummaryResults(self.pool).get(owner, context, key)

    def evidence(self, owner, key, evidence_id):
        from .remote_summary_results import PostgresSummaryResults
        with self.pool.connection() as conn:
            row = self._row(conn, owner, key)
            if row is None:
                return None
            context = str(row['context_id'])
        return PostgresSummaryResults(self.pool).evidence(owner, context, key, evidence_id)

    def latest(self, owner, context):
        from .remote_summary_results import PostgresSummaryResults
        return PostgresSummaryResults(self.pool).latest(owner, context)

    def cancel(self, owner, key):
        with self.pool.connection() as conn:
            row = self._row(conn, owner, key)
            if row is None:
                return None
            store = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn)))
            execution = store.lookup(owner, OPERATION, str(row['request_key']))
            if execution is None:
                return dict(request_key=str(row['request_key']), state='not_submitted')
            state = store.request_cancel(owner, execution.execution_id)
            return dict(request_key=str(row['request_key']), execution_id=execution.execution_id,
                        state=state or execution.state,
                        message='미전송 예산은 반환하며 이미 전송한 호출의 비용은 취소되지 않습니다.')
