"""원격 복구 검토·동의·예산 예약. 실행 포트 없이 별도 원장에만 접수한다."""
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from .remote_recovery_preview import RemoteRecoveryPreview
from .remote_retrieval import PostgresRemoteContextResolver

NOTICE = 'remote-reprocessing-v1'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def validate_contract(config):
    fields = {'policy_version','window_key','provider','model','max_calls','max_input_tokens',
              'max_output_tokens','input_usd_per_million','output_usd_per_million','review_seconds'}
    if type(config) is not dict or set(config) != fields:
        raise ValueError('reprocessing_config_invalid')
    for name in ('policy_version','window_key','provider','model'):
        if not isinstance(config[name],str) or not config[name].strip() or len(config[name])>200:
            raise ValueError('reprocessing_config_invalid')
    for name in ('max_calls','max_input_tokens','max_output_tokens','review_seconds'):
        if type(config[name]) is not int or not 1 <= config[name] <= 1000000:
            raise ValueError('reprocessing_config_invalid')
    try:
        rates = [Decimal(config[k]) if isinstance(config[k],str) else Decimal('NaN')
                 for k in ('input_usd_per_million','output_usd_per_million')]
        if any(not r.is_finite() or not 0 < r <= 1000000 for r in rates):
            raise ValueError('reprocessing_config_invalid')
        cap = (rates[0]*config['max_input_tokens']+rates[1]*config['max_output_tokens'])/1000000
    except (InvalidOperation, TypeError):
        raise ValueError('reprocessing_config_invalid') from None
    return dict(config, max_cost_usd=str(cap), notice_version=NOTICE,
                token_limit_scope='entire_job', execution_enabled=False)


class PostgresReprocessingReviews:
    def __init__(self, pool, *, config):
        self.pool, self.contract = pool, validate_contract(config)

    def _snapshot(self, conn, owner, context, operation):
        scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
        queries = SimpleNamespace(pool=scoped, owner=lambda:owner,
                                  contexts=PostgresRemoteContextResolver(scoped))
        preview = RemoteRecoveryPreview(queries).preview(context, operation)
        if preview['status'] != 'ok':
            raise ValueError('reprocessing_source_unavailable')
        value = preview['result']
        if value['target_count'] == 0:
            raise ValueError('reprocessing_no_targets')
        source = conn.execute('''SELECT u.upload_id,u.sha256_hex,u.file_size,v.active_publication_id
            FROM server_pdf_uploads u JOIN server_processing_versions v
              ON v.owner_user_id=u.owner_user_id AND v.paper_id=u.paper_id AND v.server_version_id=u.server_version_id
            WHERE u.owner_user_id=%s AND u.paper_id=%s AND u.server_version_id=%s
              AND u.status='ready' AND u.source_kind='remote'
            ORDER BY u.upload_id LIMIT 1 FOR SHARE OF u,v''',
            (owner,value['paper_id'],value['version_id'])).fetchone()
        if source is None:
            raise ValueError('reprocessing_pdf_unavailable')
        return dict(preview=value, pdf={k:str(v) if v is not None else None for k,v in source.items()})

    def draft(self, owner, context, operation):
        context = str(UUID(context))
        with self.pool.connection() as conn:
            snapshot = self._snapshot(conn,owner,context,operation)
        return dict(snapshot=snapshot, contract=self.contract, request_key=None, state='draft',
                    active=True, storage='missing', ai_provider='missing', job_id=None)

    def prepare(self, owner, key, context, operation):
        key,context = str(UUID(key)),str(UUID(context))
        with self.pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'{owner}\x1freprocessing\x1f{key}',))
            old = self._row(conn,owner,key)
            if old:
                if str(old['context_id']) != context or old['operation'] != operation:
                    raise ValueError('reprocessing_key_conflict')
            else:
                snapshot=self._snapshot(conn,owner,context,operation)
                digest=sha256(encoded(dict(snapshot=snapshot,contract=self.contract)).encode()).hexdigest()
                conn.execute('''INSERT INTO server_reprocessing_reviews
                    (owner_user_id,request_key,context_id,operation,input_hash,snapshot,contract,expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,clock_timestamp()+make_interval(secs=>%s))''',
                    (owner,key,context,operation,digest,encoded(snapshot),encoded(self.contract),self.contract['review_seconds']))
        return self.view(owner,key)

    def _row(self,conn,owner,key):
        return conn.execute('''SELECT r.*,clock_timestamp()<r.expires_at AS active
            FROM server_reprocessing_reviews r JOIN server_execution_contexts c
              ON c.owner_user_id=r.owner_user_id AND c.context_id=r.context_id
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE r.owner_user_id=%s AND r.request_key=%s AND p.deleted_at IS NULL FOR UPDATE OF r''',
            (owner,str(UUID(key)))).fetchone()

    def _active(self,row):
        if not row['active'] or row['contract'] != self.contract or row['state'] != 'review':
            raise ValueError('reprocessing_review_changed')

    def _states(self,conn,row):
        rows=conn.execute('''SELECT purpose,revoked_at FROM server_remote_consents
            WHERE owner_user_id=%s AND consent_id IN (%s,%s) AND input_hash=%s AND notice_version=%s FOR SHARE''',
            (row['owner_user_id'],row['storage_consent_id'],row['ai_consent_id'],row['input_hash'],NOTICE)).fetchall()
        return {r['purpose']:'revoked' if r['revoked_at'] else 'current' for r in rows}

    def view(self,owner,key):
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            states=self._states(conn,row)
            return dict(snapshot=row['snapshot'],contract=row['contract'],request_key=str(row['request_key']),
                state=row['state'],job_id=str(row['job_id']) if row['job_id'] else None,
                active=row['active'] and row['contract']==self.contract and row['state']=='review',
                expires_at=row['expires_at'].isoformat(),storage=states.get('storage','missing'),
                ai_provider=states.get('ai_provider','missing'),execution_enabled=False)

    def grant(self,owner,key,purpose,notice):
        if purpose not in ('storage','ai_provider') or notice != NOTICE:
            raise ValueError('reprocessing_notice_changed')
        column='storage_consent_id' if purpose=='storage' else 'ai_consent_id'
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            self._active(row)
            if row[column] is None:
                cid=str(uuid4())
                conn.execute('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
                    VALUES (%s,%s,%s,%s,%s)''',(cid,owner,purpose,row['input_hash'],NOTICE))
                conn.execute(f'UPDATE server_reprocessing_reviews SET {column}=%s WHERE owner_user_id=%s AND request_key=%s',(cid,owner,key))
        return self.view(owner,key)

    def _budget(self,conn,owner,contract):
        return conn.execute('''SELECT * FROM server_dispatch_budget_limits WHERE policy_version=%s AND window_key=%s
            AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))
            ORDER BY scope_type,scope_id FOR UPDATE''',
            (contract['policy_version'],contract['window_key'],owner)).fetchall()

    def _adjust(self,conn,owner,contract,sign):
        conn.execute('''UPDATE server_dispatch_budget_limits SET reserved_input_tokens=reserved_input_tokens+%s,
            reserved_output_tokens=reserved_output_tokens+%s WHERE policy_version=%s AND window_key=%s
            AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))''',
            (sign*contract['max_input_tokens'],sign*contract['max_output_tokens'],contract['policy_version'],contract['window_key'],owner))

    def submit(self,owner,key):
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            if row['state']!='review':return self._receipt(row)
            self._active(row)
            # 삭제·새 게시와 같은 부모 논문 잠금을 잡은 뒤 동의와 예산을 검사한다.
            conn.execute('SELECT paper_id FROM server_papers WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL FOR UPDATE',
                         (owner,row['snapshot']['preview']['paper_id']))
            if self._snapshot(conn,owner,str(row['context_id']),row['operation']) != row['snapshot']:
                raise ValueError('reprocessing_revision_conflict')
            if self._states(conn,row) != {'storage':'current','ai_provider':'current'}:
                raise ValueError('reprocessing_consent_required')
            control=conn.execute("SELECT intake_enabled FROM server_dispatch_controls WHERE control_id='provider_dispatch' FOR SHARE").fetchone()
            if control is None or not control['intake_enabled']:raise ValueError('reprocessing_intake_paused')
            limits=self._budget(conn,owner,row['contract'])
            if ({r['scope_type'] for r in limits}!={'owner','service'} or any(
                r['reserved_input_tokens']+r['consumed_input_tokens']+self.contract['max_input_tokens']>r['max_input_tokens']
                or r['reserved_output_tokens']+r['consumed_output_tokens']+self.contract['max_output_tokens']>r['max_output_tokens'] for r in limits)):
                raise ValueError('reprocessing_budget_exceeded')
            self._adjust(conn,owner,self.contract,1)
            row=conn.execute('''UPDATE server_reprocessing_reviews SET job_id=%s,state='queued',budget_reserved=true
                WHERE owner_user_id=%s AND request_key=%s RETURNING *''',(str(uuid4()),owner,key)).fetchone()
            return self._receipt(row)

    def _receipt(self,row):
        return dict(request_key=str(row['request_key']),job_id=str(row['job_id']) if row['job_id'] else None,
                    state=row['state'],execution_enabled=False,message='접수됨·실행 연결 대기' if row['state']=='queued' else '취소됨')

    def cancel(self,owner,key,purpose=None):
        if purpose is not None and purpose not in ('storage','ai_provider'):raise ValueError('invalid_purpose')
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            if row['budget_reserved']:
                if len(self._budget(conn,owner,row['contract']))!=2:raise ValueError('reprocessing_budget_missing')
                self._adjust(conn,owner,row['contract'],-1)
            if purpose:
                cid=row['storage_consent_id' if purpose=='storage' else 'ai_consent_id']
                conn.execute('UPDATE server_remote_consents SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE owner_user_id=%s AND consent_id=%s',(owner,cid))
            row=conn.execute("UPDATE server_reprocessing_reviews SET state='cancelled',budget_reserved=false WHERE owner_user_id=%s AND request_key=%s RETURNING *",(owner,key)).fetchone()
        return self.view(owner,key)
