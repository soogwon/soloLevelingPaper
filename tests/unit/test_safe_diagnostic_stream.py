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


@pytest.mark.parametrize('line', ['{broken', 'null', '[]', '"PRIVATE_TEXT"', '123'])
def test_invalid_json_or_non_object_does_not_block_next_record(line):
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write(line + '\n' + json.dumps(record()) + '\n')
    assert json.loads(output.getvalue()) == record()


@pytest.mark.parametrize('field', list(record()))
def test_missing_field_is_dropped(field):
    output = StringIO()
    incomplete = record()
    del incomplete[field]
    SafeDiagnosticStream(output).write(json.dumps(incomplete) + '\n')
    assert output.getvalue() == ''


@pytest.mark.parametrize('changes', [
    {'span_id': 'PRIVATE_KEY'}, {'request_id': None}, {'span_id': 'a' * 31},
    {'event': []}, {'error_code': {}}, {'elapsed_ms': '12'},
    {'elapsed_ms': None}, {'elapsed_ms': float('inf')}, {'elapsed_ms': 1e16},
])
def test_invalid_field_types_and_bounds_are_dropped(changes):
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(record() | changes) + '\n')
    assert output.getvalue() == ''


@pytest.mark.parametrize(('event', 'code'), [
    ('start', None), ('end', None), ('error', 'STAGE_FAILED'), ('error', 'CANCELLED'),
])
def test_allowed_events_remain_available_for_diagnosis(event, code):
    output = StringIO()
    expected = record() | {'event': event, 'error_code': code, 'elapsed_ms': 1.25}
    SafeDiagnosticStream(output).write(json.dumps(expected) + '\n')
    assert json.loads(output.getvalue()) == expected


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


def test_oversized_output_is_discarded_and_next_record_survives():
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write('x' * 10000)
    assert stream.pending == ''
    stream.write('\n' + json.dumps(record()) + '\n')
    assert json.loads(output.getvalue()) == record()
