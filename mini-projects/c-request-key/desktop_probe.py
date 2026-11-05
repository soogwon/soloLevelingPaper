"""#119 합성 요청 한 건만 지원하는 Desktop 호출자 보관·재조회 실험."""
import json
import os
import sqlite3
import sys
from pathlib import Path
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP
from solo_leveling.domain.ask_execution import ExecutionOutcome
from solo_leveling.infrastructure.database import ask_executions
from tests.unit.test_learning_persistence import setup_db, publish

root = Path(sys.argv[1]).resolve()
db = str(root / 'data.sqlite')
journal = root / 'caller.json'
context_id = 'ctx'
uri = 'ui://solo119-probe/recovery.html'
server = FastMCP('solo119-probe')


def submit():
    # 단일 요청 실험이므로 기존 기록은 덮어쓰지 않는다. 기록 실패 시 접수하지 않는다.
    key = str(uuid4())
    try:
        with journal.open('x', encoding='utf-8') as target:
            json.dump([context_id, key], target)
            target.flush()
            os.fsync(target.fileno())
    except OSError:
        return '보관 실패 또는 기존 요청 있음'
    execution = ask_executions.begin_execution(db, key, 'synthetic-input', context_id)
    assert execution.outcome is ExecutionOutcome.STARTED
    saved = ask_executions.complete_execution(db, key, execution.owner_token, '{"synthetic":true}')
    if saved.outcome is not ExecutionOutcome.SAVED:
        raise RuntimeError('합성 결과 저장 실패')
    # 실제 결과를 반환하지 않아 카드가 결과를 얻으려면 다시 조회해야 한다.
    raise RuntimeError('합성 접수 응답 유실')


def restore():
    try:
        stored = json.loads(journal.read_text(encoding='utf-8'))
        if (not isinstance(stored, list) or len(stored) != 2
                or not all(isinstance(value, str) for value in stored)):
            return '키 복원 실패'
        context, key = stored
        if context != context_id or str(UUID(key)) != key:
            return '키 복원 실패'
    except (OSError, ValueError, TypeError):
        return '키 복원 실패'
    try:
        view = ask_executions.lookup_execution(db, key)
    except sqlite3.Error:
        return '상태 확인 불가'
    if view.context_id != context_id:
        return '상태 확인 불가'
    return '저장된 결과 복원' if view.result_json == '{"synthetic":true}' else view.state.value


@server.resource(uri, mime_type='text/html;profile=mcp-app')
def recovery_card() -> str:
    return (root / 'card.html').read_text(encoding='utf-8')


@server.tool(meta={'ui': {'resourceUri': uri}})
def show_recovery_probe() -> str:
    """합성 요청을 명시적으로 제출하거나 저장된 결과를 조회하는 카드를 연다."""
    return '복구 실험 카드'


@server.tool(meta={'ui': {'visibility': ['app']}})
def probe_action(action: str) -> str:
    if action == 'submit':
        return submit()
    if action == 'restore':
        return restore()
    return '지원하지 않는 동작'


if __name__ == '__main__':
    if sys.argv[2:] == ['init']:
        database, chunks = setup_db.__wrapped__(root)
        publish(database, chunks)
    else:
        server.run(transport='stdio')
