"""추출 서비스의 원문 읽기와 A의 저장 계약 연결. 원문·번역을 로그에 쓰지 않는다."""
from contextlib import closing
from pathlib import Path
import sqlite3

from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError
from solo_leveling.application.paper_insights.service import InsightChunk, InsightSource
from solo_leveling.infrastructure.database import repository as repo


class SQLiteInsightStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def load(self, context_id: str, scope: str) -> InsightSource:
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            context = conn.execute('SELECT * FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone()
            if context is None:
                raise ResourceNotFoundError('학습 맥락을 찾지 못했습니다.')
            linked = conn.execute('''SELECT 1 FROM parse_revisions p
                JOIN translation_revisions t ON t.parse_revision_id=p.parse_revision_id
                JOIN search_indexes s ON s.version_id=p.version_id AND s.parse_revision_id=p.parse_revision_id
                    AND s.translation_revision_id=t.translation_revision_id
                WHERE p.version_id=? AND p.parse_revision_id=? AND t.translation_revision_id=? AND s.embedding_set_id=?''',
                (context['version_id'], context['parse_revision_id'], context['translation_revision_id'], context['embedding_set_id'])).fetchone()
            if linked is None:
                raise DataIntegrityError('학습 맥락의 출처 연결이 일치하지 않습니다.')
            sql, args = 'SELECT * FROM chunks WHERE parse_revision_id=?', [context['parse_revision_id']]
            if scope != 'paper':
                sql += ' AND section_id=?'
                args.append(scope)
            rows = conn.execute(sql + ' ORDER BY chunk_index, chunk_id', args).fetchall()
            if any(not isinstance(row['original_text'], str) for row in rows):
                raise DataIntegrityError('저장된 원문 형식이 올바르지 않습니다.')
            if scope != 'paper' and not rows:
                raise ResourceNotFoundError('요청한 섹션을 찾지 못했습니다.')
            titles = ()
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sections'").fetchone():
                titles = tuple((row['section_id'], row['title']) for row in conn.execute(
                    'SELECT section_id,title FROM sections WHERE parse_revision_id=? ORDER BY ordinal,section_id', (context['parse_revision_id'],)))
            chunks = tuple(InsightChunk(row['chunk_id'], row['parse_revision_id'], row['chunk_index'],
                row['original_text'], pdf_page=row['pdf_page'], section_id=row['section_id']) for row in rows)
            existing = conn.execute('SELECT collection_id FROM paper_insight_collections WHERE context_id=? AND scope=?',
                                    (context_id, scope)).fetchone()
            return InsightSource(context_id, context['version_id'], context['parse_revision_id'],
                context['translation_revision_id'], context['embedding_set_id'], scope, chunks, titles,
                existing['collection_id'] if existing else None)

    def get(self, context_id, scope):
        return repo.get_paper_insights(self.db_path, context_id, scope)

    def save(self, source, items, *, expected_collection_id):
        return repo.save_paper_insights(self.db_path, source.context_id, source.scope, list(items),
            expected_collection_id=expected_collection_id,
            expected_context=(source.version_id, source.parse_revision_id,
                              source.translation_revision_id, source.embedding_set_id))
