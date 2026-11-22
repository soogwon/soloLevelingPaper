"""제품 추출기·청커를 메모리 PDF에 연결한다. 경로·R2·DB는 받지 않는다."""

from io import BytesIO
from itertools import count
from uuid import NAMESPACE_URL, uuid5
from dataclasses import dataclass

from solo_leveling.domain.remote_processing import PaperFrontMatter, ParsedDocument, ProcessingChunk, ProcessingError
from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages, PdfExtractionError
from solo_leveling.infrastructure.parsing.chunker import chunk_pages_with_sections, CHUNK_SIZE, CHUNK_OVERLAP


@dataclass(frozen=True, kw_only=True)
class ProcessingPdfParser:
    chunk_size: int = CHUNK_SIZE
    overlap: int = CHUNK_OVERLAP

    def __post_init__(self):
        if (type(self.chunk_size) is not int or type(self.overlap) is not int
                or not 0 <= self.overlap < self.chunk_size):
            raise ProcessingError('PROCESSING_CONFIG_INVALID')

    def parse(self, pdf: bytes, parse_revision_id: str) -> ParsedDocument:
        try:
            with BytesIO(pdf) as stream:
                pages = extract_pages(stream)
        except PdfExtractionError as error:
            code = 'PROCESSING_PDF_ENCRYPTED' if error.code == 'pdf_encrypted' else 'PROCESSING_PDF_UNREADABLE'
            raise ProcessingError(code) from None
        # 같은 고정 파싱 리비전의 재실행에서도 청크 신원이 달라지지 않는다.
        indices = count()
        chunks, sections = chunk_pages_with_sections(parse_revision_id, pages,
            chunk_size=self.chunk_size, overlap=self.overlap,
            id_factory=lambda: str(uuid5(NAMESPACE_URL, f'remote-parse:{parse_revision_id}:{next(indices)}')))
        front_matter = pages[0].front_matter if pages else None
        return ParsedDocument(len(pages), tuple(ProcessingChunk(
            c.chunk_id, c.parse_revision_id, c.chunk_index, c.original_text,
            c.pdf_page, c.section_id, c.printed_page_label) for c in chunks), tuple(sections),
            front_matter if isinstance(front_matter, PaperFrontMatter) else None)
