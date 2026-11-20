"""고정 판본에서 읽기용 PDF 페이지 본문을 반환한다."""

from uuid import UUID

CHUNKS_PER_RESPONSE = 8
TEXT_LIMIT = 1000
PAGE_NOTICE = "PDF 페이지는 이 고정 판본 기준입니다. 인쇄 페이지 표기는 별도입니다."


def read_paper_page(pool, binding, pdf_page, cursor, include_translation):
    scope = (binding.owner_user_id, binding.paper_id, binding.server_version_id, binding.parse_revision_id)
    with pool.connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = conn.execute("""
            SELECT r.status, r.document_metadata
            FROM server_parse_revisions r JOIN server_papers p
              ON p.owner_user_id=r.owner_user_id AND p.paper_id=r.paper_id
            WHERE r.owner_user_id=%s AND r.paper_id=%s AND r.server_version_id=%s AND r.parse_revision_id=%s
              AND p.deleted_at IS NULL
            """, scope).fetchone()
        if row is None:
            return {"status": "not_found"}
        if row["status"] != "ready":
            return {"status": "not_ready"}
        page_count = row["document_metadata"].get("page_count")
        if type(page_count) is not int or page_count < 1:
            # 초기 게시에는 실제 전체 페이지 수가 없으므로 확인 가능한 최고 페이지까지만 제공한다.
            page_count = conn.execute("""
                SELECT max(pdf_page) AS page_count FROM server_processing_chunks
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s AND parse_revision_id=%s
                """, scope).fetchone()["page_count"]
        if page_count is None:
            return {"status": "not_ready"}
        if pdf_page > page_count:
            return {"status": "page_out_of_range"}
        ordinal, offset = -1, 0
        if cursor is not None:
            chunk_id, offset = cursor
            anchor = conn.execute("""
                SELECT ordinal FROM server_processing_chunks
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s AND parse_revision_id=%s
                  AND pdf_page=%s AND chunk_id=%s
                """, (*scope, pdf_page, chunk_id)).fetchone()
            if anchor is None:
                return {"status": "not_found"}
            ordinal = anchor["ordinal"]
        translation_columns = """
            substring(t.translated_text FROM ch.text_offset + 1 FOR %s) AS translated_text,
            COALESCE(length(t.translated_text), 0) AS translation_length,
            t.translation_status
        """ if include_translation else "NULL AS translated_text, 0 AS translation_length, NULL AS translation_status"
        translation_join = """
            LEFT JOIN server_translation_revisions tr ON tr.owner_user_id=ch.owner_user_id
              AND tr.paper_id=ch.paper_id AND tr.server_version_id=ch.server_version_id
              AND tr.parse_revision_id=ch.parse_revision_id AND tr.translation_revision_id=%s AND tr.status='ready'
            LEFT JOIN server_translation_chunks t ON t.owner_user_id=tr.owner_user_id
              AND t.translation_revision_id=tr.translation_revision_id AND t.chunk_id=ch.chunk_id
              AND t.translation_status IN ('translated', 'reference_content')
        """ if include_translation else ""
        translation_params = (TEXT_LIMIT, binding.translation_revision_id) if include_translation else ()
        rows = conn.execute(f"""
            WITH page_chunks AS (
                SELECT ch.*, CASE WHEN ordinal=%s THEN %s ELSE 0 END AS text_offset
                FROM server_processing_chunks ch
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s AND parse_revision_id=%s
                  AND pdf_page=%s AND ordinal>%s
                ORDER BY ordinal LIMIT %s
            )
            SELECT ch.chunk_id, ch.ordinal, ch.section_id, ch.printed_page_label, ch.text_offset,
                   substring(ch.original_text FROM ch.text_offset + 1 FOR %s) AS original_text,
                   length(ch.original_text) AS original_length, {translation_columns}
            FROM page_chunks ch
            {translation_join}
            ORDER BY ch.ordinal
            """, (ordinal, offset, *scope, pdf_page, ordinal - bool(offset),
                  CHUNKS_PER_RESPONSE + 1, TEXT_LIMIT, *translation_params)).fetchall()
    chunks = []
    next_cursor = None
    for row in rows[:CHUNKS_PER_RESPONSE]:
        chunk = dict(row, chunk_id=str(row["chunk_id"]))
        next_offset = chunk["text_offset"] + TEXT_LIMIT
        original_length = chunk.pop("original_length")
        translation_length = chunk.pop("translation_length")
        if offset and not chunks and offset >= max(original_length, translation_length):
            return {"status": "not_found"}
        chunk["original_truncated"] = original_length > next_offset
        chunk["translation_truncated"] = translation_length > next_offset
        chunks.append(chunk)
        if chunk["original_truncated"] or chunk["translation_truncated"]:
            next_cursor = f'{chunk["chunk_id"]}:{next_offset}'
            break
    if next_cursor is None and len(rows) > CHUNKS_PER_RESPONSE:
        next_cursor = chunks[-1]["chunk_id"]
    return {"status": "ok", "result": {
        "context_id": binding.context_id, "paper_id": binding.paper_id,
        "version_id": binding.server_version_id, "publication_id": binding.publication_id,
        "parse_revision_id": binding.parse_revision_id, "publication_policy": binding.publication_policy,
        "pdf_page": pdf_page, "page_count": page_count,
        "page_status": "empty" if not chunks and cursor is None else "available",
        "chunks": chunks, "next_cursor": next_cursor,
        "page_notice": PAGE_NOTICE,
    }}


def valid_page_arguments(context_id, pdf_page, cursor):
    if type(pdf_page) is not int or not 1 <= pdf_page <= 2147483647:
        raise ValueError("invalid_pdf_page")
    context_id = str(UUID(context_id))
    if cursor is None:
        return context_id, None
    chunk_id, separator, raw_offset = cursor.partition(":")
    offset = int(raw_offset) if separator and raw_offset.isascii() and raw_offset.isdecimal() and len(raw_offset) <= 10 else 0
    if separator and (offset < TEXT_LIMIT or offset > 2147483647 - TEXT_LIMIT or offset % TEXT_LIMIT):
        raise ValueError("invalid_page_cursor")
    return context_id, (str(UUID(chunk_id)), offset)
