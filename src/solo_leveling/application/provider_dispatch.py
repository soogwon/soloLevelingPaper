"""실제 제공자 전송 1회 앞의 B gate. 지속 원장·동의·네트워크 adapter는 주입 포트다."""

from dataclasses import dataclass
from typing import Protocol

STAGES = frozenset({'generation', 'repair', 'coverage', 'semantic_verification', 'translation'})


class DispatchError(RuntimeError):
    """본문·토큰·식별자를 포함하지 않는 고정 내부 코드."""


class DispatchBudgetExceeded(DispatchError):
    """제한 상태를 근거 부족이나 원장 장애와 구분한다."""
    def __init__(self, reason):
        super().__init__('DISPATCH_TIME_BUDGET_EXCEEDED' if reason == 'time' else 'DISPATCH_CALL_TOKEN_BUDGET_EXCEEDED')


@dataclass(frozen=True, repr=False)
class DispatchIntent:
    owner_user_id: str
    execution_id: str
    lease_token: str
    input_hash: str
    storage_consent_id: str
    ai_consent_id: str
    notice_version: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in self.__dict__.values()):
            raise DispatchError('DISPATCH_INTENT_INVALID')


@dataclass(frozen=True, repr=False)
class AttemptRequest:
    intent: DispatchIntent
    dispatch_key: str
    stage: str
    input_token_upper_bound: int
    max_output_tokens: int

    def __post_init__(self):
        if (not isinstance(self.intent, DispatchIntent) or not isinstance(self.dispatch_key, str)
                or not self.dispatch_key.strip() or not isinstance(self.stage, str) or self.stage not in STAGES
                or any(type(v) is not int or v < 1 for v in (self.input_token_upper_bound, self.max_output_tokens))):
            raise DispatchError('DISPATCH_REQUEST_INVALID')


@dataclass(frozen=True, repr=False)
class AttemptPermit:
    attempt_id: str
    request: AttemptRequest


@dataclass(frozen=True, repr=False)
class ProviderResponse:
    payload: object
    input_tokens: int | None
    output_tokens: int | None


class DispatchLedger(Protocol):
    def authorize(self, request: AttemptRequest) -> AttemptPermit | None:
        """현재 세대·동의/고지·중지·예산을 원자 검사/예약하고 해당 key의 전송 허가를 한 번만 소비한다."""
        ...

    def observe(self, attempt_id: str, input_tokens: int, output_tokens: int) -> None:
        """알려진 시도를 한 번만 정산한다. 초과 사용도 사실대로 기록하고 후속 허가를 막는다."""
        ...

    def unknown(self, attempt_id: str) -> None:
        """예약을 반환하지 않고 보류한다. 이미 정산된 관측을 unknown으로 덮어쓰지 않는다."""
        ...


class ProviderDispatch:
    def __init__(self, ledger: DispatchLedger, intent: DispatchIntent):
        if not isinstance(intent, DispatchIntent):
            raise DispatchError('DISPATCH_INTENT_INVALID')
        self.ledger, self.intent = ledger, intent
        self._used, self._blocked = set(), False

    def _unknown(self, permit):
        self._blocked = True
        try:
            self.ledger.unknown(permit.attempt_id)
        except Exception:
            raise DispatchError('DISPATCH_RECONCILIATION_UNAVAILABLE') from None

    def send(self, stage, input_token_upper_bound, max_output_tokens, transport, *, dispatch_key):
        """transport(request)는 재시도 없는 실제 전송 1회다. 토큰 상한은 신뢰하는 추정기가 제공한다."""
        if self._blocked:
            raise DispatchError('DISPATCH_BLOCKED')
        request = AttemptRequest(self.intent, dispatch_key, stage, input_token_upper_bound, max_output_tokens)
        try:
            permit = self.ledger.authorize(request)
        except DispatchBudgetExceeded:
            self._blocked = True
            raise
        except Exception:
            self._blocked = True
            raise DispatchError('DISPATCH_AUTHORIZATION_UNAVAILABLE') from None
        if permit is None:
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        if (not isinstance(permit, AttemptPermit) or permit.request != request
                or not isinstance(permit.attempt_id, str) or not permit.attempt_id.strip()
                or permit.attempt_id in self._used):
            self._blocked = True
            raise DispatchError('DISPATCH_PERMIT_INVALID')
        self._used.add(permit.attempt_id)
        try:
            response = transport(request)
        except Exception:
            self._unknown(permit)
            raise DispatchError('DISPATCH_USAGE_UNKNOWN') from None
        if (not isinstance(response, ProviderResponse)
                or any(type(v) is not int or v < 0 for v in (response.input_tokens, response.output_tokens))):
            self._unknown(permit)
            raise DispatchError('DISPATCH_USAGE_UNKNOWN')
        try:
            self.ledger.observe(permit.attempt_id, response.input_tokens, response.output_tokens)
        except Exception:
            self._unknown(permit)
            raise DispatchError('DISPATCH_RECONCILIATION_UNAVAILABLE') from None
        if response.input_tokens > input_token_upper_bound or response.output_tokens > max_output_tokens:
            self._blocked = True
            raise DispatchError('DISPATCH_RESERVATION_EXCEEDED')
        return response
