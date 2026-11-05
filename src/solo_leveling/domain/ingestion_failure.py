"""등록 처리 상태의 실패 원인·재시도·다음 행동 계약(#60). 값은 A가 계산하고 MCP는 그대로 전달한다.

응답에 항상 들어가는 다섯 키: failed_stage, failure_code, retryable, next_action, outcome.
값이 없으면 JSON null이다. retryable=null(제공되는 방법 없음 또는 판단 안 함)과 false(허용 안 됨)는 다르다.

- retryable: 같은 입력으로 새 작업을 시작하는 것이 허용되는가(성공 보장 아님).
- next_action: 사용자가 취할 수 있는 실제 행동 하나(register_again | replace_file). 없거나 미확정이면 null.
- outcome: complete | partial | failed. queued·processing은 null.
- 알 수 없는 limitations 문자열은 failure_code로 쓰지 않고 fallback(ingestion_failed / interrupted)으로 처리한다.
  예외 메시지·경로·원문은 담지 않는다.
- failure_code는 아래 고정 코드와 TranslationFailureCode에 정의된 번역 실패 코드뿐이다(미등록 코드나 형식이 맞지 않는 항목은 translation_failed). empty_pages 같은 품질 경고는 failure_code도 outcome도 바꾸지 않는다.
"""
import re
from typing import Optional

from solo_leveling.domain.translation import TranslationFailureCode

KEYS = ('failed_stage', 'failure_code', 'retryable', 'next_action', 'outcome')

REGISTER_AGAIN = 'register_again'
REPLACE_FILE = 'replace_file'

# 코드 → (failed_stage, retryable, next_action). failed_stage가 None이면 작업 단계(job.stage)를 쓴다.
FAILED_CODES = {
    'pdf_encrypted': ('parse', False, REPLACE_FILE),
    'pdf_unreadable': ('parse', False, REPLACE_FILE),
    'extraction_empty': ('parse', False, REPLACE_FILE),
    'translation_failed': ('translate', True, REGISTER_AGAIN),
    'EMBEDDING_TIMEOUT': ('index', True, REGISTER_AGAIN),
    'EMBEDDING_UNAVAILABLE': ('index', True, REGISTER_AGAIN),
    'EMBEDDING_INVALID_RESULT': ('index', False, None),
}
FALLBACK_FAILED = 'ingestion_failed'
INTERRUPT_CODES = ('SERVER_SHUTTING_DOWN',)
FALLBACK_INTERRUPTED = 'interrupted'
_PARTIAL_PREFIX = 'translation_failed:'


def _empty(outcome: Optional[str] = None) -> dict:
    return {'failed_stage': None, 'failure_code': None, 'retryable': None, 'next_action': None, 'outcome': outcome}


_TRANSLATION_CODES = frozenset(code.value for code in TranslationFailureCode)


_PARTIAL_RECORD = re.compile(r'translation_failed:([A-Za-z0-9_-]{1,64}):([A-Za-z0-9_]{1,64})')


def _first_partial_code(limitations) -> Optional[str]:
    """첫 번째 'translation_failed:<chunk_id>:<code>' 항목의 코드. 항목이 없으면 None.

    기록 형식을 엄격히 검사한다: 필드는 정확히 세 개이고(접두어, 청크 ID, 코드), 청크 ID는 영문·숫자·밑줄·하이픈 1~64자,
    코드는 TranslationFailureCode에 정의된 값이어야 한다. 하나라도 어긋나면(필드 수가 다르거나, 청크 ID에 콜론·공백이
    있거나, 미등록 코드이거나) 값을 그대로 내보내지 않고 고정 코드 'translation_failed'로 대체한다(#60, #180).
    """
    for item in limitations:
        if isinstance(item, str) and item.startswith(_PARTIAL_PREFIX):
            match = _PARTIAL_RECORD.fullmatch(item)
            return match[2] if match and match[2] in _TRANSLATION_CODES else 'translation_failed'
    return None


def describe_outcome(status: str, stage: Optional[str], limitations) -> dict:
    """작업 상태·단계·limitations로 다섯 키를 계산한다."""
    limitations = list(limitations or [])
    if status == 'ready':
        code = _first_partial_code(limitations)
        if code is None:
            return _empty('complete')
        return {'failed_stage': 'translate', 'failure_code': code, 'retryable': None,
                'next_action': None, 'outcome': 'partial'}
    if status == 'failed':
        code = next((i for i in limitations if i in FAILED_CODES), FALLBACK_FAILED)
        fixed_stage, retryable, action = FAILED_CODES.get(code, (None, True, REGISTER_AGAIN))
        return {'failed_stage': fixed_stage or stage, 'failure_code': code, 'retryable': retryable,
                'next_action': action, 'outcome': 'failed'}
    if status == 'interrupted':
        code = next((i for i in limitations if i in INTERRUPT_CODES), FALLBACK_INTERRUPTED)
        return {'failed_stage': stage, 'failure_code': code, 'retryable': True,
                'next_action': REGISTER_AGAIN, 'outcome': 'failed'}
    return _empty()  # queued·processing 등 아직 끝나지 않은 상태
