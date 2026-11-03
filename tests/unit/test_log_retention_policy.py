"""파일 접근 없이 공통 이름·JSON·기간 판정의 경계를 확인한다."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from solo_leveling.infrastructure.log_retention.contracts import BundleId, LeaseResult
from solo_leveling.infrastructure.log_retention.policy import (
    MAX_METADATA_BYTES, RetentionPolicy, closed_name, log_name,
    parse_log_name, parse_metadata_summary, retention_status,
)


BUNDLE = BundleId('worker', 'a' * 32)
TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def metadata(kinds=('safe',)):
    return {'schema_version': 1, 'owner': 'worker', 'run_id': 'a' * 32,
            'closed_at': '2026-01-01T00:00:00Z',
            'files': [{'kind': kind, 'name': log_name(BUNDLE, kind),
                       'size_bytes': 42, 'identity': None} for kind in kinds]}


def parse(row):
    return parse_metadata_summary(json.dumps(row).encode(), expected=BUNDLE)


@pytest.mark.parametrize('owner,kind', [('mcp', 'safe'), ('mcp', 'detail'),
    ('mcp', 'raw'), ('mcp', 'stack'), ('worker', 'safe'), ('worker', 'raw'), ('worker', 'stack')])
def test_name_round_trip(owner, kind):
    bundle = BundleId(owner, 'b' * 32)
    assert parse_log_name(log_name(bundle, kind)) == (bundle, kind)
    assert closed_name(bundle) == f'slp-log-v1-{owner}-' + 'b' * 32 + '.closed.json'


@pytest.mark.parametrize('name', [
    'worker-' + 'a' * 32 + '.log', '../' + log_name(BUNDLE, 'safe'),
    'C:\\' + log_name(BUNDLE, 'safe'), '/' + log_name(BUNDLE, 'safe'),
    log_name(BUNDLE, 'safe') + '\n', log_name(BUNDLE, 'safe').upper(),
    'slp-log-v1-worker-detail-' + 'a' * 32 + '.log', '', None,
])
def test_unrecognized_name_does_not_classify(name):
    assert parse_log_name(name) is None


@pytest.mark.parametrize('changes', [
    {'schema_version': True}, {'schema_version': 2}, {'owner': 'mcp'},
    {'run_id': 'b' * 32}, {'extra': 'PRIVATE'}, {'files': []},
    {'closed_at': '2026-01-01'}, {'closed_at': '2026-02-30T00:00:00Z'},
    {'closed_at': '2026-01-01T00:00:00+09:00'}, {'closed_at': None},
])
def test_metadata_rejects_unexpected_contract(changes):
    with pytest.raises(ValueError, match='^INVALID_LOG_METADATA$'):
        parse(metadata() | changes)


@pytest.mark.parametrize('changes', [
    {'name': '../PRIVATE'}, {'name': 'D:\\PRIVATE'}, {'kind': []},
    {'size_bytes': True}, {'size_bytes': -1}, {'size_bytes': 2**63},
    {'size_bytes': 1.5}, {'path': 'PRIVATE'},
])
def test_metadata_file_rejects_invalid_common_fields(changes):
    row = metadata()
    row['files'][0].update(changes)
    with pytest.raises(ValueError, match='^INVALID_LOG_METADATA$'):
        parse(row)


@pytest.mark.parametrize('identity', [None, {'undecided': 'PRIVATE'},
    ['PRIVATE'], 'PRIVATE', 42, True])
def test_identity_content_is_not_a_common_contract_or_closure_proof(identity):
    row = metadata()
    row['files'][0]['identity'] = identity
    summary = parse(row)
    assert summary == parse(metadata())
    assert 'PRIVATE' not in repr(summary)
    assert not hasattr(summary.files[0], 'identity')
    assert not hasattr(summary, 'can_delete')
    assert retention_status(summary, now=TIME + timedelta(days=30)) == 'expired'


@pytest.mark.parametrize('constant', [float('nan'), float('inf'), -float('inf')])
def test_identity_still_requires_standard_json(constant):
    row = metadata()
    row['files'][0]['identity'] = constant
    with pytest.raises(ValueError, match='^INVALID_LOG_METADATA$'):
        parse(row)


@pytest.mark.parametrize('payload', [b'null', b'[]', b'\xff', b'{',
    b' ' * (MAX_METADATA_BYTES + 1), b'{"owner":"worker","owner":"mcp"}',
    b'[' * 1100 + b']' * 1100])
def test_invalid_json_is_bounded_and_error_does_not_echo_payload(payload):
    with pytest.raises(ValueError, match='^INVALID_LOG_METADATA$'):
        parse_metadata_summary(payload, expected=BUNDLE)


def test_duplicate_files_and_missing_fields_are_rejected():
    row = metadata()
    row['files'] *= 2
    with pytest.raises(ValueError):
        parse(row)
    row = metadata()
    del row['files'][0]['identity']
    with pytest.raises(ValueError):
        parse(row)


@pytest.mark.parametrize('days,expected', [(29, 'retained'), (30, 'expired'), (31, 'expired')])
def test_retention_boundary(days, expected):
    assert retention_status(parse(metadata()), now=TIME + timedelta(days=days)) == expected


def test_expiry_does_not_claim_closed_and_mixed_bundle_waits_longest():
    summary = parse(metadata(('safe', 'raw', 'stack')))
    assert not hasattr(summary, 'can_delete')
    assert retention_status(summary, now=TIME + timedelta(days=31)) == 'raw_excluded'
    assert retention_status(summary, now=TIME + timedelta(days=1), include_raw=True) == 'retained'
    assert retention_status(summary, now=TIME + timedelta(days=30), include_raw=True) == 'expired'
    assert retention_status(summary, now=TIME - timedelta(seconds=1)) == 'future_timestamp'


@pytest.mark.parametrize('value', [0, -1, True, 1.5, float('nan'), 36501])
def test_policy_rejects_invalid_days(value):
    with pytest.raises(ValueError, match='^INVALID_RETENTION_POLICY$'):
        RetentionPolicy(safe_days=value)


def test_naive_time_and_non_boolean_raw_choice_rejected():
    with pytest.raises(ValueError):
        retention_status(parse(metadata()), now=datetime(2026, 2, 1))
    with pytest.raises(ValueError):
        retention_status(parse(metadata()), now=TIME, include_raw='yes')


def test_lease_result_does_not_claim_acquisition_without_handle():
    assert LeaseResult('unsupported').lease is None
    with pytest.raises(ValueError):
        LeaseResult('acquired')
    with pytest.raises(ValueError):
        LeaseResult('busy', object())
