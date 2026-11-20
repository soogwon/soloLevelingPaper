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
        ordinal = -1
        if cursor is not None:
            anchor = conn.execute("""
                SELECT ordinal FROM server_processing_chunks
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s AND parse_revision_id=%s
                  AND pdf_page=%s AND chunk_id=%s
                """, (*scope, pdf_page, cursor)).fetchone()
            if anchor is None:
                return {"status": "not_found"}
            ordinal = anchor["ordinal"]
        rows = conn.execute("""
            SELECT ch.chunk_id, ch.ordinal, ch.section_id, ch.printed_page_label,
                   left(ch.original_text, %s) AS original_text,
                   length(ch.original_text) > %s AS original_truncated,
                   left(t.translated_text, %s) AS translated_text,
                   COALESCE(length(t.translated_text) > %s, false) AS translation_truncated,
                   t.translation_status
            FROM server_processing_chunks ch
            LEFT JOIN server_translation_revisions tr ON %s AND tr.owner_user_id=ch.owner_user_id
              AND tr.paper_id=ch.paper_id AND tr.server_version_id=ch.server_version_id
              AND tr.parse_revision_id=ch.parse_revision_id AND tr.translation_revision_id=%s AND tr.status='ready'
            LEFT JOIN server_translation_chunks t ON t.owner_user_id=tr.owner_user_id
              AND t.translation_revision_id=tr.translation_revision_id AND t.chunk_id=ch.chunk_id
            WHERE ch.owner_user_id=%s AND ch.paper_id=%s AND ch.server_version_id=%s AND ch.parse_revision_id=%s
              AND ch.pdf_page=%s AND ch.ordinal>%s
            ORDER BY ch.ordinal LIMIT %s
            """, (TEXT_LIMIT, TEXT_LIMIT, TEXT_LIMIT, TEXT_LIMIT, include_translation,
                  binding.translation_revision_id, *scope, pdf_page, ordinal, CHUNKS_PER_RESPONSE + 1)).fetchall()
    chunks = [dict(row, chunk_id=str(row["chunk_id"])) for row in rows[:CHUNKS_PER_RESPONSE]]
    for chunk in chunks:
        if chunk["translation_status"] not in ("translated", "reference_content"):
            chunk["translated_text"] = None
            chunk["translation_truncated"] = False
    return {"status": "ok", "result": {
        "context_id": binding.context_id, "paper_id": binding.paper_id,
        "version_id": binding.server_version_id, "publication_id": binding.publication_id,
        "parse_revision_id": binding.parse_revision_id, "publication_policy": binding.publication_policy,
        "pdf_page": pdf_page, "page_count": page_count,
        "page_status": "empty" if not chunks and cursor is None else "available",
        "chunks": chunks, "next_cursor": chunks[-1]["chunk_id"] if len(rows) > CHUNKS_PER_RESPONSE else None,
        "page_notice": PAGE_NOTICE,
    }}


def valid_page_arguments(context_id, pdf_page, cursor):
    if type(pdf_page) is not int or not 1 <= pdf_page <= 2147483647:
        raise ValueError("invalid_pdf_page")
    return str(UUID(context_id)), str(UUID(cursor)) if cursor is not None else None
