"""계정별 기존 요청 복구 모델. 인증·영속 저장·도구 등록은 주입하는 별도 경계다."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from solo_leveling.application.remote_replay import ExecutionLookup, ReplayError, ReplayView, read_existing_execution


class RecoveryKeyStore(Protocol):
    def get(self, owner_id: str, operation: str, request_key: str) -> ExecutionLookup | None:
        """계정별 제출 전 저장된 키만 읽는다. 토큰·질문 본문은 저장하지 않는다."""
        ...

    def put(self, lookup: ExecutionLookup) -> None:
        """같은 계정/작업/키의 다른 hash/context는 원자적으로 충돌 처리해야 한다."""
        ...


@dataclass(frozen=True, repr=False)
class RecoveryDisplay:
    status: str
    result: ReplayView | None = None


class AccountRecovery:
    """UI의 계정 전환과 조회 결과를 연결한다. 이 모델의 owner 값 자체는 인증이 아니다."""

    def __init__(self, keys: RecoveryKeyStore):
        self._keys = keys
        self._owner = None
        self._generation = 0
        self._display = RecoveryDisplay('signed_out')

    def select_account(self, verified_owner: str | None) -> None:
        # 호출자는 인증된 현재 owner를 전달한다. 재연결도 이전 응답을 무효화한다.
        if verified_owner is not None and (not isinstance(verified_owner, str) or not verified_owner.strip()):
            raise ReplayError('REMOTE_LOOKUP_INVALID')
        self._generation += 1
        self._owner = verified_owner
        self._display = RecoveryDisplay('signed_out' if verified_owner is None else 'idle')

    def snapshot(self) -> RecoveryDisplay:
        return deepcopy(self._display)

    def remember(self, lookup: ExecutionLookup) -> None:
        if not isinstance(lookup, ExecutionLookup) or self._owner is None or lookup.owner_user_id != self._owner:
            raise ReplayError('REMOTE_LOOKUP_INVALID')
        try:
            self._keys.put(lookup)
        except Exception:
            raise ReplayError('REMOTE_RECOVERY_KEY_UNAVAILABLE') from None

    def recover(self, operation: str, request_key: str, reader) -> RecoveryDisplay:
        owner = self._owner
        if owner is None:
            return self.snapshot()
        self._generation += 1
        generation = self._generation
        self._display = RecoveryDisplay('loading')
        try:
            if not all(isinstance(v, str) and v.strip() for v in (operation, request_key)):
                raise ReplayError('REMOTE_LOOKUP_INVALID')
            lookup = self._keys.get(owner, operation, request_key)
            if lookup is None:
                display = RecoveryDisplay('not_found')
            elif (not isinstance(lookup, ExecutionLookup)
                  or (lookup.owner_user_id, lookup.operation, lookup.request_key) != (owner, operation, request_key)):
                display = RecoveryDisplay('not_found')
            elif generation != self._generation:
                return self.snapshot()
            else:
                result = read_existing_execution(reader, lookup)
                display = RecoveryDisplay('not_found' if result is None else 'restored', result)
        except ReplayError as error:
            display = RecoveryDisplay('conflict' if str(error) == 'REMOTE_EXECUTION_CONFLICT' else 'unavailable')
        except Exception:
            display = RecoveryDisplay('unavailable')
        # A→B→A 전환도 세대로 구분한다. 새 계정 화면에 이전 응답을 게시하지 않는다.
        if generation == self._generation:
            self._display = display
        return self.snapshot()
