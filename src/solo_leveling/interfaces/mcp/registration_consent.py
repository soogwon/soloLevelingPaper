"""등록 안내 카드에 연결된 일회성 확인 요청을 관리한다."""

from importlib.resources import files
from secrets import token_urlsafe
from time import monotonic

from mcp.server.fastmcp.exceptions import ToolError

REGISTRATION_APP_URI = 'ui://solo-leveling/registration.html'
REGISTRATION_NOTICE = """외부 API 호출을 허용한 경우, 등록 후 백그라운드 번역 단계에서 논문 원문 청크와 대상 언어를 OpenAI로 보냅니다.
여러 청크를 합하면 논문 전체 원문이 전송될 수 있으며 API 사용 비용이 발생할 수 있습니다.
동의해도 기존 LOCAL_ONLY 또는 외부 API 차단 설정은 그대로 적용됩니다.
취소하거나 동의하지 않으면 파일 복사와 등록 작업을 시작하지 않습니다."""


class RegistrationGate:
    def __init__(self, *, clock=monotonic):
        self.pending = {}
        self.clock = clock

    def prepare(self, path: str, request_key: str) -> str:
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value[2] > now}
        if len(self.pending) >= 128:
            raise ToolError('REGISTRATION_LIMIT: 잠시 후 등록 안내를 다시 요청하세요.')
        token = token_urlsafe(32)
        self.pending[token] = (path, request_key, now + 600)
        return token

    def is_prepared(self, path: str, request_key: str) -> bool:
        now = self.clock()
        return any(scope == path and key == request_key and expires > now
                   for scope, key, expires in self.pending.values())

    def consume(self, token: str, path: str, request_key: str) -> None:
        pending = self.pending.get(token)
        if pending is None or pending[2] <= self.clock() or pending[:2] != (path, request_key):
            raise ToolError('REGISTRATION_CONFIRMATION_REQUIRED: 등록 안내 카드를 다시 열어 확인하세요.')
        del self.pending[token]

    def cancel(self, token: str) -> None:
        self.pending.pop(token, None)


def register_registration_app(server):
    @server.resource(
        REGISTRATION_APP_URI, name='registration', title='논문 등록 전 외부 전송 확인',
        mime_type='text/html;profile=mcp-app',
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}},
    )
    def registration_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath(
            'assets/registration.html').read_text(encoding='utf-8')
