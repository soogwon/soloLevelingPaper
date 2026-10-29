"""한 읽기 트랜잭션에서 상세 메타데이터·요약·인용 출처를 확인한다."""
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path, PurePosixPath, PureWindowsPath

from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.domain.evidence_qa import EvidenceDetail
from solo_leveling.domain.paper_summary import SummaryItem


class SQLitePaperDetailReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, context_id, scope, offset, limit, summary_id):
        # 존재하지 않는 DB를 만들거나 설정/저장 작업을 수행하지 않는다.
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            try:
                return self._read(conn, context_id, scope, offset, limit, summary_id)
            except (InvalidArgumentError, ResourceNotFoundError, DataIntegrityError):
                raise
            except (ValueError, TypeError, KeyError):
                raise DataIntegrityError('저장된 요약 또는 근거의 연결을 확인하지 못했습니다.') from None

    def _read(self, conn, context_id, scope, offset, limit, expected_summary_id):
        context = conn.execute('SELECT * FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone()
        if context is None:
            raise ResourceNotFoundError('학습 맥락을 찾지 못했습니다.')
        metadata = conn.execute('''SELECT p.title, v.paper_id, v.original_filename
            FROM paper_versions v JOIN papers p ON p.paper_id=v.paper_id
            JOIN parse_revisions r ON r.version_id=v.version_id
            JOIN translation_revisions t ON t.parse_revision_id=r.parse_revision_id
            JOIN search_indexes s ON s.version_id=v.version_id AND s.parse_revision_id=r.parse_revision_id
                AND s.translation_revision_id=t.translation_revision_id
            WHERE v.version_id=? AND r.parse_revision_id=? AND t.translation_revision_id=?
                AND s.embedding_set_id=?''',
            (context['version_id'], context['parse_revision_id'], context['translation_revision_id'],
             context['embedding_set_id'])).fetchone()
        if metadata is None:
            raise DataIntegrityError('학습 맥락의 출처 연결이 일치하지 않습니다.')
        filename = PureWindowsPath(PurePosixPath(metadata['original_filename']).name).name
        sections = [r[0] for r in conn.execute('''SELECT DISTINCT section_id FROM chunks
            WHERE parse_revision_id=? AND section_id IS NOT NULL AND trim(section_id)<>'' ORDER BY section_id''',
            (context['parse_revision_id'],))]
        if scope != 'paper' and scope not in sections:
            raise ResourceNotFoundError('요청한 섹션을 찾지 못했습니다.')
        saved = conn.execute('SELECT * FROM summaries WHERE context_id=? AND scope=?', (context_id, scope)).fetchone()
        if expected_summary_id is not None and (saved is None or saved['summary_id'] != expected_summary_id):
            raise InvalidArgumentError('요약이 변경되었습니다. offset=0으로 다시 조회하세요.')
        result = {
            'context_id': context_id, 'paper_id': metadata['paper_id'], 'title': metadata['title'],
            'file_display_name': filename, 'version_id': context['version_id'],
            'parse_revision_id': context['parse_revision_id'],
            'translation_revision_id': context['translation_revision_id'], 'scope': scope,
            'section_ids': sections, 'summary': None, 'evidence': [],
            'keywords': [], 'important_sentences': [],
            'limitations': ['keywords_not_available', 'important_sentences_not_available', 'annotations_not_connected'],
        }
        if saved is None:
            result['limitations'].append('summary_not_available')
            return result
        count = conn.execute('SELECT COUNT(*) FROM summary_items WHERE summary_id=?', (saved['summary_id'],)).fetchone()[0]
        if offset > count:
            raise InvalidArgumentError('offset이 요약 항목 수를 벗어났습니다.')
        rows = conn.execute('''SELECT * FROM summary_items WHERE summary_id=? ORDER BY position
            LIMIT ? OFFSET ?''', (saved['summary_id'], limit, offset)).fetchall()
        items, known = [], {}
        for row in rows:
            ids = json.loads(row['evidence_ids'])
            if not isinstance(ids, list) or len(set(ids)) != len(ids):
                raise ValueError('invalid evidence list')
            item = SummaryItem(row['kind'], row['text'], tuple(ids))
            items.append({'position': row['position'], 'kind': item.kind, 'text': item.text, 'evidence_ids': ids})
            for eid in ids:
                if eid in known:
                    continue
                e = conn.execute('''SELECT e.*, c.parse_revision_id, c.translation_revision_id,
                    c.original_text, c.text, c.pdf_page, c.printed_page_label, c.section_id
                    FROM evidences e JOIN chunks c ON c.chunk_id=e.chunk_id
                    WHERE e.evidence_id=? AND e.context_id=?''', (eid, context_id)).fetchone()
                if (e is None or e['parse_revision_id'] != context['parse_revision_id']
                        or e['translation_revision_id'] != context['translation_revision_id']
                        or (scope != 'paper' and e['section_id'] != scope)):
                    raise ValueError('invalid provenance')
                detail = EvidenceDetail(eid, e['chunk_id'], context['version_id'], e['parse_revision_id'],
                    e['translation_revision_id'], e['printed_page_label'], e['pdf_page'],
                    e['quote_ko'], e['quote_original'], filename)
                if (detail.quote_original not in e['original_text'] or
                        (detail.quote_ko is not None and (e['text'] is None or detail.quote_ko not in e['text']))):
                    raise ValueError('invalid quote')
                known[eid] = {**asdict(detail), 'section_id': e['section_id']}
        result['summary'] = {
            'summary_id': saved['summary_id'], 'created_at': saved['created_at'],
            'items': items, 'total_items': count,
            'next_offset': offset + len(items) if offset + len(items) < count else None,
            'verification_level': 'structural_only',
        }
        result['evidence'] = list(known.values())
        return result
