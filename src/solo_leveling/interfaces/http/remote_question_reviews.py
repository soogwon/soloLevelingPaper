"""인증된 웹에서만 질문 목적별 동의를 기록하고 같은 요청을 접수한다."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID, uuid4
from urllib.parse import urlsplit
import json
from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from .remote_retrieval import PostgresRemoteContextResolver
from .remote_question_intake import PostgresQuestionIntake
from .remote_executions import PostgresRemoteExecutionStore,RemoteExecutionConflict

NOTICE='remote-question-v1'
COUNT_NOTICE='question-count-and-generation-v1'
COUNT_AI_NOTICE='OpenAI에 질문, 선택된 원문·번역 근거, 근거 ID, 답변 지시와 출력 형식을 입력 토큰 계수에 최대 1회, 답변 생성에 최대 1회 전송합니다. 외부 전송은 합계 최대 2회이며 계수만으로 답변을 생성하지 않습니다. 서비스 예산을 사용하며 비용이 발생할 수 있습니다. 전송 결과가 불명확하면 자동 재전송하지 않습니다.'

def validate_notice(notice, policy, config):
    general = str(config.get('provider_profile_id', '')).startswith('general-question-')
    if (notice not in (NOTICE, COUNT_NOTICE) or general != (notice == COUNT_NOTICE)
            or (general and (policy.max_calls != 1 or config.get('semantic_model') != 'disabled'))):
        raise ValueError('question_notice_config_invalid')

def transfer_summary(notice, policy):
    if notice not in (NOTICE, COUNT_NOTICE):
        return None
    count = int(notice == COUNT_NOTICE)
    return dict(input_count_max_calls=count, generation_max_calls=policy['max_calls'],
                total_max_calls=count + policy['max_calls'])

STORAGE_NOTICE='질문·검색 조건·답변과 인용 근거를 현재 계정에 저장합니다. PDF 파일을 새로 업로드하지 않습니다.'
AI_NOTICE='OpenAI에 질문과 선택된 원문·번역 근거를 보내 답변 생성·보완·의미 검증을 수행합니다. 서비스 예산을 사용하며 비용이 발생합니다. 자동 유료 재시도는 하지 않습니다.'


class PostgresQuestionReviews:
    def __init__(self,pool,*,policy,processing_config,window_key,review_seconds,web_origin,notice_version=NOTICE):
        if (not isinstance(policy,RemoteExecutionPolicy) or type(review_seconds) is not int
                or not 60<=review_seconds<=86400 or not isinstance(window_key,str) or not window_key.strip()):
            raise ValueError('question_review_config_invalid')
        url=urlsplit(web_origin)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('question_review_origin_invalid')
        validate_notice(notice_version, policy, processing_config)
        self.notice=notice_version
        self.web_origin=web_origin.rstrip('/')
        self.pool,self.policy,self.config=pool,policy,dict(processing_config)
        self.window,self.seconds=window_key,review_seconds

    def prepare(self,owner,key,context,question,*,pages=(),sections=(),strict=False,top_k=5):
        key=str(UUID(key))
        with self.pool.connection() as conn:
            scoped=SimpleNamespace(connection=lambda:nullcontext(conn))
            binding=PostgresRemoteContextResolver(scoped).resolve(owner,context)
            if binding is None:return None
            request=RemoteQuestionInput.create(binding,question,RemoteSearchFilters(tuple(pages),tuple(sections),strict),top_k,self.config)
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'{owner}\x1freview\x1f{key}',))
            old=conn.execute('SELECT input_hash FROM server_question_reviews WHERE owner_user_id=%s AND request_key=%s',(owner,key)).fetchone()
            if old is not None:
                if old['input_hash']!=request.input_hash:raise RemoteExecutionConflict('question_review_conflict')
            else:
                conn.execute("""INSERT INTO server_question_reviews(owner_user_id,request_key,context_id,input_hash,
                    question_snapshot,policy_snapshot,window_key,notice_version,expires_at)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,clock_timestamp()+make_interval(secs=>%s))""",
                    (owner,key,context,request.input_hash,request.encoded,json.dumps(self.policy.snapshot()),self.window,self.notice,self.seconds))
        return {'request_key':key,'review_url':f'{self.web_origin}/#/questions/{key}','status':'review_required'}

    def _row(self,conn,owner,key):
        return conn.execute("""SELECT r.* FROM server_question_reviews r
            JOIN server_execution_contexts c ON c.owner_user_id=r.owner_user_id AND c.context_id=r.context_id
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE r.owner_user_id=%s AND r.request_key=%s AND p.deleted_at IS NULL FOR UPDATE OF r""",(owner,str(UUID(key)))).fetchone()

    def view(self,owner,key):
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            request=RemoteQuestionInput(json.dumps(row['question_snapshot']))
            binding,question,filters,top_k,config=request.unpack()
            consents=conn.execute('SELECT consent_id,revoked_at FROM server_remote_consents WHERE owner_user_id=%s AND consent_id IN (%s,%s)',(owner,row['storage_consent_id'],row['ai_consent_id'])).fetchall()
            states={r['consent_id']:('revoked' if r['revoked_at'] else 'current') for r in consents}
            active=conn.execute('SELECT clock_timestamp()<%s AS active',(row['expires_at'],)).fetchone()['active']
            execution=PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda:nullcontext(conn))).lookup(owner,'ask_paper',str(row['request_key']))
            return dict(execution_id=execution.execution_id if execution else None,state=execution.state if execution else None,request_key=str(row['request_key']),question=question,paper_id=binding.paper_id,version_id=binding.server_version_id,
                pages=list(filters.pdf_pages),sections=list(filters.section_ids),strict_pages=filters.strict_pages,top_k=top_k,notice_version=row['notice_version'],storage_notice=STORAGE_NOTICE,ai_notice=COUNT_AI_NOTICE if row['notice_version']==COUNT_NOTICE else AI_NOTICE,
                transfer=transfer_summary(row['notice_version'],row['policy_snapshot']),
                storage=states.get(row['storage_consent_id'],'missing'),ai_provider=states.get(row['ai_consent_id'],'missing'),
                policy=row['policy_snapshot'],models=config,active=active and row['notice_version']==self.notice,expires_at=row['expires_at'].isoformat())

    def grant(self,owner,key,purpose,notice):
        if purpose not in ('storage','ai_provider') or notice!=self.notice:raise ValueError('question_notice_changed')
        column='storage_consent_id' if purpose=='storage' else 'ai_consent_id'
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            active=conn.execute('SELECT clock_timestamp()<%s AS active',(row['expires_at'],)).fetchone()['active']
            if not active or row['notice_version']!=self.notice:raise ValueError('question_review_expired')
            if row[column] is None:
                cid=str(uuid4())
                conn.execute('INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (%s,%s,%s,%s,%s)',(cid,owner,purpose,row['input_hash'],self.notice))
                conn.execute(f'UPDATE server_question_reviews SET {column}=%s WHERE owner_user_id=%s AND request_key=%s',(cid,owner,key))
        return self.view(owner,key)

    def revoke(self,owner,key,purpose):
        if purpose not in ('storage','ai_provider'):raise ValueError('question_purpose_invalid')
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            cid=row['storage_consent_id' if purpose=='storage' else 'ai_consent_id']
            conn.execute('UPDATE server_remote_consents SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE owner_user_id=%s AND consent_id=%s',(owner,cid))
        return self.view(owner,key)

    def submit(self,owner,key):
        key=str(UUID(key))
        with self.pool.connection() as conn:
            row=self._row(conn,owner,key)
            if row is None:return None
            scoped=SimpleNamespace(connection=lambda:nullcontext(conn))
            existing=PostgresRemoteExecutionStore(scoped).lookup(owner,'ask_paper',key)
            if existing is not None:return {'execution_id':existing.execution_id,'state':existing.state,'request_key':key}
            active=conn.execute('SELECT clock_timestamp()<%s AS active',(row['expires_at'],)).fetchone()['active']
            if not active or row['notice_version']!=self.notice:raise ValueError('question_review_expired')
            if not row['storage_consent_id'] or not row['ai_consent_id']:raise ValueError('question_consent_required')
            saved=PostgresQuestionIntake(scoped).submit(owner,key,RemoteQuestionInput(json.dumps(row['question_snapshot'])),
                row['storage_consent_id'],row['ai_consent_id'],self.notice,policy=RemoteExecutionPolicy(**row['policy_snapshot']),window_key=row['window_key'])
            if saved is None:raise ValueError('question_admission_denied')
            return {'execution_id':saved.execution_id,'state':saved.state,'request_key':key}
