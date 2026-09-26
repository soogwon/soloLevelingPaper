from solo_leveling.workers.ingestion import register_and_ingest_url
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings, TranslationRequest


class EchoTranslationProvider:
    """실제 번역 provider가 아직 없어서, 원문을 그대로 돌려주는 테스트 전용 provider.
    파이프라인(페이지/청크/벡터/게시)이 실제로 동작하는지만 확인하는 목적."""
    def translate(self, request: TranslationRequest, settings: TranslationSettings) -> str:
        return f"[번역됨] {request.original_text}"


db_path = './test_data/db.sqlite'
chroma_dir = './test_data/chroma'
tmp_pdf_dir = './test_data/tmp_pdfs'

translation_service = TranslationService(EchoTranslationProvider())
translation_settings = TranslationSettings(
    provider='echo-test', model='none', prompt_version='v0',
)

# 1. URL로 PDF 직접 등록
r1 = register_and_ingest_url(
    db_path, chroma_dir, 'https://arxiv.org/pdf/1706.03762.pdf', tmp_pdf_dir,
    translation_service=translation_service, translation_settings=translation_settings,
)
print('PDF 등록:', r1)

# 2. URL로 HTML(초록 페이지) 등록
r2 = register_and_ingest_url(
    db_path, chroma_dir, 'https://arxiv.org/abs/1706.03762', tmp_pdf_dir,
    translation_service=translation_service, translation_settings=translation_settings,
)
print('HTML 등록:', r2)

chunks_pdf = repo.get_chunks_by_parse_revision(db_path, r1['parse_revision_id'])
chunks_html = repo.get_chunks_by_parse_revision(db_path, r2['parse_revision_id'])
print('PDF 경로 - 페이지 수:', len(set(c.pdf_page for c in chunks_pdf)))
print('HTML 경로 - pdf_page 값들:', set(c.pdf_page for c in chunks_html))
print('PDF 청크 번역문 예시:', chunks_pdf[0].text[:50] if chunks_pdf else None)