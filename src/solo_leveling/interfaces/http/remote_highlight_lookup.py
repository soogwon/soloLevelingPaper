"""고정 판본의 원문에서 공백만 정규화해 정확한 구간을 찾는다."""

import hashlib
from itertools import chain
import re

from solo_leveling.domain.annotation import MAX_QUOTE_LENGTH


def locate_quote(chunks, sections, quote, *, limit=12):
    query = ' '.join(quote.split())
    if not query or len(quote) > MAX_QUOTE_LENGTH:
        raise ValueError('highlight_quote_invalid')
    results = []
    for row in chunks:
        original = row['original_text']
        tokens = list(re.finditer(r'\S+', original))
        normalized = ' '.join(token[0] for token in tokens)
        at = normalized.find(query)
        if at < 0:
            continue
        offsets = []
        for token in tokens:
            if offsets:
                offsets.append(token.start() - 1)
            offsets.extend(range(token.start(), token.end()))
        while at >= 0:
            start, end = offsets[at], offsets[at + len(query) - 1] + 1
            snapshot = original[start:end]
            if len(snapshot) <= MAX_QUOTE_LENGTH:
                chunk_id = str(row['chunk_id'])
                results.append({
                    'candidate_id': hashlib.sha256(f'{chunk_id}:{start}:{end}'.encode()).hexdigest(),
                    'pdf_page': row['pdf_page'],
                    'section_title': sections.get(row['section_id']),
                    'excerpt': original[max(0, start - 80):end + 80],
                    'segment': {'chunk_id': chunk_id, 'start_offset': start, 'end_offset': end,
                                'quote_snapshot': snapshot},
                })
                if len(results) == limit:
                    return results
            at = normalized.find(query, at + 1)
    return results


def find_highlight_candidates(pool, owner, context, quote, pdf_page=None):
    if (not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_LENGTH
            or pdf_page is not None and (type(pdf_page) is not int or pdf_page < 1)):
        raise ValueError('highlight_quote_invalid')
    with pool.connection() as conn:
        rows = conn.execute("""
            SELECT ch.chunk_id, ch.original_text, ch.pdf_page, ch.section_id, r.document_metadata
            FROM server_execution_contexts c
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            JOIN server_parse_revisions r ON r.owner_user_id=c.owner_user_id AND r.paper_id=c.paper_id
              AND r.server_version_id=c.server_version_id AND r.parse_revision_id=c.parse_revision_id
            JOIN server_processing_chunks ch ON ch.owner_user_id=r.owner_user_id AND ch.paper_id=r.paper_id
              AND ch.server_version_id=r.server_version_id AND ch.parse_revision_id=r.parse_revision_id
            WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
              AND (%s::int IS NULL OR ch.pdf_page=%s)
            ORDER BY ch.ordinal
            """, (owner, context, pdf_page, pdf_page))
        first = rows.fetchone()
        if first is None:
            return []
        metadata = first['document_metadata']
        sections = {s.get('section_id'): s['title'] for s in metadata.get('sections', ())
                    if isinstance(s, dict) and isinstance(s.get('title'), str)}
        return locate_quote(chain((first,), rows), sections, quote)
