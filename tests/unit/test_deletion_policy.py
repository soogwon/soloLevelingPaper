import pytest

from solo_leveling.domain.deletion_policy import (
    DeletionIntent,
    DeletionPhase,
    DeletionPolicy,
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
