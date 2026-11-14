"""기존 맥락에 고정한 재파싱 안내·동의·조회와 명시적 새 맥락 선택."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from functools import wraps
from importlib.resources import files
import json
import sqlite3
from threading import Condition, RLock
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from solo_leveling.domain.models import now_iso
from solo_leveling.infrastructure.database import repository as repo
from .registration_consent import RegistrationGate


URI = 'ui://solo-leveling/paper-reparse.html'
NOTICE = ('관리 PDF를 다시 파싱하고 새 청크를 번역·임베딩합니다. 동의 후 백그라운드에서 원문 청크와 '
          '대상 언어를 번역 제공자에게 전송하며, 합치면 논문 전체가 될 수 있습니다. '
          '비용과 시간은 분량과 설정에 따라 달라집니다. 기존 맥락·인용·메모는 그대로 유지됩니다. '
          '동의로 외부 전송 차단 설정을 해제할 수 없습니다.')


class ReparseManager:
    def __init__(self, service):
        self.service = service
        self.db = service.db_path
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paper-reparse')
        self.condition = Condition(RLock())
        self.pending = set()
        self.accepting = True

    def _context(self, context_id):
        context = repo.get_learning_context(self.db, context_id)
        if not context or not context.embedding_set_id:
            raise ToolError('REPARSE_CONTEXT_UNAVAILABLE: 재파싱할 수 있는 학습 맥락을 찾을 수 없습니다.')
        return context

    def _args(self, context, key):
        return context.version_id, context.parse_revision_id, context.embedding_set_id, key

    def lookup(self, context_id, request_key):
        context = self._context(context_id)
        result = self.service.lookup(*self._args(context, request_key))
        return dict(context_id=context_id, request_key=request_key,
                    **(asdict(result) if result else {'status': 'NOT_FOUND'}))

    def preview(self, context_id, request_key, *, result=None):
        context = self._context(context_id)
        result = self.lookup(context_id, request_key) if result is None else result
        if result['status'] == 'NOT_FOUND':
            index = repo.get_search_index(self.db, context.version_id)
            if not index or (index['parse_revision_id'], index['embedding_set_id']) != (
                    context.parse_revision_id, context.embedding_set_id):
                result['status'] = 'BASE_CHANGED'
            else:
                result['status'] = 'AVAILABLE' if self.service.provider_available else 'BLOCKED'
        with closing(repo.get_connection(self.db)) as conn:
            name = conn.execute('SELECT original_filename FROM paper_versions WHERE version_id=?',
                                (context.version_id,)).fetchone()[0]
        return dict(result, filename=name, version_id=context.version_id,
                    base_parse_revision_id=context.parse_revision_id, base_embedding_set_id=context.embedding_set_id,
                    translation=asdict(self.service.settings), embedding_model=self.service.embedding_model, notice=NOTICE)

    def submit(self, context_id, request_key):
        with self.condition:
            if not self.accepting or len(self.pending) >= 16:
                raise ToolError('REPARSE_WORKER_UNAVAILABLE: 재파싱 작업을 지금 시작할 수 없습니다. 잠시 후 다시 시도하세요.')
            result = self.service.prepare(*self._args(self._context(context_id), request_key))
            if result.status == 'ACCEPTED':
                try:
                    future = self.pool.submit(self.service.run, result.job_id)
                except RuntimeError:
                    repo.fail_reparse(self.db, result.job_id, ['REPARSE_FAILED'])
                    return self.lookup(context_id, request_key)
                self.pending.add(future)
                future.add_done_callback(self._done)
            return dict(asdict(result), context_id=context_id, request_key=request_key)

    def _done(self, future):
        with self.condition:
            self.pending.discard(future)
            self.condition.notify_all()

    def begin_shutdown(self):
        with self.condition:
            self.accepting = False
            self.pool.shutdown(wait=False)

    def wait_for_idle(self, timeout):
        with self.condition:
            return self.condition.wait_for(lambda: not self.pending, timeout)

    def select(self, context_id, request_key, job_id):
        old = self._context(context_id)
        result = self.lookup(context_id, request_key)
        if result['status'] != 'COMPLETED' or not result['result_available'] or result['job_id'] != job_id:
            raise ToolError('REPARSE_RESULT_UNAVAILABLE: 선택할 수 있는 완료된 재파싱 결과가 없습니다.')
        # 같은 작업의 선택 응답이 유실돼도 같은 학습 맥락을 반환한다.
        new_id = str(uuid5(NAMESPACE_URL, json.dumps(['solo-leveling/reparse', context_id, job_id])))
        new = replace(old, created_at=now_iso(), context_id=new_id, parse_revision_id=result['new_parse_revision_id'],
                      translation_revision_id=result['translation_revision_id'], embedding_set_id=result['new_embedding_set_id'])
        try:
            repo.create_learning_context(self.db, new)
        except sqlite3.IntegrityError:
            saved = repo.get_learning_context(self.db, new_id)
            if not saved or replace(saved, created_at=new.created_at) != new:
                raise ToolError('REPARSE_CONTEXT_CONFLICT: 같은 작업으로 만든 학습 맥락이 다른 내용으로 저장되어 있습니다.') from None
        return {'context_id': new_id, 'embedding_set_id': new.embedding_set_id, 'job_id': job_id}


def _public_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ToolError:
            raise
        except Exception:
            raise ToolError('REPARSE_STATE_UNAVAILABLE: 재파싱 상태를 확인하지 못했습니다.') from None
    return wrapped


def register_reparse(server, manager):
    gate = RegistrationGate()
    app_only = {'ui': {'visibility': ['app']}}

    @server.resource(URI, mime_type='text/html;profile=mcp-app',
                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def reparse_card() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/paper-reparse.html').read_text('utf-8')

    def scope(data):
        return json.dumps([data[k] for k in ('context_id', 'version_id', 'base_parse_revision_id',
            'base_embedding_set_id', 'translation', 'embedding_model', 'status', 'notice')], sort_keys=True)

    @server.tool(meta={'ui': {'resourceUri': URI}}, annotations=ToolAnnotations(readOnlyHint=True))
    @_public_errors
    def prepare_paper_reparse(context_id: str, request_key: str) -> CallToolResult:
        """명시적 재파싱 요청에만 안내를 엽니다. 동의 전에는 접수하지 않습니다. 카드 표시 여부를 단정하지 마세요."""
        data = manager.preview(context_id, request_key)
        meta = {}
        if data['status'] == 'AVAILABLE':
            meta['confirmation_token'] = gate.prepare(scope(data), request_key)
        return CallToolResult(structuredContent=data, _meta=meta, content=[
            TextContent(type='text', text=json.dumps(data, ensure_ascii=False)),
            TextContent(type='text', text='준비 응답으로 카드 표시 여부를 판단할 수 없습니다. 카드가 보이면 직접 확인하세요.')])

    @server.tool(meta=app_only)
    @_public_errors
    def submit_paper_reparse(context_id: str, request_key: str, confirmation_token: str) -> dict[str, Any]:
        """동의 카드에서만 재파싱을 접수합니다."""
        data = manager.preview(context_id, request_key)
        try:
            gate.consume(confirmation_token, scope(data), request_key)
        except ToolError:
            raise ToolError('REPARSE_CONFIRMATION_REQUIRED: 재파싱 안내를 다시 확인하세요.') from None
        return manager.submit(context_id, request_key)

    @server.tool(meta=app_only)
    @_public_errors
    def cancel_paper_reparse(confirmation_token: str) -> dict[str, Any]:
        """미사용 재파싱 확인을 취소합니다."""
        gate.cancel(confirmation_token)
        return {'status': 'CANCELLED'}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @_public_errors
    def get_paper_reparse(context_id: str, request_key: str) -> dict[str, Any]:
        """응답 유실·재시작 시 같은 기존 맥락과 요청 키로 조회합니다. 새로운 작업을 시작하지 않습니다."""
        result = manager.lookup(context_id, request_key)
        if result['status'] != 'NOT_FOUND':
            return result
        data = manager.preview(context_id, request_key, result=result)
        if data['status'] == 'AVAILABLE' and gate.is_prepared(scope(data), request_key):
            return dict(context_id=context_id, request_key=request_key, status='PREPARED')
        raise ToolError('NOT_FOUND: 요청한 자료를 찾을 수 없습니다.')

    @server.tool(meta=app_only)
    @_public_errors
    def select_reparsed_context(context_id: str, request_key: str, job_id: str) -> dict[str, Any]:
        """사용자가 선택한 완료 작업의 게시 색인으로 학습 맥락을 엽니다."""
        return manager.select(context_id, request_key, job_id)
