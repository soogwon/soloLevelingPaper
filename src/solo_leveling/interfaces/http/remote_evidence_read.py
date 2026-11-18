"""저장된 원격 답변의 근거를 고정 출처로 재조회한다. 검색·생성 포트는 없다."""
import hashlib
import json


class PostgresRemoteEvidenceReader:
    def __init__(self, pool):
        self.pool = pool

    def get(self, owner, context_id, evidence_id):
        with self.pool.connection() as conn:
            row = conn.execute('''SELECT e.evidence_id,e.chunk_id,e.quoted_text,e.quote_ko,
                c.paper_id,c.server_version_id,c.publication_id,c.parse_revision_id,
                pub.translation_revision_id,pub.embedding_set_id,
                ch.original_text,ch.pdf_page,ch.printed_page_label,ch.section_id,
                tc.translated_text,x.payload_json,x.payload_sha256
                FROM server_execution_contexts c
                JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                JOIN server_processing_publications pub ON pub.owner_user_id=c.owner_user_id
                  AND pub.paper_id=c.paper_id AND pub.server_version_id=c.server_version_id
                  AND pub.publication_id=c.publication_id AND pub.parse_revision_id=c.parse_revision_id
                JOIN server_embedding_sets es ON es.owner_user_id=pub.owner_user_id
                  AND es.embedding_set_id=pub.embedding_set_id AND es.status='ready'
                JOIN server_processing_evidences e ON e.owner_user_id=c.owner_user_id
                  AND e.context_id=c.context_id AND e.paper_id=c.paper_id
                  AND e.server_version_id=c.server_version_id AND e.publication_id=c.publication_id
                  AND e.parse_revision_id=c.parse_revision_id
                JOIN server_remote_executions x ON x.owner_user_id=e.owner_user_id
                  AND x.execution_id=e.execution_id AND x.context_id=e.context_id AND x.state='completed'
                JOIN server_processing_chunks ch ON ch.owner_user_id=e.owner_user_id
                  AND ch.paper_id=e.paper_id AND ch.server_version_id=e.server_version_id
                  AND ch.parse_revision_id=e.parse_revision_id AND ch.chunk_id=e.chunk_id
                JOIN server_translation_chunks tc ON tc.owner_user_id=ch.owner_user_id
                  AND tc.chunk_id=ch.chunk_id AND tc.translation_revision_id=pub.translation_revision_id
                WHERE c.owner_user_id=%s AND c.context_id=%s AND e.evidence_id=%s
                  AND p.deleted_at IS NULL''', (owner, context_id, evidence_id)).fetchone()
        if row is None:
            return None
        # 완료 답변의 무결성과 당시 인용을 대조한다. 현재 검색 결과로 대체하지 않는다.
        raw = row['payload_json']
        if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != row['payload_sha256']:
            raise ValueError('remote_evidence_integrity_invalid')
        payload = json.loads(raw)
        citations = [c for c in payload.get('citations', []) if c.get('evidence_id') == str(row['evidence_id'])]
        expected = {'chunk_id': str(row['chunk_id']), 'version_id': row['server_version_id'],
                    'parse_revision_id': str(row['parse_revision_id']),
                    'translation_revision_id': str(row['translation_revision_id']),
                    'quote_original': row['quoted_text'], 'quote_ko': row['quote_ko']}
        if (payload.get('context_id') != context_id or len(citations) != 1
                or any(citations[0].get(k) != v for k, v in expected.items())
                or not row['quoted_text'] or row['quoted_text'] not in row['original_text']
                or (row['quote_ko'] is not None and row['quote_ko'] not in row['translated_text'])):
            raise ValueError('remote_evidence_integrity_invalid')
        return {'context_id': context_id, 'evidence_id': str(row['evidence_id']), **expected,
                'paper_id': str(row['paper_id']), 'publication_id': str(row['publication_id']),
                'embedding_set_id': str(row['embedding_set_id']),
                'original_text_sha256': hashlib.sha256(row['original_text'].encode()).hexdigest(),
                'pdf_page': row['pdf_page'], 'printed_page_label': row['printed_page_label'],
                'section_id': row['section_id'],
                'page_notice': 'PDF 페이지는 이 고정 판본 기준입니다. 인쇄 페이지 표기 및 다른 판본과 다를 수 있습니다.'}
