import pytest

from solo_leveling.domain.deletion_policy import (
    DELETION_NOTICE_KO,
    DeletionIntent,
    DeletionPhase,
    DeletionPolicy,
    current_deletion_policy,
    deletion_phases,
)


@pytest.mark.parametrize("intent", [
    DeletionIntent.LOCAL_ONLY,
    DeletionIntent.STOP_SYNC,
    DeletionIntent.REVOKE_CONSENT,
])
def test_non_deletion_intents_never_create_server_physical_deletion(intent):
    assert deletion_phases(intent) == ()


def test_server_deletion_blocks_recreation_before_removing_data():
    phases = deletion_phases(DeletionIntent.SERVER_PAPER)
    assert phases == (
        DeletionPhase.BLOCK_WRITES,
        DeletionPhase.CANCEL_JOBS,
        DeletionPhase.DELETE_DERIVED,
        DeletionPhase.DELETE_SOURCE,
        DeletionPhase.RETAIN_TOMBSTONE,
    )
    assert deletion_phases(DeletionIntent.ACCOUNT) == phases


@pytest.mark.parametrize("policy", [
    DeletionPolicy(),
    DeletionPolicy(30, None, 30),
    DeletionPolicy(0, 1, 1),
    DeletionPolicy(True, 1, 1),
])
def test_unapproved_or_invalid_retention_never_enables_physical_cleanup(policy):
    with pytest.raises(ValueError, match="deletion_retention_policy_unapproved"):
        policy.validate_for_physical_deletion()


def test_tombstone_must_outlive_every_restorable_backup():
    with pytest.raises(ValueError, match="tombstone_must_outlive_backup"):
        DeletionPolicy(30, 31, 30).validate_for_physical_deletion()
    DeletionPolicy(31, 30, 30).validate_for_physical_deletion()


def test_current_policy_uses_approved_60_30_30_without_changing_fail_closed_default():
    policy = current_deletion_policy()
    assert policy == DeletionPolicy(60, 30, 30)
    policy.validate_for_physical_deletion()
    with pytest.raises(ValueError, match="deletion_retention_policy_unapproved"):
        DeletionPolicy().validate_for_physical_deletion()


def test_current_user_notice_matches_approved_copy_exactly():
    assert DELETION_NOTICE_KO == (
        "삭제 요청 즉시 해당 자료에 대한 접근과 처리가 중단됩니다. "
        "활성 저장소와 백업을 포함한 완전 삭제는 최대 30일이 걸릴 수 있습니다. "
        "삭제된 데이터가 복원이나 지연된 작업으로 다시 생성되지 않도록 "
        "최소한의 삭제 표식은 추가로 최대 30일간 보관됩니다."
    )
