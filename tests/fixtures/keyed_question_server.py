"""제품 MCP/실행 서비스를 사용하되 검색·생성·장애만 합성 대역으로 주입한다."""

import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.domain.evidence_qa import SearchResult, SearchScope, RetrievalMethod, RetrievedChunk
from solo_leveling.infrastructure.database import repository
from solo_leveling.infrastructure.generation.ask_execution_service import AskExecutionService
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.interfaces.mcp.server import create_server, MCPServices


db = os.environ['PROBE_DB']


def count(kind):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('INSERT INTO probe_calls VALUES (?)', (kind,))


def search(question, **scope):
    count('search')
    context = repository.get_learning_context(db, scope['context_id'])
    chunks = repository.get_translated_chunks(db, context.parse_revision_id, context.translation_revision_id)
    result = SearchResult(question, SearchScope(context.version_id, context.parse_revision_id,
        context.translation_revision_id, scope['pdf_pages']), RetrievalMethod.VECTOR,
        context.embedding_set_id, tuple(RetrievedChunk(c, 1.0, i+1) for i, c in enumerate(chunks)))
    return ContextSearchResponse(context.context_id, result)


class Generator(FakeClaimGenerator):
    def generate_claims(self, question, evidence):
        count('generation')
        return super().generate_claims(question, evidence)


class InjectedExecution(AskExecutionService):
    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        if os.environ.get('PROBE_DROP_RESPONSE') == '1':
            # 전체 DB 커밋 후 최초 응답만 유실시킨다. 제품 저장 경로는 바꾸지 않는다.
            raise RuntimeError('PRIVATE_RESPONSE_LOSS')
        return result

    def lookup(self, *args, **kwargs):
        if os.environ.pop('PROBE_LOOKUP_FAILURE', None) == '1':
            raise sqlite3.OperationalError('PRIVATE_DATABASE_ERROR')
        return super().lookup(*args, **kwargs)


answer = AnswerService(SimpleNamespace(search=search), Generator(), None)
create_server(MCPServices(None, None, executions=InjectedExecution(db, answer))).run(transport='stdio')
