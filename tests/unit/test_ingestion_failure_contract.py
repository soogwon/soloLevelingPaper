"""등록 처리 상태 계약(#60) 표를 그대로 검증한다: 다섯 키 항상 포함, null 구분, 코드→단계·재시도·행동 매핑."""
import pytest

from solo_leveling.domain.ingestion_failure import KEYS, describe_outcome

CHUNK = '5f0c4a52-1111-4222-8333-444455556666'


@pytest.mark.parametrize('status,stage,limitations,expected', [
    # ready
    ('ready', 'index', [], (None, None, None, None, 'complete')),
    ('ready', 'index', ['empty_pages:2,5'], (None, None, None, None, 'complete')),  # 품질 경고는 outcome을 바꾸지 않는다
    ('ready', 'index', [f'translation_failed:{CHUNK}:translation_timeout'],
     ('translate', 'translation_timeout', None, None, 'partial')),
    ('ready', 'index', ['empty_pages:3', f'translation_failed:{CHUNK}:a_code', f'translation_failed:{CHUNK}2:b_code'],
     ('translate', 'a_code', None, None, 'partial')),  # 첫 번째 코드가 대표
    ('ready', 'index', ['translation_failed:broken'], ('translate', 'translation_failed', None, None, 'partial')),
    # failed: 파싱
    ('failed', 'parse', ['pdf_encrypted'], ('parse', 'pdf_encrypted', False, 'replace_file', 'failed')),
    ('failed', 'parse', ['pdf_unreadable'], ('parse', 'pdf_unreadable', False, 'replace_file', 'failed')),
    ('failed', 'download', ['extraction_empty'], ('parse', 'extraction_empty', False, 'replace_file', 'failed')),
    # failed: 번역·색인
    ('failed', 'translate', ['translation_failed', f'translation_failed:{CHUNK}:x'],
     ('translate', 'translation_failed', True, 'register_again', 'failed')),
    ('failed', 'index', ['EMBEDDING_TIMEOUT'], ('index', 'EMBEDDING_TIMEOUT', True, 'register_again', 'failed')),
    ('failed', 'index', ['EMBEDDING_UNAVAILABLE'], ('index', 'EMBEDDING_UNAVAILABLE', True, 'register_again', 'failed')),
    ('failed', 'index', ['EMBEDDING_INVALID_RESULT'], ('index', 'EMBEDDING_INVALID_RESULT', False, None, 'failed')),
    ('failed', 'index', ['index_cleanup_failed', 'EMBEDDING_TIMEOUT'],
     ('index', 'EMBEDDING_TIMEOUT', True, 'register_again', 'failed')),  # 정리 실패 표식이 앞에 와도 원인 코드를 찾는다
    # failed: 미분류 → fallback(단계는 작업 단계)
    ('failed', 'index', ['ingestion_failed'], ('index', 'ingestion_failed', True, 'register_again', 'failed')),
    ('failed', 'translate', ['something_unknown'], ('translate', 'ingestion_failed', True, 'register_again', 'failed')),
    ('failed', 'parse', [], ('parse', 'ingestion_failed', True, 'register_again', 'failed')),
    # interrupted
    ('interrupted', 'parse', ['SERVER_SHUTTING_DOWN'], ('parse', 'SERVER_SHUTTING_DOWN', True, 'register_again', 'failed')),
    ('interrupted', 'translate', [], ('translate', 'interrupted', True, 'register_again', 'failed')),
    ('interrupted', 'index', ['unknown_text'], ('index', 'interrupted', True, 'register_again', 'failed')),
    # 아직 끝나지 않음
    ('queued', None, [], (None, None, None, None, None)),
    ('processing', 'translate', [], (None, None, None, None, None)),
])
def test_contract_table(status, stage, limitations, expected):
    result = describe_outcome(status, stage, limitations)
    assert tuple(result) == KEYS  # 다섯 키가 항상 이 순서로 모두 있다
    assert tuple(result.values()) == expected


def test_retryable_null_is_distinct_from_false():
    partial = describe_outcome('ready', 'index', [f'translation_failed:{CHUNK}:c'])
    replace = describe_outcome('failed', 'parse', ['pdf_encrypted'])
    assert partial['retryable'] is None and replace['retryable'] is False


def test_unknown_limitation_strings_are_never_echoed_as_failure_code():
    secret = 'C:/Users/private/paper.pdf: secret detail'
    result = describe_outcome('failed', 'index', [secret])
    assert result['failure_code'] == 'ingestion_failed' and secret not in str(result)
    assert secret not in str(describe_outcome('interrupted', 'index', [secret]))


def test_partial_with_non_identifier_code_does_not_leak_text():
    result = describe_outcome('ready', 'index', [f'translation_failed:{CHUNK}:bad code with spaces'])
    assert result['failure_code'] == 'translation_failed' and 'spaces' not in str(result)


def test_none_and_tuple_limitations_are_accepted():
    assert describe_outcome('ready', None, None)['outcome'] == 'complete'
    assert describe_outcome('failed', 'parse', ('pdf_encrypted',))['next_action'] == 'replace_file'
