from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from solo_leveling.application.remote_replay import (
    ExecutionLookup, StoredExecution, ReplayError, read_existing_execution,
)


@pytest.fixture
def stored():
    lookup = ExecutionLookup('alice', 'ask_paper', 'same-key', 'hash', 'ctx')
    payload = dict(context_id='ctx', status='partial', verification_level='model_checked',
                   reason_code='semantic_limited', answer_ko='합성 저장 답변',
                   claims=[dict(claim_id='claim', text='합성 주장', evidence_ids=['e'])],
                   citations=[dict(evidence_id='e', chunk_id='c', version_id='v', parse_revision_id='p',
                                   translation_revision_id='t', printed_page_label=None, pdf_page=6,
                                   quote_original='Synthetic saved evidence.', quote_ko='합성 저장 인용')],
                   verification_history={'status': 'saved'})
    raw = json.dumps(payload, ensure_ascii=False)
    return StoredExecution(lookup, 'exec', 'completed', 'usage_unknown', True, True,
                           raw, hashlib.sha256(raw.encode('utf-8')).hexdigest())


def test_replay_preserves_ids_partial_quality_and_unknown_budget_without_new_run(stored):
    calls = []
    def lookup(*key): calls.append(key); return stored
    reader = NS(lookup=lookup)  # submit/provider/save 포트가 없다.
    first = read_existing_execution(reader, stored.lookup)
    first.payload['claims'].clear()  # 반환값 변경이 다음 재생/저장 문자열을 바꾸면 안 된다.
    again = read_existing_execution(reader, stored.lookup)
    assert again.payload == json.loads(stored.payload_json)
    assert again.payload['verification_level'] == 'model_checked'
    assert again.payload['status'] == 'partial' and again.budget_state == 'usage_unknown'
    assert calls == [('alice', 'ask_paper', 'same-key')] * 2


@pytest.mark.parametrize('field,value', [('owner_user_id', 'bob'), ('operation', 'other'), ('request_key', 'other')])
def test_foreign_execution_and_missing_are_not_disclosed(stored, field, value):
    row = replace(stored, lookup=replace(stored.lookup, **{field: value}))
    assert read_existing_execution(NS(lookup=lambda *_: row), stored.lookup) is None
    assert read_existing_execution(NS(lookup=lambda *_: None), stored.lookup) is None


@pytest.mark.parametrize('field,value', [('input_hash', 'other'), ('context_id', 'other')])
def test_same_key_different_input_is_conflict_not_new_execution(stored, field, value):
    with pytest.raises(ReplayError, match='REMOTE_EXECUTION_CONFLICT'):
        read_existing_execution(NS(lookup=lambda *_: stored), replace(stored.lookup, **{field: value}))


def test_lookup_failure_never_falls_back_to_generation(stored):
    def failed(*_): raise RuntimeError('private-database-error')
    with pytest.raises(ReplayError, match='^REMOTE_LOOKUP_UNAVAILABLE$'):
        read_existing_execution(NS(lookup=failed), stored.lookup)


@pytest.mark.parametrize('budget', ['available', 'limited', 'usage_unknown'])
def test_current_access_controls_reading_independently_of_ai_budget(stored, budget):
    row = replace(stored, budget_state=budget)
    assert read_existing_execution(NS(lookup=lambda *_: row), stored.lookup).payload is not None
    assert read_existing_execution(NS(lookup=lambda *_: replace(row, access_allowed=False)), stored.lookup) is None


@pytest.mark.parametrize('change', ['digest', 'context', 'reference', 'extra', 'history'])
def test_corrupt_saved_result_does_not_trigger_regeneration(stored, change):
    payload = json.loads(stored.payload_json)
    if change == 'context': payload['context_id'] = 'other'
    elif change == 'reference': payload['claims'][0]['evidence_ids'] = ['missing']
    elif change == 'extra': payload['private_api_key'] = 'never-return'
    elif change == 'history': payload['verification_history']['status'] = 'unknown'
    raw = json.dumps(payload)
    row = replace(stored, payload_json=raw, payload_sha256='bad' if change == 'digest'
                  else hashlib.sha256(raw.encode('utf-8')).hexdigest())
    with pytest.raises(ReplayError, match='^REMOTE_SAVED_RESULT_INVALID$'):
        read_existing_execution(NS(lookup=lambda *_: row), stored.lookup)


def test_completed_without_result_returns_status_only(stored):
    row = replace(stored, result_available=False, payload_json=None, payload_sha256=None)
    result = read_existing_execution(NS(lookup=lambda *_: row), stored.lookup)
    assert result.state == 'completed' and result.payload is None


def test_queued_execution_is_restored_without_resubmission(stored):
    row = replace(stored, state='queued', result_available=False, payload_json=None, payload_sha256=None)
    calls = []
    def lookup(*key):
        calls.append(key)
        return row
    result = read_existing_execution(NS(lookup=lookup), stored.lookup)
    assert result.execution_id == 'exec' and result.state == 'queued' and result.payload is None
    assert calls == [('alice', 'ask_paper', 'same-key')]
