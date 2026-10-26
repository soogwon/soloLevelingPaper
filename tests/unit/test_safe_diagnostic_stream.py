"""안전한 단계 로그 보존과 원본 문자열·추가 필드 폐기를 검증한다."""

from io import StringIO
import json

import pytest

from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream


def record():
    return dict(request_id='a' * 32, span_id='b' * 32, stage='ask_paper',
                event='start', elapsed_ms=0, error_code=None)


def test_partial_writes_preserve_stage_but_drop_plaintext():
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write('PRIVATE_KEY\n')
    line = json.dumps(record())
    stream.write(line[:20])
    stream.write(line[20:] + '\n')
    stream.flush()
    assert json.loads(output.getvalue()) == record()


@pytest.mark.parametrize('changes', [
    {'question': 'PRIVATE_TEXT'}, {'stage': 'PRIVATE_TEXT'}, {'stage': []},
    {'request_id': 'PRIVATE_KEY'}, {'event': 'PRIVATE_TEXT'},
    {'error_code': 'PRIVATE_EXCEPTION'}, {'elapsed_ms': float('nan')},
    {'elapsed_ms': True}, {'elapsed_ms': -1},
])
def test_unapproved_fields_and_values_are_dropped(changes):
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(record() | changes) + '\n')
    assert output.getvalue() == ''


@pytest.mark.parametrize('stage', ['semantic_verification', 'semantic_api_call'])
def test_semantic_stages_keep_only_existing_safe_fields(stage):
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    safe = record() | {'stage': stage}
    stream.write(json.dumps(safe) + '\n')
    stream.write(json.dumps(safe | {'claim_text': 'PRIVATE_CLAIM'}) + '\n')
    assert json.loads(output.getvalue()) == safe
    assert 'PRIVATE_CLAIM' not in output.getvalue()


def test_oversized_output_is_discarded_and_next_record_survives():
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write('x' * 10000)
    assert stream.pending == ''
    stream.write('\n' + json.dumps(record()) + '\n')
    assert json.loads(output.getvalue()) == record()


@pytest.mark.parametrize('enabled,connected,version', [
    (True, True, 'semantic-v2'), (True, True, 'semantic-v3'),
    (False, False, None), (True, False, None),
])
def test_semantic_startup_survives_default_filter(monkeypatch, enabled, connected, version):
    from solo_leveling import diagnostics
    output = StringIO()
    monkeypatch.setattr(diagnostics.sys, 'stderr', SafeDiagnosticStream(output))
    diagnostics.semantic_startup_diagnostic(enabled=enabled, connected=connected, prompt_version=version)
    assert json.loads(output.getvalue()) == dict(event='semantic_startup', enabled=enabled,
        connected=connected, prompt_version=version)


@pytest.mark.parametrize('changes', [
    {'enabled': 'PRIVATE_KEY'}, {'enabled': 1}, {'connected': 'true'},
    {'prompt_version': 'PRIVATE_TEXT'}, {'prompt_version': []},
    {'api_key': 'PRIVATE_KEY'}, {'path': 'PRIVATE_PATH'},
])
def test_semantic_startup_rejects_unsafe_fields(changes):
    output = StringIO()
    value = dict(event='semantic_startup', enabled=True, connected=True, prompt_version='semantic-v2')
    SafeDiagnosticStream(output).write(json.dumps(value | changes) + '\n')
    assert output.getvalue() == ''


def test_semantic_startup_write_failure_does_not_stop_server(monkeypatch):
    from unittest.mock import Mock
    from solo_leveling import diagnostics
    monkeypatch.setattr(diagnostics.sys, 'stderr', Mock(write=Mock(side_effect=OSError('private'))))
    diagnostics.semantic_startup_diagnostic(enabled=True, connected=True, prompt_version='semantic-v2')


def extraction_record():
    return record() | dict(stage='extraction_diagnostic', event='claim_excluded',
        attempt_id='c' * 32, claim_number=1, round_number=1, reason_code='SUPPORT_NOT_FOUND')


@pytest.mark.parametrize('changes', [
    {'claim_text': 'PRIVATE'}, {'evidence_id': 'PRIVATE'}, {'question': 'PRIVATE'},
    {'attempt_id': 'PRIVATE'}, {'reason_code': 'PRIVATE'}, {'reason_code': []},
    {'event': 'candidate'}, {'claim_number': True}, {'claim_number': 0},
    {'claim_number': 'PRIVATE'}, {'round_number': True}, {'round_number': 2},
    {'elapsed_ms': float('nan')}, {'error_code': 'PRIVATE'},
    {'event': 'repair_outcome', 'reason_code': 'REPAIR_ADOPTED'},
])
def test_extraction_diagnostic_rejects_unapproved_data(changes):
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(extraction_record() | changes) + '\n')
    assert output.getvalue() == ''


@pytest.mark.parametrize('event,reason,number', [
    ('quality', 'NO_EXTRACTION_RISK', 1),
    ('quality', 'UNFINISHED_TAIL', 2),
    ('claim_excluded', 'SUPPORT_NOT_FOUND', 1),
    ('claim_excluded', 'UNRESOLVED_PREFIX', 2),
    ('repair_outcome', 'REPAIR_ADOPTED', None),
    ('repair_outcome', 'REPAIR_PARTIAL', None),
    ('repair_outcome', 'REPAIR_NO_USABLE_CLAIMS', None),
    ('repair_outcome', 'REPAIR_INVALID', None),
    ('repair_outcome', 'REPAIR_UNAVAILABLE', None),
    ('verification_failure', 'GENERATION_JSON_INVALID', None),
    ('verification_failure', 'GENERATION_SCHEMA_INVALID', None),
    ('verification_failure', 'GENERATION_FORMAT_INVALID', None),
    ('verification_failure', 'DRAFT_INVALID', None),
    ('verification_failure', 'EMPTY_EVIDENCE_IDS', 1),
    ('verification_failure', 'DUPLICATE_EVIDENCE_ID', 2),
    ('verification_failure', 'UNKNOWN_EVIDENCE_ID', 2),
    ('verification_failure', 'SUPPORT_REFERENCE_INVALID', 1),
])
def test_extraction_events_survive_filter_without_evidence_metadata(monkeypatch, event, reason, number):
    from solo_leveling import diagnostics
    output = StringIO()
    monkeypatch.setattr(diagnostics.sys, 'stderr', SafeDiagnosticStream(output))
    @diagnostics.traced('answer', request=True)
    def emit():
        diagnostics.evidence_diagnostic(event, attempt_id='c' * 32,
            claim_number=number, round_number=1, reason_code=reason,
            evidence_id='PRIVATE_EVIDENCE', chunk_id='PRIVATE_CHUNK')
    emit()
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == 3
    result = rows[1]
    assert result['stage'] == 'extraction_diagnostic'
    assert result['event'] == event and result['reason_code'] == reason
    assert result['claim_number'] == number and result['round_number'] == 1
    assert result['request_id'] == rows[0]['request_id'] == rows[2]['request_id']
    assert set(result) == set(extraction_record())
    assert 'PRIVATE' not in output.getvalue()


@pytest.mark.parametrize('changes', [
    {'reason_code': 'PRIVATE'}, {'reason_code': 'UNFINISHED_TAIL'},
    {'reason_code': []}, {'claim_number': True}, {'claim_number': 0},
    {'claim_number': -1}, {'claim_number': 10001}, {'claim_number': 'PRIVATE'},
    {'quote_original': 'PRIVATE'}, {'question': 'PRIVATE'}, {'evidence_id': 'PRIVATE'},
    {'error_code': 'PRIVATE'}, {'round_number': 2},
])
def test_verification_failure_rejects_unapproved_values(changes):
    output = StringIO()
    row = extraction_record() | {
        'event': 'verification_failure', 'reason_code': 'UNKNOWN_EVIDENCE_ID',
        'claim_number': 1,
    }
    SafeDiagnosticStream(output).write(json.dumps(row | changes) + '\n')
    assert output.getvalue() == ''


@pytest.mark.parametrize('detail', [
    'NEXT_CANDIDATE_ABSENT', 'NEXT_NOT_CITED', 'LEFT_TAIL_START_MISSING',
    'LEFT_TAIL_END_MISSING', 'NEXT_NOT_CONTINUATION', 'NEXT_SENTENCE_END_ABSENT',
    'RIGHT_PREFIX_START_MISSING', 'RIGHT_PREFIX_END_MISSING',
])
def test_tail_detail_survives_default_filter(monkeypatch, detail):
    from solo_leveling import diagnostics
    output = StringIO()
    monkeypatch.setattr(diagnostics.sys, 'stderr', SafeDiagnosticStream(output))
    @diagnostics.traced('answer', request=True)
    def emit():
        diagnostics.evidence_diagnostic('quality', attempt_id='c' * 32, claim_number=1,
            reason_code='UNFINISHED_TAIL', detail_code=detail, evidence_id='PRIVATE')
    emit()
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rows[1]['detail_code'] == detail
    assert len(rows) == 3
    assert 'PRIVATE' not in output.getvalue()


@pytest.mark.parametrize('changes', [
    {'detail_code': 'PRIVATE'}, {'detail_code': []}, {'detail_code': None},
    {'detail_code': True}, {'event': 'claim_excluded'}, {'reason_code': 'NO_EXTRACTION_RISK'},
    {'quote_original': 'PRIVATE'},
])
def test_tail_detail_rejects_unapproved_data(changes):
    output = StringIO()
    row = extraction_record() | dict(event='quality', reason_code='UNFINISHED_TAIL',
                                      detail_code='NEXT_NOT_CITED')
    SafeDiagnosticStream(output).write(json.dumps(row | changes) + '\n')
    assert output.getvalue() == ''
