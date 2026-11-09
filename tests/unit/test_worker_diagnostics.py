"""자식 기본 로그의 고정 스키마와 민감정보 차단을 검증한다."""

import pytest

from solo_leveling.worker_diagnostics import valid_worker_stage


@pytest.mark.parametrize('override', [
    {'stage': 'SYNTHETIC_PRIVATE'}, {'event': 'unknown'}, {'request_id': '/tmp/private'},
    {'span_id': 'question'}, {'text': 'secret'}, {'error_code': 'SYNTHETIC_PRIVATE'},
    {'elapsed_ms': -1}, {'elapsed_ms': float('nan')}, {'elapsed_ms': float('inf')},
    {'elapsed_ms': True}, {'event': 'error', 'error_code': None},
])
def test_invalid_worker_stage_is_rejected(override):
    record = dict(request_id='a' * 32, span_id='b' * 32, stage='embedding_encode',
                  event='end', elapsed_ms=1.25, error_code=None)
    assert valid_worker_stage(record)
    assert not valid_worker_stage(record | override)


def test_default_worker_sink_rejects_extra_fields_and_arbitrary_stages():
    from io import StringIO
    import json
    from solo_leveling.diagnostics import _emit
    from solo_leveling.worker_diagnostics import stage_output

    output = StringIO()
    token = stage_output.set(output)
    try:
        args = ('a' * 32, 'b' * 32, 'embedding_encode', 'start', 0)
        _emit(*args, details={'text': 'SYNTHETIC_PRIVATE'})
        _emit(args[0], args[1], 'SYNTHETIC_PRIVATE', 'end', 1)
        _emit(*args)
    finally:
        stage_output.reset(token)
    assert json.loads(output.getvalue()) == dict(
        request_id=args[0], span_id=args[1], stage='embedding_encode',
        event='start', elapsed_ms=0, error_code=None)


@pytest.mark.parametrize('raw', [False, True])
def test_invalid_worker_id_is_rejected_before_opening_logs(tmp_path, raw):
    from unittest.mock import Mock
    from solo_leveling.infrastructure.embeddings.worker import diagnostic_worker

    connection, target = Mock(), Mock()
    diagnostic_worker(connection, target, tmp_path, 'SYNTHETIC_PRIVATE', 1, collect_raw=raw)
    target.assert_not_called()
    connection.send.assert_called_once_with({'ok': False})
    connection.close.assert_called_once_with()
    assert not list(tmp_path.iterdir())
