"""내부 경로를 읽지 않는 PDF 출처 projection. 스키마·저장 함수는 변경하지 않는다."""
from contextlib import closing
from pathlib import Path
import sqlite3

from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError
from solo_leveling.domain.pdf_navigation import PdfSourceIdentity


class SQLitePdfSourceReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, context_id: str) -> PdfSourceIdentity:
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('BEGIN')
                context = conn.execute('SELECT version_id, parse_revision_id FROM learning_contexts '
                                       'WHERE context_id=?', (context_id,)).fetchone()
                if context is None:
                    raise ResourceNotFoundError('학습 맥락을 찾지 못했습니다.')
                row = conn.execute('''SELECT v.version_id, v.file_hash, p.parse_revision_id, p.page_count
                    FROM paper_versions v JOIN parse_revisions p ON p.version_id=v.version_id
                    WHERE v.version_id=? AND p.parse_revision_id=?''',
                    (context['version_id'], context['parse_revision_id'])).fetchone()
                if row is None:
                    raise DataIntegrityError('PDF 위치의 저장 출처 연결이 올바르지 않습니다.')
                return PdfSourceIdentity(context_id, row['version_id'], row['parse_revision_id'],
                                         row['file_hash'], row['page_count'])
        except (sqlite3.DatabaseError, ValueError) as exc:
            if isinstance(exc, (ResourceNotFoundError, DataIntegrityError)):
                raise
            raise DataIntegrityError('PDF 위치의 저장 정보를 확인하지 못했습니다.') from None
