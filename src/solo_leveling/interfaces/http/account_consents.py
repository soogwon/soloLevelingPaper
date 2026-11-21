"""첫 로그인에서 항목별로 한 번 받는 계정 단위 동의.

기능별 동의(업로드 의도·학습 맥락·퀴즈 등)는 이 동의가 현재일 때 서버가 대신 기록하거나
현재 동의로 인정한다. 항목은 목적별로 분리하며 선택 항목을 거부해도 필수 항목만으로 가입한다.
"""
from uuid import uuid4

NOTICE_VERSION = 'account-consent-v1'
PURPOSES = ('storage', 'ai_provider')
REQUIRED = ('storage',)
NOTICES = {
    'storage': (
        '업로드한 PDF와 학습 기록(메모·하이라이트, 읽기 질문 답변, 퀴즈 응시 기록, 저장한 요약, 질문 답변)을 '
        '내 계정의 서버에 보관하고 웹과 Claude에서 다시 볼 수 있게 합니다. 서비스 제공에 꼭 필요한 항목입니다. '
        '기록과 PDF는 언제든 삭제할 수 있으며, 삭제 요청 즉시 접근과 처리를 멈추고 활성 저장소와 백업의 '
        '완전 삭제에는 최대 30일이 걸릴 수 있습니다.'
    ),
    'ai_provider': (
        '논문 PDF에서 추출한 원문 구간, 질문, 선택한 근거를 미국의 OpenAI(OpenAI, L.L.C.)에 네트워크로 보내 '
        '번역·검색 색인 보완·답변·요약·퀴즈 생성에 사용합니다(개인정보 국외 이전). PDF 파일 자체는 보내지 '
        '않습니다. 요청에 저장하지 않음 옵션을 쓰지만 제공자 측 보관은 OpenAI의 API 데이터 정책을 따릅니다. '
        '비용은 운영자가 부담합니다. 동의하지 않아도 가입과 PDF 보관은 할 수 있지만, 업로드한 PDF의 학습 자료 '
        '생성과 서버 생성 기능은 쓸 수 없습니다. 계정 설정에서 언제든 철회할 수 있으며, 철회해도 이미 보낸 '
        '요청은 되돌리지 않습니다.'
    ),
}


def account_consent_id(conn, owner, purpose):
    """현재 계정 동의 ID 또는 None. 기능별 역할도 표 권한 없이 서버 함수로 확인한다."""
    if purpose not in PURPOSES:
        raise ValueError('account_consent_purpose_invalid')
    row = conn.execute('SELECT server_account_consent_current(%s,%s,%s) AS consent_id',
                       (owner, purpose, NOTICE_VERSION)).fetchone()
    return str(row['consent_id']) if row['consent_id'] is not None else None


def account_consent_current(conn, owner, purpose):
    return account_consent_id(conn, owner, purpose) is not None


class UnavailableAccountConsents:
    def view(self, *args, **kwargs):
        raise RuntimeError('account_consent_unavailable')

    grant = revoke = view


class PostgresAccountConsents:
    def __init__(self, pool):
        self._pool = pool

    @staticmethod
    def _latest(conn, owner):
        rows = conn.execute('''SELECT DISTINCT ON (purpose) purpose,revision,consent_id,notice_version,
                granted_at,revoked_at
            FROM server_account_consents WHERE owner_user_id=%s
            ORDER BY purpose,revision DESC''', (owner,)).fetchall()
        return {row['purpose']: row for row in rows}

    @staticmethod
    def _view(latest):
        items = []
        for purpose in PURPOSES:
            row = latest.get(purpose)
            state = ('missing' if row is None else 'revoked' if row['revoked_at'] is not None
                     else 'stale' if row['notice_version'] != NOTICE_VERSION else 'current')
            items.append(dict(purpose=purpose, required=purpose in REQUIRED, notice=NOTICES[purpose],
                              state=state, revision=int(row['revision']) if row else 0,
                              granted_at=row['granted_at'] if row else None,
                              revoked_at=row['revoked_at'] if row else None))
        complete = all(item['state'] == 'current' for item in items if item['required'])
        return dict(notice_version=NOTICE_VERSION, complete=complete, items=items)

    def view(self, owner):
        _owner(owner)
        with self._pool.connection() as conn:
            return self._view(self._latest(conn, owner))

    def grant(self, owner, purposes, notice_version):
        """같은 고지의 현재 동의는 유지하고 없거나 오래된 항목만 새 판으로 기록한다."""
        _owner(owner)
        if notice_version != NOTICE_VERSION:
            raise ValueError('account_consent_notice_changed')
        purposes = tuple(dict.fromkeys(purposes))
        if not purposes or any(p not in PURPOSES for p in purposes):
            raise ValueError('account_consent_purpose_invalid')
        with self._pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'{owner}\x1faccount-consent',))
            latest = self._latest(conn, owner)
            for purpose in purposes:
                row = latest.get(purpose)
                if row is not None and row['revoked_at'] is None and row['notice_version'] == NOTICE_VERSION:
                    continue
                if row is not None and row['revoked_at'] is None:
                    conn.execute('''UPDATE server_account_consents SET revoked_at=clock_timestamp()
                        WHERE owner_user_id=%s AND purpose=%s AND revoked_at IS NULL''', (owner, purpose))
                conn.execute('''INSERT INTO server_account_consents
                    (owner_user_id,purpose,revision,consent_id,notice_version) VALUES (%s,%s,%s,%s,%s)''',
                    (owner, purpose, int(row['revision']) + 1 if row else 1, str(uuid4()), NOTICE_VERSION))
            return self._view(self._latest(conn, owner))

    def revoke(self, owner, purpose):
        _owner(owner)
        if purpose not in PURPOSES:
            raise ValueError('account_consent_purpose_invalid')
        with self._pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'{owner}\x1faccount-consent',))
            conn.execute('''UPDATE server_account_consents SET revoked_at=clock_timestamp()
                WHERE owner_user_id=%s AND purpose=%s AND revoked_at IS NULL''', (owner, purpose))
            return self._view(self._latest(conn, owner))


def _owner(owner):
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError('account_consent_input_invalid')
