"""채팅 요약의 출처를 검증하고 인증 웹에서 확인한 결과만 노출한다."""

from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

from .remote_executions import RemoteExecutionConflict
from .remote_retrieval import PostgresRemoteContextResolver

KINDS = ('claim', 'method', 'result')


def normalize_items(items):
    if type(items) is not list or not 1 <= len(items) <= 3:
        raise ValueError('chat_summary_invalid')
    by_kind = {}
    for item in items:
        kind, sentence, ids = item['kind'], item['text'], item['chunk_ids']
        if (kind not in KINDS or kind in by_kind or not isinstance(sentence, str)
                or not 1 <= len(sentence.strip()) <= 240 or len(sentence.splitlines()) != 1
                or type(ids) is not list or not 1 <= len(ids) <= 4):
            raise ValueError('chat_summary_invalid')
        chunks = [str(UUID(value)) for value in ids]
        if len(set(chunks)) != len(chunks):
            raise ValueError('chat_summary_invalid')
        by_kind[kind] = dict(kind=kind, text=sentence.strip(), evidence_ids=chunks)
    return [by_kind[kind] for kind in KINDS if kind in by_kind]


class PostgresChatSummaries:
    def __init__(self, pool, *, web_origin, review_seconds):
        self.pool, self.origin, self.seconds = pool, web_origin, review_seconds

    def prepare(self, owner, key, context, items):
        key, context = str(UUID(key)), str(UUID(context))
        normalized = normalize_items(items)
        with self.pool.connection() as conn:
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            binding = PostgresRemoteContextResolver(scoped).resolve(owner, context)
            if binding is None:
                return None
            ids = sorted({chunk for item in normalized for chunk in item['evidence_ids']})
            rows = conn.execute("""SELECT chunk_id,pdf_page FROM server_processing_chunks
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                  AND parse_revision_id=%s AND chunk_id=ANY(%s::uuid[])""",
                (owner, binding.paper_id, binding.server_version_id, binding.parse_revision_id, ids)).fetchall()
            pages = {str(row['chunk_id']): row['pdf_page'] for row in rows}
            if set(pages) != set(ids):
                raise ValueError('chat_summary_evidence_invalid')
            snapshot = dict(binding=asdict(binding), items=normalized, evidence_pages=pages)
            conn.execute("""INSERT INTO server_chat_summaries(owner_user_id,request_key,context_id,snapshot)
                VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (owner, key, context, json.dumps(snapshot)))
            row = self._row(conn, owner, key)
            if row is None:
                return None
            if row['snapshot'] != snapshot:
                raise RemoteExecutionConflict('chat_summary_conflict')
        return dict(status='review_required', request_key=key,
                    review_url=f'{self.origin}/#/chat-summaries/{key}', items=normalized, evidence_pages=pages)

    def _row(self, conn, owner, key, *, save_lock=False, paper_lock=False):
        return conn.execute("""SELECT s.*, clock_timestamp() < s.created_at + make_interval(secs=>%s) AS active
            FROM server_chat_summaries s JOIN server_execution_contexts c USING(owner_user_id,context_id)
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE s.owner_user_id=%s AND s.request_key=%s AND p.deleted_at IS NULL"""
            + (' FOR UPDATE OF s FOR SHARE OF p' if save_lock else ' FOR SHARE OF p' if paper_lock else ''),
            (self.seconds, owner, str(UUID(key)))).fetchone()

    def _view(self, row):
        return dict(request_key=str(row['request_key']), context_id=str(row['context_id']),
                    active=row['active'], saved=row['saved_at'] is not None, **row['snapshot'])

    def review(self, owner, key):
        with self.pool.connection() as conn:
            row = self._row(conn, owner, key)
            return self._view(row) if row is not None else None

    def save(self, owner, key):
        with self.pool.connection() as conn:
            row = self._row(conn, owner, key, save_lock=True)
            if row is None:
                return None
            if row['saved_at'] is None:
                # 행 잠금을 기다리는 중에도 검토 시간이 끝날 수 있다.
                row = conn.execute("""UPDATE server_chat_summaries SET saved_at=clock_timestamp()
                    WHERE owner_user_id=%s AND request_key=%s
                      AND clock_timestamp() < created_at + make_interval(secs=>%s)
                    RETURNING *,true AS active""",
                    (owner, str(UUID(key)), self.seconds)).fetchone()
                if row is None:
                    raise RemoteExecutionConflict('chat_summary_expired')
            return self._view(row)

    def _completed(self, row):
        if row is None or row['saved_at'] is None:
            return None
        snapshot = row['snapshot']
        context, key = str(row['context_id']), str(row['request_key'])
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        result = dict(snapshot, context_id=context, scope='paper', section_id=None,
                      model='미기록', provider='Claude 채팅', language='ko',
                      prompt_version='remote-chat-summary-v1', input_hash=digest,
                      generated_at=row['saved_at'].isoformat())
        return dict(state='completed', context_id=context, request_key=key, result=result,
                    evidence_pages=result['evidence_pages'])

    def get(self, owner, key):
        with self.pool.connection() as conn:
            return self._completed(self._row(conn, owner, key))

    def evidence(self, owner, key, evidence_id):
        evidence_id = str(UUID(evidence_id))
        with self.pool.connection() as conn:
            saved = self._completed(self._row(conn, owner, key, paper_lock=True))
            if saved is None or evidence_id not in saved['evidence_pages']:
                return None
            binding = saved['result']['binding']
            row = conn.execute("""SELECT original_text, pdf_page, section_id FROM server_processing_chunks
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                  AND parse_revision_id=%s AND chunk_id=%s""",
                (owner, binding['paper_id'], binding['server_version_id'],
                 binding['parse_revision_id'], evidence_id)).fetchone()
            if row is None:
                return None
        return dict(row, context_id=saved['context_id'], request_key=saved['request_key'],
                    evidence_id=evidence_id, chunk_id=evidence_id, paper_id=binding['paper_id'],
                    version_id=binding['server_version_id'], publication_id=binding['publication_id'],
                    parse_revision_id=binding['parse_revision_id'])

    def latest(self, owner, context, generated):
        with self.pool.connection() as conn:
            row = conn.execute("""SELECT s.* FROM server_chat_summaries s
                JOIN server_execution_contexts c USING(owner_user_id,context_id)
                JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE s.owner_user_id=%s AND s.context_id=%s AND s.saved_at IS NOT NULL AND p.deleted_at IS NULL
                ORDER BY s.saved_at DESC,s.request_key DESC LIMIT 1""", (owner, str(UUID(context)))).fetchone()
        if row is None or (generated and row['saved_at'] < datetime.fromisoformat(generated['result']['generated_at'])):
            return generated
        return self._completed(row)
