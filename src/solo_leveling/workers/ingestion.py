"""
workers/ingestion — 등록 파이프라인 오케스트레이션

10번 문서 단계: download → parse → translate → index
22번 문서 순서 원칙: "파생 색인을 먼저 준비·검증한 후 SQLite의 ready 상태를 게시한다"

주의(팀 공유용 TODO): 번역(translate) 단계는 B 담당 모델·프롬프트가 아직 없다.
이 파이프라인은 A의 1주차 산출물("PDF→원문·페이지·벡터 저장")을 끝까지 시연하기
위해, 번역이 없을 때 원문(original_text)을 그대로 임베딩하는 "임시 통과
(passthrough) 번역 리비전"을 만든다. provider="interim-passthrough"로 명시
표시했으니, B의 실제 번역 파이프라인이 준비되면 이 부분을 교체해야 한다.
"""
import hashlib
import uuid
from pathlib import Path
from typing import Optional

from solo_leveling.domain.models import (
    Chunk,
    EmbeddingSet,
    JobStage,
    JobStatus,
    Paper,
    ParseRevision,
    PaperVersion,
    ProcessingJob,
    TranslationRevision,
    now_iso,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.embeddings.embedder import (
    DEFAULT_MODEL_NAME,
    embed_texts,
    embedding_dimension,
)
from solo_leveling.infrastructure.parsing.chunker import chunk_pages
from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages
from solo_leveling.infrastructure.storage.vector_store import get_client, upsert_chunk_embeddings

INTERIM_TRANSLATION_PROVIDER = "interim-passthrough"


def compute_file_hash(pdf_path: str) -> str:
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def register_and_ingest(
    db_path: str,
    chroma_dir: str,
    pdf_path: str,
    original_filename: Optional[str] = None,
    paper_id: Optional[str] = None,
    embedding_model: str = DEFAULT_MODEL_NAME,
) -> dict:
    """
    PDF 한 편을 처음부터 끝까지 등록·색인한다.
    반환: {"job_id", "status", "version_id", "parse_revision_id", "chunk_count", "reused_existing"}

    idempotency: 같은 paper_id에 같은 file_hash가 이미 있으면 재처리하지 않고
    기존 버전을 그대로 반환한다(10번 문서 request_key 취지의 최소 구현).
    """
    init_db(db_path)
    paper_id = paper_id or str(uuid.uuid4())
    original_filename = original_filename or Path(pdf_path).name

    # papers 행이 없으면 생성 (idempotent)
    conn = repo.get_connection(db_path)
    exists = conn.execute("SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    conn.close()
    if not exists:
        repo.insert_paper(db_path, Paper(paper_id=paper_id, title=None, source_kind="local_file"))

    file_hash = compute_file_hash(pdf_path)
    existing = repo.find_version_by_hash(db_path, paper_id, file_hash)
    if existing:
        return {
            "job_id": None,
            "status": "ready",
            "version_id": existing.version_id,
            "parse_revision_id": None,
            "chunk_count": None,
            "reused_existing": True,
        }

    version = PaperVersion(
        version_id=str(uuid.uuid4()),
        paper_id=paper_id,
        file_hash=file_hash,
        stored_path=pdf_path,  # TODO: runtime/pdfs로 실제 복사하는 로직은 C 영역과 합의 후 추가
        original_filename=original_filename,
    )
    repo.insert_version(db_path, version)

    job = ProcessingJob(job_id=str(uuid.uuid4()), version_id=version.version_id, status=JobStatus.QUEUED)
    repo.insert_job(db_path, job)

    try:
        # ── parse ──────────────────────────────────────────────
        repo.update_job_status(db_path, job.job_id, JobStatus.PROCESSING, stage=JobStage.PARSE)
        pages = extract_pages(pdf_path)
        parse_revision = ParseRevision(
            parse_revision_id=str(uuid.uuid4()),
            version_id=version.version_id,
            page_count=len(pages),
        )
        repo.insert_parse_revision(db_path, parse_revision)

        chunks = chunk_pages(parse_revision.parse_revision_id, pages)
        if chunks:
            repo.insert_chunks(db_path, chunks)

        # ── translate (임시 통과 — B 파이프라인 대기) ─────────────
        repo.update_job_status(db_path, job.job_id, JobStatus.PROCESSING, stage=JobStage.TRANSLATE)
        translation = TranslationRevision(
            translation_revision_id=str(uuid.uuid4()),
            parse_revision_id=parse_revision.parse_revision_id,
            provider=INTERIM_TRANSLATION_PROVIDER,
            model=None,
        )
        # 번역 리비전 자체는 SQLite에 별도 저장하지 않음(translation_revisions 테이블은
        # B 파이프라인이 실제로 쓸 때 채워질 것). 임베딩 세트 id 생성에만 사용.

        limitations = []
        if not chunks:
            limitations.append("extraction_empty")

        # ── index (임베딩 + Chroma 저장) ──────────────────────────
        repo.update_job_status(db_path, job.job_id, JobStatus.PROCESSING, stage=JobStage.INDEX)
        chunk_count = len(chunks)
        if chunks:
            texts_to_embed = [c.original_text for c in chunks]  # TODO: B 번역 완료 후 c.text로 교체
            vectors = embed_texts(texts_to_embed, model_name=embedding_model)
            dim = embedding_dimension(embedding_model)
            embedding_set = EmbeddingSet(
                embedding_set_id=str(uuid.uuid4()),
                translation_revision_id=translation.translation_revision_id,
                model_name=embedding_model,
                dimension=dim,
            )
            client = get_client(chroma_dir)
            upsert_chunk_embeddings(
                client,
                chunk_ids=[c.chunk_id for c in chunks],
                embeddings=vectors,
                embedding_set_id=embedding_set.embedding_set_id,
                paper_id=paper_id,
                version_id=version.version_id,
                documents=texts_to_embed,
            )
            # 파생 색인(Chroma)이 검증된 뒤에만 SQLite를 ready로 게시한다(22번 문서 순서 원칙)

        repo.update_job_status(
            db_path, job.job_id, JobStatus.READY, stage=JobStage.INDEX, limitations=limitations
        )

        return {
            "job_id": job.job_id,
            "status": "ready",
            "version_id": version.version_id,
            "parse_revision_id": parse_revision.parse_revision_id,
            "chunk_count": chunk_count,
            "reused_existing": False,
        }

    except Exception as e:
        repo.update_job_status(
            db_path, job.job_id, JobStatus.FAILED, limitations=[f"error: {e}"]
        )
        raise
