"""임베딩 자식의 기본 로그에 허용하는 구조화된 단계 이벤트."""

from contextvars import ContextVar
import re


stage_output = ContextVar('worker_stage_output', default=None)
_STAGES = (
    'worker_request_received', 'embedding_worker', 'embedding_model_load',
    'embedding_library_import', 'embedding_model_construct', 'embedding_memory_cache_hit',
    'embedding_encode', 'worker_result_send',
)
_FIELDS = {'request_id', 'span_id', 'stage', 'event', 'elapsed_ms', 'error_code'}


def valid_diagnostic_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def valid_worker_stage(record):
    if not isinstance(record, dict) or set(record) != _FIELDS or record['stage'] not in _STAGES:
        return False
    if not all(valid_diagnostic_id(record[key]) for key in ('request_id', 'span_id')):
        return False
    elapsed = record['elapsed_ms']
    if type(elapsed) not in (int, float) or not 0 <= elapsed <= 1e15:
        return False
    event, code = record['event'], record['error_code']
    if event == 'error':
        return code in ('STAGE_FAILED', 'CANCELLED')
    return code is None and (event == 'end' or (event == 'start' and elapsed == 0))
