"""고정 판본의 원문에서 공백만 정규화해 정확한 구간을 찾는다."""

import hashlib
from itertools import chain
import re

from solo_leveling.domain.annotation import MAX_QUOTE_LENGTH, MAX_SEGMENTS
from solo_leveling.infrastructure.parsing.chunker import CHUNK_OVERLAP


def _verified_overlap(left, right):
    # 짧은 단어나 여러 길이에서 일치하는 반복 문구는 결합 근거로 쓰지 않는다.
    matches = [
        length for length in range(1, min(CHUNK_OVERLAP, len(left), len(right) - 1) + 1)
        if left.endswith(right[:length])
        and (len(left) == length or left[-length - 1].isspace())
    ]
    if len(matches) != 1 or matches[0] < 16 or len(right[:matches[0]].split()) < 2:
        return 0
    return matches[0]


def _original_runs(chunks):
    """같은 페이지·섹션의 검증된 겹침만 제거하고 청크별 문자 좌표를 남긴다."""
    previous = None
    text_parts, spans = [], []
    length = 0
    for row in chunks:
        original = row['original_text']
        overlap = 0
        if (previous is not None and previous['pdf_page'] == row['pdf_page']
                and previous['section_id'] == row['section_id']
                and previous.get('ordinal', -2) + 1 == row.get('ordinal', -1)):
            overlap = _verified_overlap(previous['original_text'], original)
        if previous is not None and not overlap:
            yield previous, ''.join(text_parts), spans
            text_parts, spans, length = [], [], 0
        spans.append((length, length + len(original) - overlap, overlap, str(row['chunk_id'])))
        text_parts.append(original[overlap:])
        length += len(original) - overlap
        previous = row
    if previous is not None:
        yield previous, ''.join(text_parts), spans


def _selected_segments(original, spans, start, end):
    segments = []
    for left, right, offset, chunk_id in spans:
        first, last = max(start, left), min(end, right)
        if first < last:
            segments.append({'chunk_id': chunk_id, 'start_offset': first - left + offset,
                             'end_offset': last - left + offset, 'quote_snapshot': original[first:last]})
    return segments


def locate_quote(chunks, sections, quote, *, limit=12):
    query = ' '.join(quote.split())
    if not query or len(quote) > MAX_QUOTE_LENGTH:
        raise ValueError('highlight_quote_invalid')
    results = []
    for row, original, spans in _original_runs(chunks):
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
            segments = _selected_segments(original, spans, start, end)
            if len(snapshot) <= MAX_QUOTE_LENGTH and 0 < len(segments) <= MAX_SEGMENTS:
                key = '|'.join(f"{s['chunk_id']}:{s['start_offset']}:{s['end_offset']}" for s in segments)
                results.append({
                    'candidate_id': hashlib.sha256(key.encode()).hexdigest(),
                    'pdf_page': row['pdf_page'],
                    'section_title': sections.get(row['section_id']),
                    'excerpt': original[max(0, start - 80):end + 80],
                    'segments': segments,
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
            SELECT ch.chunk_id, ch.original_text, ch.pdf_page, ch.section_id, ch.ordinal, r.document_metadata
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
