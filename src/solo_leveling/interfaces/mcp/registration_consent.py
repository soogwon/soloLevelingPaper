"""등록 안내 카드에 연결된 일회성 확인 요청을 관리한다."""

from importlib.resources import files
from time import time

from mcp.server.fastmcp.exceptions import ToolError

from .confirmations import ConfirmationLimitExceeded, ConfirmationStore

REGISTRATION_APP_URI = 'ui://solo-leveling/registration.html'
REGISTRATION_NOTICE = """외부 API 호출을 허용한 경우, 등록 후 백그라운드 번역 단계에서 논문 원문 청크와 대상 언어를 OpenAI로 보냅니다.
여러 청크를 합하면 논문 전체 원문이 전송될 수 있으며 API 사용 비용이 발생할 수 있습니다.
동의해도 기존 LOCAL_ONLY 또는 외부 API 차단 설정은 그대로 적용됩니다.
취소하거나 동의하지 않으면 파일 복사와 등록 작업을 시작하지 않습니다."""


class RegistrationGate:
    """등록·재처리·복구 카드의 일회성 확인 토큰.

    store를 넘기면 같은 데이터 폴더를 쓰는 모든 MCP 프로세스가 토큰을 공유한다.
    """

    def __init__(self, *, clock=time, store: ConfirmationStore | None = None,
                 kind: str = 'registration'):
        self.store = store if store is not None else ConfirmationStore(clock=clock)
        self.kind = kind

    @property
    def pending(self) -> list[None]:
        """만료되지 않은 대기 토큰 수 확인용(내용은 노출하지 않는다)."""
        return [None] * self.store.pending_count(self.kind)

    def prepare(self, path: str, request_key: str) -> str:
        try:
            return self.store.issue(self.kind, [path, request_key])
        except ConfirmationLimitExceeded:
            raise ToolError('REGISTRATION_LIMIT: 잠시 후 등록 안내를 다시 요청하세요.') from None

    def is_prepared(self, path: str, request_key: str) -> bool:
        return self.store.has_pending(self.kind, [path, request_key])

    def consume(self, token: str, path: str, request_key: str) -> None:
        accepted, _ = self.store.take(self.kind, token, [path, request_key])
        if not accepted:
            raise ToolError('REGISTRATION_CONFIRMATION_REQUIRED: 등록 안내 카드를 다시 열어 확인하세요.')

    def cancel(self, token: str) -> None:
        self.store.cancel(self.kind, token)


def register_registration_app(server):
    @server.resource(
        REGISTRATION_APP_URI, name='registration', title='논문 등록 전 외부 전송 확인',
        mime_type='text/html;profile=mcp-app',
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}},
    )
    def registration_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath(
            'assets/registration.html').read_text(encoding='utf-8')
