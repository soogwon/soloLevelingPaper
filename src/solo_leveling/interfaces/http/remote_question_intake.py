"""검증된 owner·불변 입력·동의와 전체 질문 예산을 같은 transaction에 접수한다."""
from contextlib import nullcontext
from types import SimpleNamespace

from solo_leveling.application.remote_replay import ExecutionLookup
from .remote_executions import PostgresRemoteExecutionStore, RemoteExecutionConflict


class _AdmissionDenied(Exception):
    pass


class PostgresQuestionIntake:
    def __init__(self,pool):
        self.pool=pool

    def submit(self, owner, request_key, request, storage_id, ai_id, notice, *, policy, window_key):
        if not isinstance(window_key,str) or not window_key.strip() or len(window_key)>200:
            raise ValueError('remote_question_window_invalid')
        binding=request.unpack()[0]
        if owner!=binding.owner_user_id:
            return None
        lookup=ExecutionLookup(owner,'ask_paper',request_key,request.input_hash,binding.context_id)
        try:
            with self.pool.connection() as conn:
                store=PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda:nullcontext(conn)))
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f'{owner}\x1fask_paper\x1f{request_key}',))
                existing=store.lookup(owner,'ask_paper',request_key)
                saved=store.create(lookup,storage_id,ai_id,notice,policy=policy,question_input=request)
                if saved is None:
                    raise _AdmissionDenied()
                if existing is None:
                    control=conn.execute("SELECT intake_enabled FROM server_dispatch_controls WHERE control_id='provider_dispatch' FOR SHARE").fetchone()
                    if control is None or not control['intake_enabled']:
                        raise _AdmissionDenied()
                reservation=conn.execute("SELECT policy_version,window_key FROM server_question_budget_reservations WHERE owner_user_id=%s AND execution_id=%s",(owner,saved.execution_id)).fetchone()
                if existing is not None:
                    if reservation is None:
                        raise RemoteExecutionConflict('remote_question_admission_conflict')
                    return saved
                limits=conn.execute("""SELECT * FROM server_dispatch_budget_limits
                    WHERE policy_version=%s AND window_key=%s AND
                        ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=%s))
                    ORDER BY scope_type,scope_id FOR UPDATE""",(policy.version,window_key,owner)).fetchall()
                if ({r['scope_type'] for r in limits}!={'owner','service'} or any(
                        r['reserved_input_tokens']+r['consumed_input_tokens']+policy.max_input_tokens>r['max_input_tokens']
                        or r['reserved_output_tokens']+r['consumed_output_tokens']+policy.max_output_tokens>r['max_output_tokens']
                        for r in limits)):
                    raise _AdmissionDenied()
                conn.execute("""INSERT INTO server_question_budget_reservations
                    (owner_user_id,execution_id,policy_version,window_key,remaining_input_tokens,remaining_output_tokens)
                    VALUES (%s,%s,%s,%s,%s,%s)""",(owner,saved.execution_id,policy.version,window_key,policy.max_input_tokens,policy.max_output_tokens))
                conn.execute("""UPDATE server_dispatch_budget_limits SET
                    reserved_input_tokens=reserved_input_tokens+%s,reserved_output_tokens=reserved_output_tokens+%s
                    WHERE policy_version=%s AND window_key=%s AND
                        ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=%s))""",
                    (policy.max_input_tokens,policy.max_output_tokens,policy.version,window_key,owner))
                return saved
        except _AdmissionDenied:
            return None
