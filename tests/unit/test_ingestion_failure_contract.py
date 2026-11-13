"""등록 처리 상태 계약(#60) 표를 그대로 검증한다: 다섯 키 항상 포함, null 구분, 코드→단계·재시도·행동 매핑."""
import pytest

from solo_leveling.domain.ingestion_failure import KEYS, describe_outcome

CHUNK = '5f0c4a52-1111-4222-8333-444455556666'


@pytest.mark.parametrize('status,stage,limitations,expected', [
    # ready
    ('ready', 'index', [], (None, None, None, None, 'complete')),
    ('ready', 'index', ['empty_pages:2,5'], (None, None, None, None, 'complete')),  # 품질 경고는 outcome을 바꾸지 않는다
    ('ready', 'index', [f'translation_failed:{CHUNK}:provider_unavailable'],
     ('translate', 'provider_unavailable', None, None, 'partial')),
    ('ready', 'index', ['empty_pages:3', f'translation_failed:{CHUNK}:empty_translation', f'translation_failed:{CHUNK}2:provider_unavailable'],
     ('translate', 'empty_translation', None, None, 'partial')),  # 첫 번째 코드가 대표
    # 미등록 값은 그대로 내보내지 않고 고정 코드로 대체한다(#60 B 리뷰). 첫 항목이 대표이며 뒤의 유효한 항목으로 건너뛰지 않는다.
    ('ready', 'index', ['translation_failed:abc:privateMarker123'],
     ('translate', 'translation_failed', None, None, 'partial')),
    ('ready', 'index', [f'translation_failed:{CHUNK}:privateMarker123', f'translation_failed:{CHUNK}2:empty_translation'],
     ('translate', 'translation_failed', None, None, 'partial')),
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
    partial = describe_outcome('ready', 'index', [f'translation_failed:{CHUNK}:empty_translation'])
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


def test_unregistered_translation_code_is_never_exposed():
    """B 리뷰 재현 입력: 문자 구성만 맞는 미등록 값이 failure_code로 그대로 나가면 안 된다."""
    result = describe_outcome('ready', 'index', ['translation_failed:c1:privateMarker123'])
    assert result == {'failed_stage': 'translate', 'failure_code': 'translation_failed', 'retryable': None,
                      'next_action': None, 'outcome': 'partial'}
    assert 'privateMarker123' not in str(result)


def test_only_defined_translation_failure_codes_pass_through():
    from solo_leveling.domain.translation import TranslationFailureCode

    for code in TranslationFailureCode:
        result = describe_outcome('ready', 'index', [f'translation_failed:c1:{code.value}'])
        assert result['failure_code'] == code.value and result['outcome'] == 'partial'
    for bad in ('PROVIDER_UNAVAILABLE', 'provider-unavailable', 'provider_unavailable ', '', 'a:b'):
        assert describe_outcome('ready', 'index', [f'translation_failed:c1:{bad}'])['failure_code'] == 'translation_failed'


# ── 부분 번역 실패 기록 검증(#180): 청크 ID·필드 수·코드를 엄격히 검사한다 ─────────────
@pytest.mark.parametrize('record', [
    f'translation_failed:{CHUNK}:provider_unavailable:extra',    # 필드가 네 개
    f'translation_failed:{CHUNK}:other:provider_unavailable',    # 마지막 필드만 유효한 코드
    'translation_failed::provider_unavailable',                  # 빈 청크 ID
    f'translation_failed:{CHUNK}:',                              # 빈 코드
    'translation_failed:a b:provider_unavailable',               # 청크 ID에 공백
    'translation_failed:C:/Users/x:provider_unavailable',        # 청크 ID에 경로 모양
    f'translation_failed:{CHUNK}:provider_unavailable ',         # 끝 공백
    f'translation_failed:{CHUNK}:provider_unavailable\n',        # 끝 줄바꿈
    f'translation_failed:{CHUNK}:Provider_Unavailable',          # 대소문자가 다른 코드
    f'translation_failed:{"x" * 65}:provider_unavailable',       # 너무 긴 청크 ID
    'translation_failed:한글청크:provider_unavailable',          # 허용 문자 밖
    'translation_failed:',                                       # 접두어만
])
def test_malformed_partial_record_is_replaced_by_the_fixed_code(record):
    result = describe_outcome('ready', 'index', [record])
    assert (result['outcome'], result['failure_code']) == ('partial', 'translation_failed')


def test_malformed_first_record_is_not_skipped_for_a_later_valid_one():
    result = describe_outcome('ready', 'index', [
        f'translation_failed:{CHUNK}:provider_unavailable:extra', f'translation_failed:{CHUNK}:empty_translation'])
    assert result['failure_code'] == 'translation_failed'


@pytest.mark.parametrize('code', [
    'provider_unavailable', 'empty_translation', 'unchanged_translation',
    'target_language_mismatch',
])
def test_well_formed_record_keeps_its_registered_code(code):
    assert describe_outcome('ready', 'index', [f'translation_failed:{CHUNK}:{code}'])['failure_code'] == code


@pytest.mark.parametrize('bad', [None, 5, b'translation_failed:x:provider_unavailable', ['translation_failed']])
def test_non_string_limitation_items_are_ignored(bad):
    assert describe_outcome('ready', 'index', [bad])['outcome'] == 'complete'
