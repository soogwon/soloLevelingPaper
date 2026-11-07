"""Desktop 호출자가 제출 전에 보관한 키를 학습 맥락별로 복원한다."""
import sqlite3
from contextlib import closing
from importlib.resources import files
from pathlib import Path
from uuid import uuid4
from typing_extensions import TypedDict

from mcp.types import ToolAnnotations
from solo_leveling.domain.ask_execution import KEY_RETENTION_DAYS
from .errors import to_tool_error

CARD_URI = 'ui://solo-leveling/question-card.html'


class CardContext(TypedDict):
    context_id: str


class ReservedKey(TypedDict):
    request_key: str


class SavedKey(ReservedKey):
    created_at: int


class CardRequests(TypedDict):
    requests: list[SavedKey]


class QuestionKeys:
    def __init__(self, path: Path):
        self.path = path

    def reserve(self, context_id: str) -> dict:
        key = str(uuid4())
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS requests
                (request_key TEXT PRIMARY KEY, context_id TEXT NOT NULL, created_at INTEGER NOT NULL)""")
            conn.execute("DELETE FROM requests WHERE created_at <= unixepoch() - ?", (KEY_RETENTION_DAYS * 86400,))
            conn.execute("INSERT INTO requests VALUES (?, ?, unixepoch())", (key, context_id))
        return {'request_key': key}

    def recent(self, context_id: str) -> list[dict]:
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as conn:
            rows = conn.execute("""SELECT request_key, created_at FROM requests WHERE context_id=?
                AND created_at > unixepoch() - ? ORDER BY created_at DESC, rowid DESC""",
                (context_id, KEY_RETENTION_DAYS * 86400))
            return [dict(request_key=key, created_at=created) for key, created in rows]


def register_question_card(server, keys):
    @server.resource(CARD_URI, name='question-card', title='논문 질문과 결과 복원',
                     mime_type='text/html;profile=mcp-app',
                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def question_card_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/question-card.html').read_text(encoding='utf-8')

    @server.tool(structured_output=True, meta={'ui': {'resourceUri': CARD_URI}},
                 annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def open_paper_question_card(context_id: str) -> CardContext:
        """해당 학습 맥락의 질문 카드를 엽니다. 재시작 후에도 같은 context_id로 열어 기존 결과를 조회하세요."""
        return {'context_id': context_id}

    @server.tool(structured_output=True, meta={'ui': {'visibility': ['app']}},
                 annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def list_card_question_keys(context_id: str) -> CardRequests:
        """이 카드에서 보관한 요청 키를 최근 순으로 읽습니다."""
        try:
            return {'requests': keys.recent(context_id)}
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(structured_output=True, meta={'ui': {'visibility': ['app']}},
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def reserve_card_question_key(context_id: str) -> ReservedKey:
        """사용자가 새 질문을 제출할 때 실행 전에 키 하나를 로컬에 저장합니다."""
        try:
            return keys.reserve(context_id)
        except Exception as error:
            raise to_tool_error(error) from None
