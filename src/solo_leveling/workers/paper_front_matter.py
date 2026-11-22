"""PDF 첫 페이지에서 찾은 서지 정보를 논문의 빈 제목·저자·출판 연도 열에만 채운다."""
from io import BytesIO
import json

from solo_leveling.domain.remote_processing import PaperFrontMatter, front_matter_valid


def fill_missing_paper_metadata(conn, owner_user_id: str, paper_id: str,
                                front_matter: PaperFrontMatter | None) -> bool:
    """비어 있는 열만 채운다. 사용자·로컬 기기가 넣은 값은 덮어쓰지 않는다.

    실제로 바뀐 행이 있으면 True. 호출자가 연 transaction 안에서 실행한다.
    """
    if front_matter is None:
        return False
    if not front_matter_valid(front_matter):
        raise ValueError('front_matter_invalid')
    authors = (None if front_matter.authors is None
               else json.dumps(list(front_matter.authors), ensure_ascii=False))
    values = (front_matter.title, authors, front_matter.publication_year)
    row = conn.execute('''UPDATE server_papers SET
            title=COALESCE(title,%s), authors=COALESCE(authors,%s::jsonb),
            publication_year=COALESCE(publication_year,%s::integer),
            revision=revision+1, updated_at=CURRENT_TIMESTAMP
        WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL
          AND ((title IS NULL AND %s::text IS NOT NULL)
            OR (authors IS NULL AND %s::jsonb IS NOT NULL)
            OR (publication_year IS NULL AND %s::integer IS NOT NULL))
        RETURNING paper_id''',
        (*values, owner_user_id, paper_id, *values)).fetchone()
    return row is not None


def read_front_matter(pdf: bytes) -> PaperFrontMatter | None:
    """메모리 PDF의 첫 페이지 서지 정보. 처리 worker와 같은 추출기를 쓴다."""
    from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages

    with BytesIO(pdf) as stream:
        pages = extract_pages(stream)
    value = pages[0].front_matter if pages else None
    return value if isinstance(value, PaperFrontMatter) and front_matter_valid(value) else None
