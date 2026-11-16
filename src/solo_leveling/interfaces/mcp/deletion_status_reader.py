"""인증 경계에서 고정한 소유자 범위로 기존 삭제 안내를 조회한다."""

from collections.abc import Callable
from typing import Protocol

from solo_leveling.interfaces.http.remote_deletions import DeletionRequest
from solo_leveling.interfaces.mcp.deletion_guidance import DeletionLookup, DeletionSnapshot


class ExistingDeletionStore(Protocol):
    def get_existing(self, owner_user_id: str, paper_id: str, request_key: str) -> DeletionRequest | None:
        ...


class OwnerScopedDeletionReader:
    """호출자가 입력한 owner는 인증이 아니다. 현재 인증 주체를 매번 다시 확인한다."""

    def __init__(self, store: ExistingDeletionStore, current_owner: Callable[[], str | None]):
        self._store = store
        self._current_owner = current_owner

    def read(self, lookup: DeletionLookup) -> DeletionSnapshot | None:
        if not isinstance(lookup, DeletionLookup):
            return None
        owner = self._current_owner()
        if not isinstance(owner, str) or not owner.strip() or owner != lookup.owner_id:
            return None
        result = self._store.get_existing(owner, lookup.paper_id, lookup.request_key)
        # 조회 중 계정이 전환되거나 인증이 해제되면 이전 계정 응답을 반환하지 않는다.
        if self._current_owner() != owner or result is None:
            return None
        if not isinstance(result, DeletionRequest) or result.paper_id != lookup.paper_id:
            raise ValueError('remote_deletion_snapshot_invalid')
        # 저장소 complete는 활성 저장소만 의미한다. 백업 삭제 사실은 별도 계약이다.
        return DeletionSnapshot(lookup, result.request_id, result.status, access_allowed=True,
                                backup_erasure_confirmed=False)
