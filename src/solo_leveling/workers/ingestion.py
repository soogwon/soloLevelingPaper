"""PDF/URL 최초 등록: 한국어 번역 → 벡터 저장 → 검증 → READY 상태 게시.

게시된 등록 결과는 변경하지 않는다. 게시 전 실패한 등록은 저장된 성공 번역을
재사용하고, 번역이 없는 청크만 재시도한다.
"""

import hashlib
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Optional

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.domain.models import (
    EmbeddingSet, JobStage, JobStatus, Paper, ParseRevision, PaperVersion, ProcessingJob,
    IngestionDisposition,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.embeddings.embedder import (
    DEFAULT_MODEL_NAME, embed_texts, embedding_dimension,
)
from solo_leveling.infrastructure.parsing.chunker import chunk_pages
from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages, ExtractedPage
from solo_leveling.infrastructure.storage.vector_store import (
    get_client, upsert_chunk_embeddings, verify_chunk_embeddings, delete_by_embedding_set,
)


def compute_file_hash(pdf_path: str) -> str:
    h = hashlib.sha256()
    with open(pdf_path, 'rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def _published_result(index: dict, reused: bool, db_path: str) -> dict:
    return {
        'job_id': index['job_id'], 'status': 'ready', 'version_id': index['version_id'],
        'parse_revision_id': index['parse_revision_id'],
        'translation_revision_id': index['translation_revision_id'],
        'embedding_set_id': index['embedding_set_id'], 'chunk_count': index['chunk_count'],
        'reused_existing': reused,
        'result_available': True,
        'limitations': repo.get_job(db_path, index['job_id']).limitations,
    }


def get_ingestion_status(db_path: str, job_id: str) -> dict:
    """작업 상태를 조회하고 완료된 경우 검증된 색인 결과를 함께 반환한다."""
    job = repo.get_job(db_path, job_id)
    if job is None:
        raise ValueError('작업을 찾을 수 없습니다.')
    response = {
        'job_id': job.job_id, 'version_id': job.version_id,
        'status': job.status.value, 'stage': job.stage.value if job.stage else None,
        'limitations': job.limitations, 'result_available': False,
    }
    if job.status == JobStatus.READY:
        index = repo.get_search_index(db_path, job.version_id)
        if index is None or index['job_id'] != job.job_id:
            raise ValueError('완료 작업과 색인 정보가 일치하지 않습니다.')
        response['result_available'] = True
        response['result'] = _published_result(index, True, db_path)
    return response


def _ingest_pages(
    db_path: str, chroma_dir: str, pages_provider: Callable[[], list],
    version_id: str, job: ProcessingJob, paper_id: str, reused: bool,
    embedding_model: str, translation_service: TranslationService,
    translation_settings: TranslationSettings,
) -> dict:
    """파싱 이후(번역→임베딩→게시) 공통 로직.

    PDF 직접 등록, URL-PDF, URL-HTML 세 경로가 모두 이 함수를 공유한다.
    각 호출부는 pages_provider로 "아직 파싱 안 됐을 때만 호출되는" 페이지
    추출 함수를 넘기면 된다 (이미 파싱된 청크가 있으면 재사용하고 호출 안 함).
    """
    client = None
    embedding_set = None
    parse_revision = None
    translation_id = None
    limitations = []
    published = False
    try:
        parse_revision = repo.get_latest_parse_revision(db_path, version_id)
        chunks = repo.get_chunks_by_parse_revision(db_path, parse_revision.parse_revision_id) if parse_revision else []
        if not chunks:
            pages = pages_provider()
            parse_revision = ParseRevision(str(uuid.uuid4()), version_id, len(pages))
            chunks = chunk_pages(parse_revision.parse_revision_id, pages)
            # 파싱 결과 전체를 원자적으로 저장해 중단 시 절반만 남는 것을 막는다.
            with closing(repo.get_connection(db_path)) as conn, conn:
                conn.execute('INSERT INTO parse_revisions VALUES (?, ?, ?, ?)',
                             (parse_revision.parse_revision_id, version_id, len(pages), parse_revision.created_at))
                conn.executemany('''INSERT INTO chunks
                    (chunk_id, parse_revision_id, chunk_index, original_text, text,
                     translation_revision_id, printed_page_label, pdf_page, section_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    [(c.chunk_id, c.parse_revision_id, c.chunk_index, c.original_text, c.text,
                      c.translation_revision_id, c.printed_page_label, c.pdf_page, c.section_id) for c in chunks])
        if not chunks:
            limitations = ['extraction_empty']
        else:
            repo.update_job_status(db_path, job.job_id, JobStatus.PROCESSING, JobStage.TRANSLATE)
            metadata = repo.get_translation_metadata(db_path, parse_revision.parse_revision_id)
            pending = [c for c in chunks if c.text is None]
            if metadata:
                translation_id = metadata['translation_revision_id']
                stored_settings = TranslationSettings(*(metadata[k] for k in
                    ('provider', 'model', 'prompt_version', 'target_language')))
                if pending and stored_settings != translation_settings:
                    raise ValueError('retry must use original translation settings')
                service = TranslationService(translation_service.provider, lambda: translation_id)
            else:
                service = translation_service
            if pending:
                batch = service.translate(pending, translation_settings)
                repo.save_translation_batch(db_path, batch)
                translation_id = batch.revision.translation_revision_id
            if translation_id is None:
                raise ValueError('translated chunks have no persisted revision')
            translated = repo.get_translated_chunks(db_path, parse_revision.parse_revision_id, translation_id)
            failures = repo.get_translation_failures(db_path, translation_id)
            limitations = [f"translation_failed:{f['chunk_id']}:{f['failure_code']}" for f in failures]
            if translated:
                repo.update_job_status(db_path, job.job_id, JobStatus.PROCESSING, JobStage.INDEX)
                texts = [c.text for c in translated]
                vectors = embed_texts(texts, model_name=embedding_model)
                dim = embedding_dimension(embedding_model)
                embedding_set = EmbeddingSet(str(uuid.uuid4()), translation_id, embedding_model, dim)
                client = get_client(chroma_dir)
                # 임베딩 세트별 컬렉션을 사용해 이후 모델 차원이 달라져도 공존 저장 가능
                collection = f'chunks-{embedding_set.embedding_set_id}'
                upsert_chunk_embeddings(
                    client, [c.chunk_id for c in translated], vectors,
                    embedding_set.embedding_set_id, paper_id, version_id, texts,
                    collection_name=collection,
                )
                verify_chunk_embeddings(
                    client, [c.chunk_id for c in translated], texts, embedding_set.embedding_set_id,
                    paper_id, version_id, dim, collection_name=collection,
                )
                repo.publish_search_index(db_path, version_id, parse_revision.parse_revision_id,
                                          embedding_set, job.job_id, len(translated), limitations)
                published = True
                return _published_result(repo.get_search_index(db_path, version_id), reused, db_path)
            limitations = ['translation_failed', *limitations]
        repo.update_job_status(db_path, job.job_id, JobStatus.FAILED, limitations=limitations)
        return {
            'job_id': job.job_id, 'status': 'failed', 'version_id': version_id,
            'result_available': False,
            'parse_revision_id': parse_revision.parse_revision_id,
            'translation_revision_id': translation_id, 'embedding_set_id': None,
            'chunk_count': 0, 'reused_existing': reused, 'limitations': limitations,
        }
    except Exception:
        if not published:
            if client is not None and embedding_set is not None:
                try:
                    delete_by_embedding_set(client, embedding_set.embedding_set_id,
                                            collection_name=f'chunks-{embedding_set.embedding_set_id}')
                except Exception:
                    limitations.append('index_cleanup_failed')
            repo.update_job_status(db_path, job.job_id, JobStatus.FAILED,
                                   limitations=[*limitations, 'ingestion_failed'])
        raise


def register_and_ingest(
    db_path: str, chroma_dir: str, pdf_path: str,
    original_filename: Optional[str] = None, paper_id: Optional[str] = None,
    embedding_model: str = DEFAULT_MODEL_NAME, *,
    translation_service: TranslationService, translation_settings: TranslationSettings,
) -> dict:
    """번역 설정을 명시적으로 받아야 하며, 원문으로 자동 폴백하지 않는다.

    중복 확인 범위는 paper_id와 파일 해시. 호출자가 가져오기 루트의 접근 권한을
    검증해야 한다. 게시된 부분 번역은 그대로 재사용하며, 게시 전 미완 구간을
    재시도하는 기능은 별도 후속 작업이다.
    """
    init_db(db_path)
    paper_id = paper_id or str(uuid.uuid4())
    original_filename = original_filename or Path(pdf_path).name
    file_hash = compute_file_hash(pdf_path)
    registration = repo.prepare_ingestion(
        db_path, Paper(paper_id=paper_id, source_kind='local_file'),
        PaperVersion(str(uuid.uuid4()), paper_id, file_hash, pdf_path, original_filename),
        str(uuid.uuid4()),
    )
    if registration.disposition != IngestionDisposition.STARTED:
        # 상태 조회 직전에 완료된 경우에도 최신 완료 결과를 반환한다.
        status = get_ingestion_status(db_path, registration.job_id)
        if status['result_available']:
            return status['result']
        return {**status, 'reused_existing': True}

    version_id = registration.version_id
    reused = registration.reused_existing
    job = ProcessingJob(registration.job_id, version_id)
    return _ingest_pages(
        db_path, chroma_dir, lambda: extract_pages(pdf_path),
        version_id, job, paper_id, reused, embedding_model,
        translation_service, translation_settings,
    )


def register_and_ingest_url(
    db_path: str, chroma_dir: str, url: str, tmp_pdf_dir: str,
    paper_id: Optional[str] = None, embedding_model: str = DEFAULT_MODEL_NAME, *,
    translation_service: TranslationService, translation_settings: TranslationSettings,
) -> dict:
    """URL로 논문을 등록한다(M1 기능 ①의 URL 부분).

    PDF 링크면 다운로드해 register_and_ingest를 그대로 재사용한다(중복 로직 없음).
    일반 웹페이지(HTML)면 본문을 추출해 "페이지 1개"로 취급하고, register_and_ingest와
    동일한 번역·임베딩·게시 파이프라인(_ingest_pages)을 공유한다.

    주의: HTML로 등록된 논문은 pdf_page가 항상 1이고 printed_page_label도 항상
    None이다 — 실제 인쇄 페이지 개념이 없기 때문이다.

    tmp_pdf_dir: PDF로 판별된 경우 다운로드한 바이트를 저장할 임시 폴더.
    URL 사설망 차단은 infrastructure/parsing/url_ingest.py에서 처리하며, 이는
    최소 방어선이다 — 정식 네트워크 정책은 C 담당 영역과 통합이 필요하다.
    """
    from solo_leveling.infrastructure.parsing.url_ingest import fetch_and_classify, extract_html_text

    kind, response = fetch_and_classify(url)

    if kind == 'pdf':
        Path(tmp_pdf_dir).mkdir(parents=True, exist_ok=True)
        tmp_path = Path(tmp_pdf_dir) / f'{uuid.uuid4()}.pdf'
        with open(tmp_path, 'wb') as f:
            f.write(response.content)
        result = register_and_ingest(
            db_path, chroma_dir, str(tmp_path),
            original_filename=Path(url).name or 'downloaded.pdf', paper_id=paper_id,
            embedding_model=embedding_model,
            translation_service=translation_service, translation_settings=translation_settings,
        )
        result['source_kind'] = 'url_pdf'
        return result

    if kind != 'html':
        raise ValueError(f'지원하지 않는 콘텐츠 타입입니다(kind={kind}): {url}')

    text = extract_html_text(response.text)
    file_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

    init_db(db_path)
    paper_id = paper_id or str(uuid.uuid4())
    registration = repo.prepare_ingestion(
        db_path, Paper(paper_id=paper_id, source_kind='url'),
        PaperVersion(str(uuid.uuid4()), paper_id, file_hash, url, url),
        str(uuid.uuid4()),
    )
    if registration.disposition != IngestionDisposition.STARTED:
        status = get_ingestion_status(db_path, registration.job_id)
        if status['result_available']:
            result = status['result']
        else:
            result = {**status, 'reused_existing': True}
        result['source_kind'] = 'url_html'
        return result

    version_id = registration.version_id
    reused = registration.reused_existing
    job = ProcessingJob(registration.job_id, version_id)
    result = _ingest_pages(
        db_path, chroma_dir,
        lambda: [ExtractedPage(pdf_page=1, text=text, printed_page_label=None)],
        version_id, job, paper_id, reused, embedding_model,
        translation_service, translation_settings,
    )
    result['source_kind'] = 'url_html'
    return result