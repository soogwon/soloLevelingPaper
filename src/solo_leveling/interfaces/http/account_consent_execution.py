"""계정 동의가 모두 현재이면 유료 서버 생성의 요청별 동의를 서버가 대신 기록한다.

비용은 운영자가 부담하고 예산 한도로 막으므로 사용자에게 요청마다 확인받지 않는다.
요청별 동의 기록과 예산·접수 검사는 기존 경로를 그대로 사용한다.
"""
from .account_consents import account_consent_current

PURPOSES = ('storage', 'ai_provider')


def account_ready(pool, owner):
    with pool.connection() as conn:
        return all(account_consent_current(conn, owner, purpose) for purpose in PURPOSES)


def grant_and_submit(pool, owner, prepared, *, grant, submit=None):
    """prepare 결과를 받아 계정 동의로 두 목적을 기록하고, submit이 있으면 바로 접수한다."""
    if prepared is None or not account_ready(pool, owner):
        return prepared
    for purpose in PURPOSES:
        grant(purpose)
    if submit is None:
        return prepared
    receipt = submit()
    if receipt is None:
        return prepared
    return {**prepared, **receipt, 'status': 'submitted', 'consent': 'account'}
