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


def test_oversized_output_is_discarded_and_next_record_survives():
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write('x' * 10000)
    assert stream.pending == ''
    stream.write('\n' + json.dumps(record()) + '\n')
    assert json.loads(output.getvalue()) == record()
