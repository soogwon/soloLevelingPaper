"""호스트가 준비한 저장소·설정으로 B 서비스를 조립한다. 실행·환경 로딩은 하지 않는다."""

from dataclasses import dataclass

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.application.evidence_qa.evidence import EvidenceService
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.embeddings.embedder import embed_texts
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceReader, SQLiteEvidenceWriter
from solo_leveling.infrastructure.generation.openai_generator import OpenAIClaimGenerator, OpenAIGenerationSettings
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever, SQLiteContextReader
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider


@dataclass(frozen=True)
class EvidenceQAServices:
    search: SearchEntryService
    answer: AnswerService
    evidence: EvidenceService
    translation: TranslationService
    translation_settings: TranslationSettings


def build_services(db_path: str, chroma_client, *, generation: OpenAIGenerationSettings,
                   translation: OpenAITranslationConfig, embedder=embed_texts) -> EvidenceQAServices:
    """DB 초기화·모델 다운로드·외부 API 호출 없이 객체만 연결한다."""
    retriever = SQLiteChromaRetriever(db_path, chroma_client, embedder=embedder)
    search = SearchEntryService(SQLiteContextReader(db_path), retriever)
    return EvidenceQAServices(search,
        AnswerService(search, OpenAIClaimGenerator(generation), SQLiteEvidenceWriter(db_path),
                      continuation_reader=retriever),
        EvidenceService(SQLiteEvidenceReader(db_path)),
        TranslationService(OpenAITranslationProvider(translation)), translation.translation_settings)
