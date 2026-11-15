"""로컬 삭제, 서버 삭제, 동의 철회와 계정 삭제를 구분하는 계약."""

from dataclasses import dataclass
from enum import StrEnum


CURRENT_TOMBSTONE_RETENTION_DAYS = 60
CURRENT_BACKUP_EXPIRY_DAYS = 30
CURRENT_ACCOUNT_ERASURE_DAYS = 30
DELETION_NOTICE_KO = (
    "삭제 요청 즉시 해당 자료에 대한 접근과 처리가 중단됩니다. "
    "활성 저장소와 백업을 포함한 완전 삭제는 최대 30일이 걸릴 수 있습니다. "
    "삭제된 데이터가 복원이나 지연된 작업으로 다시 생성되지 않도록 "
    "최소한의 삭제 표식은 추가로 최대 30일간 보관됩니다."
)


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


def current_deletion_policy() -> DeletionPolicy:
    """2026-11-16에 승인된 현재 운영 삭제 기간을 명시적으로 선택한다."""
    return DeletionPolicy(
        tombstone_retention_days=CURRENT_TOMBSTONE_RETENTION_DAYS,
        backup_expiry_days=CURRENT_BACKUP_EXPIRY_DAYS,
        account_erasure_days=CURRENT_ACCOUNT_ERASURE_DAYS,
    )


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
