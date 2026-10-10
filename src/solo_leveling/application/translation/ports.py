"""제공자는 한 번 호출하고 서비스가 안전한 분류에 따라 재시도한다."""

from enum import Enum
import math
from typing import Protocol

from solo_leveling.domain.translation import TranslationRequest, TranslationSettings


class ProviderFailure(str, Enum):
    UNKNOWN = 'unknown'
    TIMEOUT = 'timeout'
    CONNECTION = 'connection'
    RATE_LIMIT = 'rate_limit'
    SERVER = 'server'
    AUTHENTICATION = 'authentication'
    PERMISSION = 'permission'
    QUOTA = 'quota'
    INVALID_REQUEST = 'invalid_request'
    DISABLED = 'disabled'
    REFUSAL = 'refusal'
    INCOMPLETE = 'incomplete'
    INVALID_RESPONSE = 'invalid_response'


class TranslationProviderUnavailable(Exception):
    """기존 생성 방식은 허용하되 외부 메시지는 보관하거나 출력하지 않는다."""

    def __init__(self, message: str = '', *, code: ProviderFailure = ProviderFailure.UNKNOWN,
                 retry_after_seconds: float | None = None):
        if not isinstance(code, ProviderFailure):
            raise ValueError('허용된 번역 오류 코드가 필요합니다.')
        if retry_after_seconds is not None and (
            type(retry_after_seconds) not in (int, float)
            or not math.isfinite(retry_after_seconds) or retry_after_seconds < 0
        ):
            raise ValueError('재시도 대기 시간은 유한한 음이 아닌 수여야 합니다.')
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code.value)

    @property
    def retryable(self) -> bool:
        return self.code in (ProviderFailure.TIMEOUT, ProviderFailure.CONNECTION,
                             ProviderFailure.RATE_LIMIT, ProviderFailure.SERVER)


class TranslationProvider(Protocol):
    def translate(self, request: TranslationRequest, settings: TranslationSettings) -> str:
        """Return translated text or raise TranslationProviderUnavailable.

        Unexpected exceptions and non-string responses are programming/adapter
        errors and must propagate rather than being hidden as per-chunk failures.
        """
        ...
