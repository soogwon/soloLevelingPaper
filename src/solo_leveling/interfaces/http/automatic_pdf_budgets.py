"""원격 PDF 전용 가입 예산과 접수 판정."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re


class AutomaticPdfQuotaExceeded(Exception):
    """계정당 처리 접수 건수 한도에 도달했다. 공개 코드만 담는다."""


@dataclass(frozen=True)
class AutomaticPdfBudget:
    policy_version: str
    window_key: str
    # 기간은 선택이다. 둘 다 비우면 만료 없이 접수하고, 지정하려면 둘 다 지정한다.
    starts_at: datetime | None
    expires_at: datetime | None
    input_tokens: int
    output_tokens: int
    allowed_owner_ids: tuple[str, ...] | None = None
    max_jobs_per_owner: int | None = None

    def __post_init__(self):
        if (not re.fullmatch(r'pdf-auto-[a-z0-9-]{1,80}', self.policy_version)
                or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', self.window_key)
                or (self.starts_at is None) != (self.expires_at is None)
                or (self.starts_at is not None and (
                    self.starts_at.utcoffset() is None or self.expires_at.utcoffset() is None
                    or self.starts_at >= self.expires_at))
                or (self.allowed_owner_ids is not None and (
                    type(self.allowed_owner_ids) is not tuple
                    or any(type(owner) is not str or not owner or len(owner) > 512
                           for owner in self.allowed_owner_ids)))
                or (self.max_jobs_per_owner is not None and (
                    type(self.max_jobs_per_owner) is not int or not 1 <= self.max_jobs_per_owner <= 10000))
                or any(type(n) is not int or not 1 <= n <= 2**31 - 1
                       for n in (self.input_tokens, self.output_tokens))):
            raise ValueError('automatic_pdf_budget_invalid')

    def active(self):
        if self.starts_at is None:
            return True
        return self.starts_at <= datetime.now(timezone.utc) < self.expires_at

    def _eligible(self, owner):
        return self.active() and (self.allowed_owner_ids is None or owner in self.allowed_owner_ids)

    def provision(self, conn, owner):
        if self._eligible(owner):
            # 서비스 예산은 운영자가 명시적으로 준비하며 로그인에서는 증액할 수 없다.
            conn.execute("""INSERT INTO server_dispatch_budget_limits
                (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
                SELECT 'owner',%s,%s,%s,%s,%s
                WHERE EXISTS (SELECT 1 FROM server_dispatch_budget_limits
                    WHERE scope_type='service' AND scope_id='service' AND policy_version=%s AND window_key=%s)
                ON CONFLICT DO NOTHING""",
                (owner,self.policy_version,self.window_key,self.input_tokens,self.output_tokens,
                 self.policy_version,self.window_key))

    def quota_reached(self, conn, owner):
        """계정당 접수 건수 한도. 실패·취소·삭제한 논문의 접수도 비용이 났을 수 있어 센다."""
        if self.max_jobs_per_owner is None:
            return False
        count = conn.execute("""SELECT count(*) AS total FROM server_processing_job_authorizations
            WHERE owner_user_id=%s AND processing_policy_version=%s AND processing_window_key=%s""",
            (owner,self.policy_version,self.window_key)).fetchone()['total']
        return count >= self.max_jobs_per_owner

    def allows_admission(self, conn, owner):
        if not self._eligible(owner):
            return False
        # 접수 transaction은 공통 제어 행을 잠가 동시 접수를 직렬화한다.
        if self.quota_reached(conn, owner):
            raise AutomaticPdfQuotaExceeded('remote_processing_quota_exceeded')
        self.provision(conn, owner)
        rows = conn.execute("""SELECT scope_type,max_input_tokens,max_output_tokens,
                      reserved_input_tokens,reserved_output_tokens,consumed_input_tokens,consumed_output_tokens
               FROM server_dispatch_budget_limits WHERE policy_version=%s AND window_key=%s
                 AND ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=%s))
               ORDER BY scope_type,scope_id""", (self.policy_version,self.window_key,owner)).fetchall()
        # 접수는 남은 예산을 안내하고 실제 전송 한도는 worker가 원장 행을 잠가 검사한다.
        return {row['scope_type'] for row in rows} == {'owner','service'} and all(
            row[f'max_{kind}_tokens'] > row[f'reserved_{kind}_tokens'] + row[f'consumed_{kind}_tokens']
            for row in rows for kind in ('input','output'))


def load_automatic_pdf_budget():
    raw = os.environ.get('REMOTE_AUTOMATIC_PDF_BUDGET_JSON')
    if raw is None:
        return None
    data = json.loads(raw)
    if 'allowed_owner_ids' in data:
        if type(data['allowed_owner_ids']) is not list:
            raise ValueError('automatic_pdf_budget_invalid')
        data['allowed_owner_ids'] = tuple(data['allowed_owner_ids'])
    for field in ('starts_at', 'expires_at'):
        value = data.get(field)
        data[field] = None if value is None else datetime.fromisoformat(value)
    return AutomaticPdfBudget(**data)
