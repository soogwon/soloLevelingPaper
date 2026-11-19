"""검수 완료 퀴즈 저장·제출 기반. 생성 provider와 외부 API는 연결하지 않는다."""
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace
from uuid import UUID, uuid4
import json

from solo_leveling.application.quiz.attempts import QuizAttemptService
from solo_leveling.domain.quiz import Quiz, QuizQuestion
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from .remote_reflection import digest, encoded
from .remote_retrieval import PostgresRemoteContextResolver
from .remote_executions import RemoteExecutionConflict

NOTICE = 'remote-quiz-attempt-v1'


class PostgresRemoteQuiz:
    def __init__(self,pool):
        self.pool = pool

    def _binding(self,conn,owner,context,*,write=False):
        parent = conn.execute('''SELECT p.paper_id FROM server_execution_contexts c JOIN server_papers p
            ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id WHERE c.owner_user_id=%s
            AND c.context_id=%s AND p.deleted_at IS NULL''' + (' FOR UPDATE OF p' if write else ' FOR SHARE OF p'),
            (owner,str(UUID(context)))).fetchone()
        if parent is None:
            return None
        return PostgresRemoteContextResolver(SimpleNamespace(connection=lambda:nullcontext(conn))).resolve(owner,context)

    def store_reviewed(self,owner,context,quiz_id,step,questions):
        """내부 검수 입력만 저장한다. MCP나 웹 쓰기 도구에 노출하지 않는다."""
        context,quiz_id = str(UUID(context)),str(UUID(quiz_id))
        if step not in LEARNING_STEPS or type(questions) is not tuple or not 1<=len(questions)<=20:
            raise ValueError('remote_quiz_invalid')
        if any(type(q) is not QuizQuestion for q in questions):
            raise ValueError('remote_quiz_invalid')
        snapshot = dict(quiz_id=quiz_id,step=step,questions=[asdict(q) for q in questions])
        if len(encoded(snapshot))>262144:
            raise ValueError('remote_quiz_too_large')
        with self.pool.connection() as conn:
            binding = self._binding(conn,owner,context,write=True)
            if binding is None:
                return None
            ids = sorted({str(UUID(e)) for q in questions for e in q.evidence_ids})
            known = conn.execute('''SELECT evidence_id FROM server_processing_evidences WHERE owner_user_id=%s
                AND context_id=%s AND publication_id=%s AND parse_revision_id=%s AND evidence_id=ANY(%s::uuid[])''',
                (owner,context,binding.publication_id,binding.parse_revision_id,ids)).fetchall()
            if {str(r['evidence_id']) for r in known} != set(ids):
                raise ValueError('remote_quiz_evidence_invalid')
            snapshot['binding'] = asdict(binding)
            fingerprint = digest(snapshot)
            old = conn.execute('SELECT snapshot_hash FROM server_remote_quizzes WHERE owner_user_id=%s AND context_id=%s AND quiz_id=%s', (owner,context,quiz_id)).fetchone()
            if old:
                if old['snapshot_hash'] != fingerprint:
                    raise RemoteExecutionConflict('remote_quiz_changed')
            else:
                conn.execute('''INSERT INTO server_remote_quizzes(owner_user_id,context_id,quiz_id,step,snapshot,snapshot_hash)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s)''',(owner,context,quiz_id,step,encoded(snapshot),fingerprint))
        return self.get(owner,context,quiz_id)

    def _quiz(self,conn,owner,context,quiz_id,*,write=False):
        binding = self._binding(conn,owner,context,write=write)
        if binding is None:
            return None
        row = conn.execute('''SELECT * FROM server_remote_quizzes WHERE owner_user_id=%s
            AND context_id=%s AND quiz_id=%s''',(owner,context,str(UUID(quiz_id)))).fetchone()
        if row is None:
            return None
        if digest(row['snapshot']) != row['snapshot_hash'] or row['snapshot']['binding'] != asdict(binding):
            raise ValueError('remote_quiz_snapshot_invalid')
        return row

    def get(self,owner,context,quiz_id):
        with self.pool.connection() as conn:
            row = self._quiz(conn,owner,context,quiz_id)
            if row is None:
                return None
        return dict(quiz_id=str(row['quiz_id']),context_id=str(UUID(context)),step=row['step'],
            questions=[dict(position=i,question_type=q['question_type'],prompt=q['prompt'],choices=q['choices'])
                       for i,q in enumerate(row['snapshot']['questions'])],
            notice_version=NOTICE,storage_input_hash=NOTICE+':'+row['snapshot_hash'],
            grading_notice='단답형은 정규화된 정답 문구와 정확히 일치할 때만 정답입니다. AI 채점하지 않습니다.')

    def consent(self,owner,context,quiz_id):
        with self.pool.connection() as conn:
            row = self._quiz(conn,owner,context,quiz_id)
            if row is None:
                return None
            current = conn.execute("""SELECT consent_id FROM server_remote_consents WHERE owner_user_id=%s
                AND purpose='storage' AND notice_version=%s AND input_hash=%s AND revoked_at IS NULL
                ORDER BY consent_id LIMIT 1""",(owner,NOTICE,NOTICE+':'+row['snapshot_hash'])).fetchone()
            return dict(context_id=str(UUID(context)),quiz_id=str(UUID(quiz_id)),quiz_revision=row['snapshot_hash'],
                        notice_version=NOTICE,storage='current' if current else 'missing',
                        consent_id=current['consent_id'] if current else None,
                        notice='이 학습 맥락의 고정 퀴즈에 제출한 답안·점수·오답 근거를 저장합니다. AI 생성·전송 동의는 포함하지 않습니다.')

    def grant_storage(self,owner,context,quiz_id,notice,quiz_revision):
        if notice != NOTICE:
            raise ValueError('remote_quiz_notice_changed')
        with self.pool.connection() as conn:
            row = self._quiz(conn,owner,context,quiz_id,write=True)
            if row is None:
                return None
            if row['snapshot_hash'] != quiz_revision:
                raise RemoteExecutionConflict('remote_quiz_revision_changed')
            current = conn.execute("""SELECT consent_id FROM server_remote_consents WHERE owner_user_id=%s
                AND purpose='storage' AND notice_version=%s AND input_hash=%s AND revoked_at IS NULL
                LIMIT 1""",(owner,NOTICE,NOTICE+':'+quiz_revision)).fetchone()
            if current is None:
                conn.execute("""INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
                    VALUES (%s,%s,'storage',%s,%s)""",(str(uuid4()),owner,NOTICE+':'+quiz_revision,NOTICE))
        return self.consent(owner,context,quiz_id)

    def revoke_storage(self,owner,context,quiz_id):
        with self.pool.connection() as conn:
            row = self._quiz(conn,owner,context,quiz_id,write=True)
            if row is None:
                return None
            conn.execute("""UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE owner_user_id=%s
                AND purpose='storage' AND notice_version=%s AND input_hash=%s AND revoked_at IS NULL""",
                (owner,NOTICE,NOTICE+':'+row['snapshot_hash']))
        return self.consent(owner,context,quiz_id)

    def submit(self,owner,context,quiz_id,key,answers,storage_consent_id):
        context,quiz_id,key = str(UUID(context)),str(UUID(quiz_id)),str(UUID(key))
        if (type(answers) is not dict or not answers or len(answers)>20
                or any(type(k) is not int or type(v) not in (int,str)
                       or (isinstance(v,str) and len(v)>5000) for k,v in answers.items())):
            raise ValueError('remote_quiz_answers_invalid')
        fingerprint = digest(dict(quiz_id=quiz_id,answers=sorted(answers.items())))
        with self.pool.connection() as conn:
            row = self._quiz(conn,owner,context,quiz_id,write=True)
            if row is None:
                return None
            old = conn.execute('''SELECT input_hash,payload FROM server_remote_quiz_attempts
                WHERE owner_user_id=%s AND context_id=%s AND request_key=%s''',(owner,context,key)).fetchone()
            if old:
                if old['input_hash'] != fingerprint:
                    raise RemoteExecutionConflict('remote_quiz_request_changed')
                return old['payload']
            consent = conn.execute('''SELECT 1 FROM server_remote_consents WHERE owner_user_id=%s
                AND consent_id=%s AND purpose='storage' AND notice_version=%s AND input_hash=%s
                AND revoked_at IS NULL FOR SHARE''',(owner,storage_consent_id,NOTICE,NOTICE+':'+row['snapshot_hash'])).fetchone()
            if consent is None:
                raise ValueError('remote_quiz_storage_consent_required')
            quiz = Quiz(quiz_id,context,row['step'],tuple(QuizQuestion(**q) for q in row['snapshot']['questions']),row['created_at'].isoformat())
            attempt = QuizAttemptService('')._make_attempt(quiz,answers)
            payload = json.loads(encoded(dict(asdict(attempt),correct_count=attempt.correct_count,
                                             total_count=attempt.total_count,score=attempt.score,request_key=key)))
            conn.execute('''INSERT INTO server_remote_quiz_attempts(owner_user_id,context_id,request_key,quiz_id,
                attempt_id,input_hash,payload,correct_count,total_count) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)''',
                (owner,context,key,quiz_id,attempt.attempt_id,fingerprint,encoded(payload),attempt.correct_count,attempt.total_count))
            return payload

    def attempt(self,owner,context,attempt_id):
        with self.pool.connection() as conn:
            if self._binding(conn,owner,context) is None:
                return None
            row = conn.execute('''SELECT payload FROM server_remote_quiz_attempts WHERE owner_user_id=%s
                AND context_id=%s AND attempt_id=%s''',(owner,context,str(UUID(attempt_id)))).fetchone()
            return row['payload'] if row else None

    def generation_result(self, owner, context, request_key):
        """생성 요청 키에서 완료 퀴즈만 찾는다. 정답은 기존 get과 동일하게 숨긴다."""
        from uuid import uuid5, NAMESPACE_URL
        from .remote_executions import PostgresRemoteExecutionStore
        with self.pool.connection() as conn:
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            execution = PostgresRemoteExecutionStore(scoped).lookup(owner, 'generate_quiz', str(UUID(request_key)))
            if (execution is None or not execution.access_allowed
                    or execution.lookup.context_id != str(UUID(context))):
                return None
            quiz = None
            if execution.state == 'completed':
                quiz_id = str(uuid5(NAMESPACE_URL, 'remote-quiz:' + owner + ':' + execution.execution_id))
                quiz = PostgresRemoteQuiz(scoped).get(owner, context, quiz_id)
                if quiz is None:
                    raise ValueError('remote_quiz_result_missing')
            return dict(context_id=str(UUID(context)), request_key=str(UUID(request_key)),
                        execution_id=execution.execution_id, state=execution.state, quiz=quiz)

    def attempt_by_key(self, owner, context, quiz_id, request_key):
        """전송 결과가 유실돼도 새 답안 저장 없이 같은 요청 결과만 조회한다."""
        with self.pool.connection() as conn:
            if self._quiz(conn, owner, context, quiz_id) is None:
                return None
            row = conn.execute("""SELECT payload FROM server_remote_quiz_attempts
                WHERE owner_user_id=%s AND context_id=%s AND quiz_id=%s AND request_key=%s""",
                (owner, str(UUID(context)), str(UUID(quiz_id)), str(UUID(request_key)))).fetchone()
            return dict(context_id=str(UUID(context)), quiz_id=str(UUID(quiz_id)),
                        request_key=str(UUID(request_key)), attempt=row['payload'] if row else None)

    def history(self,owner,context,*,limit=20,cursor=None):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('remote_quiz_page_invalid')
        with self.pool.connection() as conn:
            if self._binding(conn,owner,context) is None:
                return None
            after = None
            if cursor is not None:
                after = conn.execute('''SELECT created_at,attempt_id FROM server_remote_quiz_attempts
                    WHERE owner_user_id=%s AND context_id=%s AND attempt_id=%s''',(owner,context,str(UUID(cursor)))).fetchone()
                if after is None:
                    raise ValueError('remote_quiz_page_invalid')
            rows = conn.execute('''SELECT attempt_id,quiz_id,correct_count,total_count,created_at
                FROM server_remote_quiz_attempts WHERE owner_user_id=%s AND context_id=%s''' +
                (' AND (created_at,attempt_id)<(%s,%s)' if after else '') +
                ' ORDER BY created_at DESC,attempt_id DESC LIMIT %s',
                (owner,context,*((after['created_at'],after['attempt_id']) if after else ()),limit+1)).fetchall()
            return dict(items=[dict(r,attempt_id=str(r['attempt_id']),quiz_id=str(r['quiz_id']),
                                   created_at=r['created_at'].isoformat()) for r in rows[:limit]],
                        next_cursor=str(rows[limit-1]['attempt_id']) if len(rows)>limit else None)

    def scores(self,owner,context):
        with self.pool.connection() as conn:
            if self._binding(conn,owner,context) is None:
                return None
            rows = conn.execute('''SELECT q.step,count(*) AS attempts,
                max(a.correct_count::float/a.total_count) AS best_score,
                (array_agg(a.correct_count::float/a.total_count ORDER BY a.created_at DESC,a.attempt_id DESC))[1] AS latest_score
                FROM server_remote_quiz_attempts a JOIN server_remote_quizzes q
                ON q.owner_user_id=a.owner_user_id AND q.context_id=a.context_id AND q.quiz_id=a.quiz_id
                WHERE a.owner_user_id=%s AND a.context_id=%s GROUP BY q.step''',(owner,context)).fetchall()
            by_step = {r['step']:r for r in rows}
            return [by_step.get(step,dict(step=step,attempts=0,best_score=None,latest_score=None)) for step in LEARNING_STEPS]
