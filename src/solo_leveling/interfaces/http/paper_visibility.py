"""웹 아카이브와 컬렉션에서 공유하는 논문 노출 조건."""

# p는 소유권이 확인된 server_papers 행이다. 등록 준비 행은 업로드 화면에서 관리한다.
VISIBLE_PAPER = """p.deleted_at IS NULL AND (
    p.origin <> 'remote'
    OR EXISTS (
        SELECT 1 FROM server_pdf_uploads visible_upload
        WHERE visible_upload.owner_user_id = p.owner_user_id AND visible_upload.paper_id = p.paper_id
          AND visible_upload.source_kind = 'remote' AND visible_upload.status = 'ready'
    )
    OR EXISTS (
        SELECT 1 FROM server_learning_records visible_learning
        WHERE visible_learning.owner_user_id = p.owner_user_id AND visible_learning.paper_id = p.paper_id
    )
    OR EXISTS (
        SELECT 1 FROM server_remote_learning_records visible_remote_learning
        WHERE visible_remote_learning.owner_user_id = p.owner_user_id
          AND visible_remote_learning.paper_id = p.paper_id
    )
)"""
