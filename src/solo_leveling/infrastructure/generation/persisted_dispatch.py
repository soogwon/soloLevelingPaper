"""원격 Responses 전송을 영속 원장과 정확한 실행 scope 검사에 연결한다."""
from solo_leveling.application.provider_dispatch import ProviderDispatch
from solo_leveling.interfaces.http.dispatch_ledger import PostgresDispatchLedger
from solo_leveling.interfaces.http.remote_executions import validate_execution_scope
from .gated_responses import GatedResponsesTransport


def create_persisted_responses_transport(pool, intent, policy_version, window_key, prepare, *, post=None):
    """prepare는 안전한 토큰 상한·영속 전송 키를 공급한다. 임의 정책·키를 생성하지 않는다.

    반환 transport를 생성기와 의미 검증기에 함께 주입한다. post 대역을 생략한 실제 전송에는
    별도의 외부 호출 승인이 필요하다. 기존 로컬 생성기의 기본 transport는 바꾸지 않는다.
    """
    if not callable(prepare):
        raise ValueError('dispatch_prepare_required')
    ledger = PostgresDispatchLedger(pool, policy_version, window_key, validate_execution_scope)
    return GatedResponsesTransport(ProviderDispatch(ledger, intent), prepare, post=post)
