"""검수 성찰 템플릿과 사용자 답변만 저장한다. 모델 평가·외부 전송은 없다."""
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace
from uuid import UUID
import hashlib
import json

from solo_leveling.domain.reflection import TEMPLATES, COMPARISON_GROUPS, ANSWER_STATUSES, MAX_ANSWER_LENGTH
from .remote_retrieval import PostgresRemoteContextResolver
from .remote_executions import RemoteExecutionConflict

NOTICE = 'remote-reflection-v1'


def encoded(value):
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def question_set():
    value = dict(questions=[asdict(t) for phase in ('before','after') for t in TEMPLATES[phase]],
                 groups=[asdict(g) for g in COMPARISON_GROUPS])
    return json.loads(encoded(value))


class PostgresRemoteReflection:
    def __init__(self,pool):
        self.pool = pool

    def _context(self,conn,owner,context,*,lock=False):
        context = str(UUID(context))
        parent = conn.execute('''SELECT p.paper_id FROM server_execution_contexts c JOIN server_papers p
            ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL''' +
            (' FOR UPDATE OF p' if lock else ' FOR SHARE OF p'), (owner,context)).fetchone()
        if parent is None:
            return None
        binding = PostgresRemoteContextResolver(SimpleNamespace(connection=lambda:nullcontext(conn))).resolve(owner,context)
        if binding is None:
            return None
        row = conn.execute('SELECT * FROM server_reflection_contexts WHERE owner_user_id=%s AND context_id=%s',(owner,context)).fetchone()
        snapshot = row['snapshot'] if row else dict(binding=asdict(binding),**question_set())
        if snapshot['binding'] != asdict(binding):
            raise ValueError('reflection_context_changed')
        revision = digest(snapshot)
        if row and row['question_revision'] != revision:
            raise ValueError('reflection_snapshot_invalid')
        return snapshot, revision, row is not None

    def questions(self,owner,context):
        with self.pool.connection() as conn:
            resolved = self._context(conn,owner,context)
            if resolved is None:
                return None
            snapshot,revision,_ = resolved
            answers = conn.execute('''SELECT template_id,body,status,revision,updated_at
                FROM server_reflection_answers WHERE owner_user_id=%s AND context_id=%s
                ORDER BY template_id''',(owner,context)).fetchall()
        return dict(context_id=str(UUID(context)),question_revision=revision,
            binding=snapshot['binding'],questions=snapshot['questions'],groups=snapshot['groups'],
            answers=[dict(r,updated_at=r['updated_at'].isoformat()) for r in answers],
            storage_input_hash=NOTICE+':'+revision,notice_version=NOTICE,
            notice='읽기 전후 답변과 변경 요청 이력을 현재 학습 맥락에 저장합니다. AI 전송·자동 평가는 없습니다.')

    def save(self,owner,context,key,*,question_revision,template_id,body,status,expected_revision,storage_consent_id):
        context,key = str(UUID(context)),str(UUID(key))
        if (not isinstance(body,str) or len(body)>MAX_ANSWER_LENGTH or status not in ANSWER_STATUSES
                or (status=='completed' and not body.strip()) or type(expected_revision) is not int
                or not 0<=expected_revision<9223372036854775807):
            raise ValueError('reflection_answer_invalid')
        fingerprint = digest(dict(question_revision=question_revision,template_id=template_id,
                                  body=body,status=status,expected_revision=expected_revision))
        with self.pool.connection() as conn:
            resolved = self._context(conn,owner,context,lock=True)
            if resolved is None:
                return None
            snapshot,revision,exists = resolved
            if revision != question_revision or template_id not in {q['template_id'] for q in snapshot['questions']}:
                raise RemoteExecutionConflict('reflection_question_changed')
            replay = conn.execute('''SELECT input_hash,result FROM server_reflection_requests
                WHERE owner_user_id=%s AND context_id=%s AND request_key=%s''',(owner,context,key)).fetchone()
            if replay:
                if replay['input_hash'] != fingerprint:
                    raise RemoteExecutionConflict('reflection_request_changed')
                return replay['result']
            consent = conn.execute('''SELECT 1 FROM server_remote_consents WHERE owner_user_id=%s
                AND consent_id=%s AND purpose='storage' AND notice_version=%s AND input_hash=%s
                AND revoked_at IS NULL FOR SHARE''',(owner,storage_consent_id,NOTICE,NOTICE+':'+revision)).fetchone()
            if consent is None:
                raise ValueError('reflection_storage_consent_required')
            if not exists:
                conn.execute('''INSERT INTO server_reflection_contexts(owner_user_id,context_id,question_revision,snapshot)
                    VALUES (%s,%s,%s,%s::jsonb)''',(owner,context,revision,encoded(snapshot)))
            current = conn.execute('''SELECT revision FROM server_reflection_answers
                WHERE owner_user_id=%s AND context_id=%s AND template_id=%s''',(owner,context,template_id)).fetchone()
            if (current['revision'] if current else 0) != expected_revision:
                raise RemoteExecutionConflict('reflection_revision_conflict')
            result = conn.execute('''INSERT INTO server_reflection_answers
                (owner_user_id,context_id,template_id,body,status,revision) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(owner_user_id,context_id,template_id) DO UPDATE SET body=EXCLUDED.body,
                    status=EXCLUDED.status,revision=EXCLUDED.revision,updated_at=clock_timestamp()
                RETURNING template_id,body,status,revision,updated_at''',
                (owner,context,template_id,body,status,expected_revision+1)).fetchone()
            result = dict(result,updated_at=result['updated_at'].isoformat(),context_id=context,
                          question_revision=revision,request_key=key)
            conn.execute('''INSERT INTO server_reflection_requests(owner_user_id,context_id,request_key,input_hash,result)
                VALUES (%s,%s,%s,%s,%s::jsonb)''',(owner,context,key,fingerprint,encoded(result)))
            return result

    def comparison(self,owner,context):
        view = self.questions(owner,context)
        if view is None:
            return None
        answers = {a['template_id']:a for a in view['answers'] if a['status'] != 'deleted'}
        questions = {q['template_id']:q for q in view['questions']}
        def item(key):
            return dict(question=questions[key],answer=answers.get(key))
        return dict(context_id=view['context_id'],question_revision=view['question_revision'],binding=view['binding'],
            groups=[dict(group_id=g['group_id'],before=item(g['before_id']),after=[item(k) for k in g['after_ids']])
                    for g in view['groups']],notice='저장된 두 시점의 현재 답변을 나란히 표시합니다. 이해 향상이나 정답을 판정하지 않습니다.')

    def delete_answer(self,owner,context,template_id,expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('reflection_revision_invalid')
        with self.pool.connection() as conn:
            if self._context(conn,owner,context,lock=True) is None:
                return None
            row = conn.execute('''UPDATE server_reflection_answers SET body='',status='deleted',
                revision=revision+1,updated_at=clock_timestamp() WHERE owner_user_id=%s
                AND context_id=%s AND template_id=%s AND revision=%s AND status<>'deleted'
                RETURNING template_id,revision''',
                (owner,context,template_id,expected_revision)).fetchone()
            if row is None:
                raise RemoteExecutionConflict('reflection_revision_conflict')
            # 동일 키 재생으로 삭제 본문이 다시 노출되거나 재생성되지 않게 이력도 지운다.
            conn.execute('''UPDATE server_reflection_requests SET result=jsonb_build_object(
                'state','deleted','context_id',context_id,'template_id',%s::text,'request_key',request_key,
                'revision',%s::bigint)
                WHERE owner_user_id=%s AND context_id=%s AND result->>'template_id'=%s''',
                (template_id,row['revision'],owner,context,template_id))
        return dict(state='deleted',context_id=str(UUID(context)),template_id=template_id,revision=row['revision'])

    def export(self,owner,context):
        with self.pool.connection() as conn:
            scoped = PostgresRemoteReflection(SimpleNamespace(connection=lambda:nullcontext(conn)))
            # 저장/삭제와 같은 부모 잠금으로 현재 답변과 이력을 일관되게 읽는다.
            if self._context(conn,owner,context,lock=True) is None:
                return None
            view = scoped.questions(owner,context)
            rows = conn.execute('''SELECT result FROM server_reflection_requests WHERE owner_user_id=%s
                AND context_id=%s ORDER BY created_at,request_key''',(owner,context)).fetchall()
            return dict(current=view,request_history=[r['result'] for r in rows])
