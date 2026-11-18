"""승인된 질문 입력 계수 1회를 영속 예약한다. 추정값 대체·자동 재전송은 없다."""
from copy import deepcopy
import math
import requests

from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from solo_leveling.interfaces.http.remote_executions import validate_execution_scope, _question_consent
from .question_contract import QuestionContract

REVISION = 'responses-input-tokens-v1'
NOTICE = 'question-count-and-generation-v1'
ENDPOINT = 'https://api.openai.com/v1/responses/input_tokens'


def require(ok):
    if not ok:
        raise DispatchError('QUESTION_TOKEN_COUNT_REJECTED')


class PersistedTokenCounter:
    def __init__(self, pool, intent, contract, allowed, *, api_key, enabled=False, timeout_seconds=10, post=None):
        require(isinstance(contract, QuestionContract) and contract.estimator_revision == REVISION
                and callable(allowed) and type(enabled) is bool and type(api_key) is str and bool(api_key.strip())
                and type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds)
                and 0 < timeout_seconds <= 30)
        self.pool, self.intent, self.contract, self.allowed = pool, intent, contract, allowed
        self.key, self.enabled, self.timeout, self.post = api_key, enabled, timeout_seconds, post

    def _scope(self, conn):
        i = self.intent
        require(self.allowed() and i.notice_version == NOTICE and validate_execution_scope(conn, i))
        require(_question_consent(conn, i.owner_user_id, i.storage_consent_id, i.ai_consent_id,
                                  i.input_hash, i.notice_version))
        row = conn.execute('SELECT policy_snapshot FROM server_remote_executions WHERE owner_user_id=%s AND execution_id=%s',
                           (i.owner_user_id, i.execution_id)).fetchone()
        from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
        policy = RemoteExecutionPolicy(**row['policy_snapshot'])
        c = self.contract
        c.validate_policy(policy, c.model, c.window_key, c.profile, False)
        require(self.timeout <= policy.stage_timeout_seconds)
        reservation = conn.execute('''SELECT remaining_input_tokens,remaining_output_tokens FROM server_question_budget_reservations
            WHERE owner_user_id=%s AND execution_id=%s AND policy_version=%s AND window_key=%s FOR UPDATE''',
            (i.owner_user_id,i.execution_id,c.policy_version,c.window_key)).fetchone()
        require(reservation and reservation['remaining_input_tokens'] >= c.max_input_tokens
                and reservation['remaining_output_tokens'] >= c.max_output_tokens)
        limits = conn.execute('''SELECT * FROM server_dispatch_budget_limits WHERE policy_version=%s AND window_key=%s
            AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))
            ORDER BY scope_type,scope_id FOR UPDATE''', (c.policy_version,c.window_key,i.owner_user_id)).fetchall()
        c.validate_limits(limits)
        require(all(r['reserved_input_tokens']+r['consumed_input_tokens'] <= r['max_input_tokens']
                    and r['reserved_output_tokens']+r['consumed_output_tokens'] <= r['max_output_tokens'] for r in limits))

    def __call__(self, model, payload):
        require(self.enabled and model == self.contract.model)
        frozen = deepcopy(payload)
        self.contract.validate_payload('generation', frozen)
        digest = payload_digest(frozen)
        i, c = self.intent, self.contract
        reserved_here = False
        try:
            with self.pool.connection() as conn:
                self._scope(conn)
                row = conn.execute('SELECT * FROM server_question_token_counts WHERE owner_user_id=%s AND execution_id=%s',
                                   (i.owner_user_id,i.execution_id)).fetchone()
                if row:
                    require(row['status']=='observed' and row['payload_sha256']==digest and row['profile']==c.profile)
                    c.validate_upper(row['input_tokens'])
                    return row['input_tokens']
                conn.execute('''INSERT INTO server_question_token_counts
                    (owner_user_id,execution_id,lease_token,profile,payload_sha256,status)
                    VALUES (%s,%s,%s,%s,%s,'reserved')''', (i.owner_user_id,i.execution_id,i.lease_token,c.profile,digest))
            reserved_here = True
            # 예약을 확정한 뒤 전송하며 외부 호출 중에는 DB 잠금을 유지하지 않는다.
            require(self.allowed())
            body = {key:frozen[key] for key in ('model','instructions','input','text')}
            response = (requests.post if self.post is None else self.post)(ENDPOINT, json=body,
                headers={'Authorization': f'Bearer {self.key}'}, timeout=(5.0,self.timeout), allow_redirects=False)
            try:
                require(response.status_code == 200)
                data = response.json()
                require(type(data) is dict and data.get('object') == 'response.input_tokens')
                count = data.get('input_tokens')
                c.validate_upper(count)
            finally:
                response.close()
            with self.pool.connection() as conn:
                self._scope(conn)
                row = conn.execute('''UPDATE server_question_token_counts SET status='observed',input_tokens=%s
                    WHERE owner_user_id=%s AND execution_id=%s AND lease_token=%s AND payload_sha256=%s
                    AND status='reserved' RETURNING execution_id''', (count,i.owner_user_id,i.execution_id,i.lease_token,digest)).fetchone()
                require(row)
            return count
        except Exception:
            # 원문 오류·자격·응답을 노출하지 않는다. 예약/불명확 상태는 재시작 후에도 재전송하지 않는다.
            try:
                if not reserved_here:
                    raise DispatchError('QUESTION_TOKEN_COUNT_UNAVAILABLE')
                with self.pool.connection() as conn:
                    conn.execute('''UPDATE server_question_token_counts SET status='unknown'
                        WHERE owner_user_id=%s AND execution_id=%s AND lease_token=%s AND status='reserved' ''',
                        (i.owner_user_id,i.execution_id,i.lease_token))
            except Exception:
                pass
            raise DispatchError('QUESTION_TOKEN_COUNT_UNAVAILABLE') from None
