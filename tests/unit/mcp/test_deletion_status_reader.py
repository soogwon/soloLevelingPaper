"""삭제 상태 조회는 접수와 분리하고 현재 인증 계정에만 안내한다."""
from unittest.mock import Mock

import pytest

from solo_leveling.interfaces.http.remote_deletions import DeletionRequest, PostgresRemoteDeletionStore
from solo_leveling.interfaces.mcp.deletion_guidance import DeletionLookup, recover_deletion_guidance
from solo_leveling.interfaces.mcp.deletion_status_reader import OwnerScopedDeletionReader
from tests.unit.test_remote_deletions import Pool


@pytest.mark.parametrize('status', ['blocking', 'cleaning', 'failed', 'complete'])
def test_reads_existing_status_without_write_or_backup_inference(status):
    pool = Pool(old={'request_id': 'r', 'paper_id': 'p', 'status': status})
    reader = OwnerScopedDeletionReader(PostgresRemoteDeletionStore(pool), lambda: 'owner')
    lookup = DeletionLookup('owner', 'p', 'key')
    for _ in range(2):
        assert recover_deletion_guidance(reader, lookup).state == status
    assert len(pool.conn.calls) == 2
    for sql, params in pool.conn.calls:
        assert sql.strip().startswith('SELECT')
        assert 'FOR UPDATE' not in sql
        assert 'owner_user_id=%s AND paper_id=%s AND request_key=%s' in sql
        assert params == ('owner', 'p', 'key')


@pytest.mark.parametrize('owner', [None, '', 'other'])
def test_wrong_or_signed_out_owner_never_queries_store(owner):
    store = Mock()
    reader = OwnerScopedDeletionReader(store, lambda: owner)
    assert recover_deletion_guidance(reader, DeletionLookup('owner', 'p', 'key')).state == 'not_found'
    store.get_existing.assert_not_called()
    store.request.assert_not_called()


def test_account_switch_during_read_discards_previous_account_result():
    store = Mock()
    store.get_existing.return_value = DeletionRequest('r', 'p', 'complete')
    reader = OwnerScopedDeletionReader(store, Mock(side_effect=['owner', 'other']))
    assert recover_deletion_guidance(reader, DeletionLookup('owner', 'p', 'key')).state == 'not_found'
    store.request.assert_not_called()


def test_missing_request_does_not_fall_back_to_creating_one():
    store = Mock()
    store.get_existing.return_value = None
    reader = OwnerScopedDeletionReader(store, lambda: 'owner')
    assert recover_deletion_guidance(reader, DeletionLookup('owner', 'p', 'key')).state == 'not_found'
    store.request.assert_not_called()


@pytest.mark.parametrize('result', [DeletionRequest('r', 'other', 'complete'),
                                     DeletionRequest('r', 'p', 'unexpected'), object()])
def test_malformed_store_result_does_not_claim_completion(result):
    store = Mock()
    store.get_existing.return_value = result
    reader = OwnerScopedDeletionReader(store, lambda: 'owner')
    assert recover_deletion_guidance(reader, DeletionLookup('owner', 'p', 'key')).state == 'unavailable'


def test_error_is_fixed_and_next_lookup_can_recover():
    store = Mock()
    store.get_existing.side_effect = [RuntimeError('PRIVATE_BODY'), DeletionRequest('r', 'p', 'complete')]
    reader = OwnerScopedDeletionReader(store, lambda: 'owner')
    lookup = DeletionLookup('owner', 'p', 'key')
    first = recover_deletion_guidance(reader, lookup)
    assert first.state == 'unavailable' and 'PRIVATE_BODY' not in first.message
    assert recover_deletion_guidance(reader, lookup).state == 'complete'
    store.request.assert_not_called()


@pytest.mark.parametrize('values', [('', 'p', 'key'), ('owner', ' ', 'key'), ('owner', 'p', None)])
def test_invalid_store_lookup_is_rejected_before_database(values):
    pool = Pool()
    with pytest.raises(ValueError, match='remote_deletion_input_invalid'):
        PostgresRemoteDeletionStore(pool).get_existing(*values)
    assert pool.conn.calls == []
