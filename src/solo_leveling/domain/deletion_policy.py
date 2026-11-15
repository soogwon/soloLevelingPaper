"""로컬 삭제, 서버 삭제, 동의 철회와 계정 삭제를 구분하는 계약."""

from dataclasses import dataclass
from enum import StrEnum


class DeletionIntent(StrEnum):
    LOCAL_ONLY = "local_only"
    SERVER_PAPER = "server_paper"
    ACCOUNT = "account"
    STOP_SYNC = "stop_sync"
    REVOKE_CONSENT = "revoke_consent"


class DeletionPhase(StrEnum):
    BLOCK_WRITES = "block_writes"
    CANCEL_JOBS = "cancel_jobs"
    DELETE_DERIVED = "delete_derived"
    DELETE_SOURCE = "delete_source"
    RETAIN_TOMBSTONE = "retain_tombstone"


@dataclass(frozen=True)
class DeletionPolicy:
    """승인되지 않은 기간을 코드 기본값으로 만들지 않는 삭제 정책."""

    tombstone_retention_days: int | None = None
    backup_expiry_days: int | None = None
    account_erasure_days: int | None = None

    def validate_for_physical_deletion(self) -> None:
        values = (
            self.tombstone_retention_days,
            self.backup_expiry_days,
            self.account_erasure_days,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("deletion_retention_policy_unapproved")
        if self.tombstone_retention_days < self.backup_expiry_days:
            raise ValueError("tombstone_must_outlive_backup")


def deletion_phases(intent: DeletionIntent) -> tuple[DeletionPhase, ...]:
    """명시적 서버/계정 삭제만 물리 삭제 계획을 만든다."""
    if intent in (DeletionIntent.LOCAL_ONLY, DeletionIntent.STOP_SYNC, DeletionIntent.REVOKE_CONSENT):
        return ()
    if intent in (DeletionIntent.SERVER_PAPER, DeletionIntent.ACCOUNT):
        return (
            DeletionPhase.BLOCK_WRITES,
            DeletionPhase.CANCEL_JOBS,
            DeletionPhase.DELETE_DERIVED,
            DeletionPhase.DELETE_SOURCE,
            DeletionPhase.RETAIN_TOMBSTONE,
        )
    raise ValueError("deletion_intent_invalid")
