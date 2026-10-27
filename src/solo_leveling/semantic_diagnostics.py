"""본문 없이 기록할 의미 검증 이벤트의 고정 계약."""

from solo_leveling.domain.semantic_verification import SEMANTIC_REASON_RULES, AUDIT_CHECK_LOCATIONS


def basic_assessment(check, status):
    """기본 로그는 판정·최종 처리와 실패 체크만 보존한다."""
    return (check in ('claim.verdict', 'claim.final')
            or status in ('unsupported', 'missing', 'changed', 'partial', 'unanswered'))


def valid_assessment(record):
    """모델 문자열·식별자·임의 추가 필드가 기본 로그에 들어오지 않게 한다."""
    if set(record) != {'item_number', 'check_item', 'status', 'reason_code'}:
        return False
    number, check, status, reason = (record[k] for k in ('item_number', 'check_item', 'status', 'reason_code'))
    if type(number) is not int or not 1 <= number <= 10000:
        return False
    if not all(isinstance(v, str) for v in (check, status)):
        return False
    if check == 'claim.verdict':
        return any(status == verdict.value and reason in [r.value for r in reasons]
                   for verdict, reasons in SEMANTIC_REASON_RULES.items())
    if check == 'claim.final':
        return (status, reason) in (('retained', 'RETAINED'), ('excluded', 'SEMANTIC_NOT_SUPPORTED'),
                                   ('excluded', 'REPAIR_CHANGED'))
    if reason is not None:
        return False
    if check == 'repairs.overall':
        return status in ('preserved', 'partial', 'changed')
    if check == 'coverage.overall':
        return status in ('answered', 'partial', 'unanswered')
    if check not in AUDIT_CHECK_LOCATIONS:
        return False
    if check.startswith('original.'):
        return status in ('supported', 'unsupported', 'not_applicable')
    if check.startswith('repairs.'):
        return status in ('preserved', 'corrected', 'missing', 'changed', 'not_applicable')
    return status in ('satisfied', 'missing', 'not_applicable')
