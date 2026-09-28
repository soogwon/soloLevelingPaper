"""호출 계층에서 문자열 비교 없이 구분하는 서비스 오류."""


class InvalidArgumentError(ValueError):
    """사용자 입력의 형식이나 조합이 올바르지 않다."""


class ResourceNotFoundError(ValueError):
    """요청한 맥락·논문 버전·근거를 찾을 수 없다."""


class DataIntegrityError(ValueError):
    """저장 데이터나 내부 서비스 결과가 계약과 일치하지 않는다."""
