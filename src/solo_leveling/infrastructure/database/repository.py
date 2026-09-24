"""
infrastructure/database — 리포지토리

도메인 모델(dataclass)과 SQLite 행 사이를 변환한다.
processing_jobs.limitations는 JSON 문자열로 저장한다(SQLite에 배열 타입이 없어서).
"""
import json
from contextlib import closing
from pathlib import PureWindowsPath, PurePosixPath
from typing import List, Optional

from solo_leveling.domain.models import (
    Chunk,
    JobStage,
    JobStatus,
    Paper,
    ParseRevision,
    PaperVersion,
    ProcessingJob,
    TranslationRevision,
    EmbeddingSet,
    LearningContext,
    Evidence,
    now_iso,
)
from solo_leveling.domain.translation import TranslationBatchResult
from solo_leveling.domain.evidence_qa import EvidenceDetail, GetEvidenceResult, require_text
from solo_leveling.infrastructure.database.schema import get_connection


# ── 논문 / 버전 / 파싱 리비전 ───────────────────────────────────
def insert_paper(db_path: str, paper: Paper) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO papers (paper_id, title, source_kind, created_at) VALUES (?, ?, ?, ?)",
        (paper.paper_id, paper.title, paper.source_kind, paper.created_at),
    )
    conn.commit()
    conn.close()


def insert_version(db_path: str, version: PaperVersion) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO paper_versions
           (version_id, paper_id, file_hash, stored_path, original_filename, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            version.version_id,
            version.paper_id,
            version.file_hash,
            version.stored_path,
            version.original_filename,
            version.created_at,
        ),
    )
    conn.commit()
    conn.close()


def find_version_by_hash(db_path: str, paper_id: str, file_hash: str) -> Optional[PaperVersion]:
    """같은 해시의 파일이 이미 등록돼 있으면 그 버전을 반환 (중복 등록 방지)"""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM paper_versions WHERE paper_id = ? AND file_hash = ?",
        (paper_id, file_hash),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return PaperVersion(**dict(row))


def insert_parse_revision(db_path: str, revision: ParseRevision) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO parse_revisions (parse_revision_id, version_id, page_count, created_at) VALUES (?, ?, ?, ?)",
        (revision.parse_revision_id, revision.version_id, revision.page_count, revision.created_at),
    )
    conn.commit()
    conn.close()


# ── 청크 ─────────────────────────────────────────────────────────
def insert_chunks(db_path: str, chunks: List[Chunk]) -> None:
    conn = get_connection(db_path)
    conn.executemany(
        """INSERT INTO chunks
           (chunk_id, parse_revision_id, chunk_index, original_text, text,
            translation_revision_id, printed_page_label, pdf_page, section_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                c.chunk_id,
                c.parse_revision_id,
                c.chunk_index,
                c.original_text,
                c.text,
                c.translation_revision_id,
                c.printed_page_label,
                c.pdf_page,
                c.section_id,
            )
            for c in chunks
        ],
    )
    conn.commit()
    conn.close()


def get_chunks_by_parse_revision(db_path: str, parse_revision_id: str) -> List[Chunk]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM chunks WHERE parse_revision_id = ? ORDER BY chunk_index",
        (parse_revision_id,),
    ).fetchall()
    conn.close()
    return [Chunk(**dict(r)) for r in rows]


def get_chunk(db_path: str, chunk_id: str) -> Optional[Chunk]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
    conn.close()
    return Chunk(**dict(row)) if row else None


# ── 처리 작업 ────────────────────────────────────────────────────
def insert_job(db_path: str, job: ProcessingJob) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO processing_jobs
           (job_id, version_id, status, stage, limitations, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            job.job_id,
            job.version_id,
            job.status.value,
            job.stage.value if job.stage else None,
            json.dumps(job.limitations, ensure_ascii=False),
            job.created_at,
            job.updated_at,
        ),
    )
    conn.commit()
    conn.close()


def update_job_status(
    db_path: str,
    job_id: str,
    status: JobStatus,
    stage: Optional[JobStage] = None,
    limitations: Optional[list] = None,
    updated_at: Optional[str] = None,
) -> None:
    from solo_leveling.domain.models import now_iso

    conn = get_connection(db_path)
    fields = ["status = ?", "updated_at = ?"]
    params: list = [status.value, updated_at or now_iso()]
    if stage is not None:
        fields.append("stage = ?")
        params.append(stage.value)
    if limitations is not None:
        fields.append("limitations = ?")
        params.append(json.dumps(limitations, ensure_ascii=False))
    params.append(job_id)
    conn.execute(f"UPDATE processing_jobs SET {', '.join(fields)} WHERE job_id = ?", params)
    conn.commit()
    conn.close()


def get_job(db_path: str, job_id: str) -> Optional[ProcessingJob]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["status"] = JobStatus(d["status"])
    d["stage"] = JobStage(d["stage"]) if d["stage"] else None
    d["limitations"] = json.loads(d["limitations"])
    return ProcessingJob(**d)


def mark_interrupted_jobs_on_startup(db_path: str) -> int:
    """
    22번 문서: "재시작 시 미완료 processing 작업은 interrupted로 표시한다.
    자동 이어 실행을 보장하지 않는다."
    반환: interrupted로 바뀐 작업 수
    """
    from solo_leveling.domain.models import now_iso

    conn = get_connection(db_path)
    cur = conn.execute(
        "UPDATE processing_jobs SET status = ?, updated_at = ? WHERE status = ?",
        (JobStatus.INTERRUPTED.value, now_iso(), JobStatus.PROCESSING.value),
    )
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count


def get_latest_parse_revision(db_path: str, version_id: str) -> Optional[ParseRevision]:
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            'SELECT * FROM parse_revisions WHERE version_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (version_id,),
        ).fetchone()
        return ParseRevision(**dict(row)) if row else None


def get_translation_metadata(db_path: str, parse_revision_id: str) -> Optional[dict]:
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('SELECT * FROM translation_revisions WHERE parse_revision_id=?',
                            (parse_revision_id,)).fetchall()
        if len(rows) > 1:
            raise ValueError('multiple translations are not supported by the initial-translation policy')
        return dict(rows[0]) if rows else None


def save_translation_batch(db_path: str, batch: TranslationBatchResult) -> None:
    """최초 번역을 원자적으로 저장하고, 재시도 시 실패한 청크만 채운다.

    동일한 내용은 반복 저장해도 결과가 같다. 게시된 번역과 성공한 번역문은
    변경할 수 없다. SQLite BEGIN IMMEDIATE로 동시 쓰기를 순차 처리한다.
    """
    batch.validate()
    rev, settings = batch.revision, batch.settings
    metadata = (rev.parse_revision_id, settings.provider, settings.model,
                settings.prompt_version, settings.target_language)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM translation_revisions WHERE translation_revision_id=?',
                                (rev.translation_revision_id,)).fetchone()
        if existing:
            if tuple(existing[k] for k in ('parse_revision_id', 'provider', 'model', 'prompt_version', 'target_language')) != metadata:
                raise ValueError('translation metadata conflict')
        else:
            if conn.execute('SELECT 1 FROM translation_revisions WHERE parse_revision_id=?',
                            (rev.parse_revision_id,)).fetchone():
                raise ValueError('initial translation already exists; automatic retranslation is disabled')
            conn.execute(
                '''INSERT INTO translation_revisions
                   (translation_revision_id, parse_revision_id, provider, model, prompt_version, target_language, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (rev.translation_revision_id, *metadata, rev.created_at),
            )
        published = conn.execute('SELECT 1 FROM search_indexes WHERE translation_revision_id=?',
                                 (rev.translation_revision_id,)).fetchone()
        for item in batch.items:
            row = conn.execute('SELECT * FROM chunks WHERE chunk_id=?', (item.request.chunk_id,)).fetchone()
            if not row or (row['parse_revision_id'], row['original_text']) != (
                rev.parse_revision_id, item.request.original_text,
            ):
                raise ValueError('translation source does not match database')
            prior = conn.execute('SELECT failure_code FROM translation_results WHERE translation_revision_id=? AND chunk_id=?',
                                 (rev.translation_revision_id, row['chunk_id'])).fetchone()
            code = item.failure_code.value if item.failure_code else None
            identical = (prior is not None and prior['failure_code'] == code
                         and (not item.succeeded or (row['text'], row['translation_revision_id']) == (item.text, rev.translation_revision_id)))
            if identical:
                continue
            if published:
                raise ValueError('published translation cannot be changed')
            if row['text'] is not None or row['translation_revision_id'] is not None:
                raise ValueError('successful translation cannot be overwritten')
            if item.succeeded:
                count = conn.execute('UPDATE chunks SET text=?, translation_revision_id=? WHERE chunk_id=? AND text IS NULL AND translation_revision_id IS NULL',
                                     (item.text, rev.translation_revision_id, row['chunk_id'])).rowcount
                if count != 1:
                    raise ValueError('translation update conflict')
            conn.execute('''INSERT INTO translation_results VALUES (?, ?, ?)
                            ON CONFLICT(translation_revision_id, chunk_id) DO UPDATE SET failure_code=excluded.failure_code''',
                         (rev.translation_revision_id, row['chunk_id'], code))


def get_translation_failures(db_path: str, translation_revision_id: str) -> list[dict]:
    with closing(get_connection(db_path)) as conn:
        return [dict(r) for r in conn.execute(
            'SELECT chunk_id, failure_code FROM translation_results WHERE translation_revision_id=? AND failure_code IS NOT NULL ORDER BY chunk_id',
            (translation_revision_id,),
        )]


def get_translated_chunks(db_path: str, parse_revision_id: str, translation_revision_id: str) -> List[Chunk]:
    with closing(get_connection(db_path)) as conn:
        if not conn.execute('SELECT 1 FROM translation_revisions WHERE translation_revision_id=? AND parse_revision_id=?',
                            (translation_revision_id, parse_revision_id)).fetchone():
            raise ValueError('translation does not belong to parse revision')
        return [Chunk(**dict(row)) for row in conn.execute(
            'SELECT * FROM chunks WHERE parse_revision_id=? AND translation_revision_id=? AND text IS NOT NULL ORDER BY chunk_index',
            (parse_revision_id, translation_revision_id),
        )]


def get_search_index(db_path: str, version_id: str, parse_revision_id: str | None = None,
                     translation_revision_id: str | None = None) -> Optional[dict]:
    """게시된 색인만 조회하며, 리비전을 지정하면 정확히 일치하는 경우만 반환한다."""
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('''SELECT s.*, e.model_name, e.dimension FROM search_indexes s
                              JOIN embedding_sets e USING(embedding_set_id) WHERE s.version_id=?''',
                           (version_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if ((parse_revision_id is not None and result['parse_revision_id'] != parse_revision_id)
                or (translation_revision_id is not None and result['translation_revision_id'] != translation_revision_id)):
            return None
        return result


def publish_search_index(db_path: str, version_id: str, parse_revision_id: str,
                         embedding_set: EmbeddingSet, job_id: str, chunk_count: int,
                         limitations: list[str]) -> None:
    """벡터 검증 후 색인 게시와 READY 상태 전환을 하나의 트랜잭션으로 처리한다."""
    if type(chunk_count) is not int or chunk_count < 1 or type(embedding_set.dimension) is not int or embedding_set.dimension < 1:
        raise ValueError('positive chunk count and embedding dimension required')
    require_text(embedding_set.embedding_set_id, 'embedding_set_id')
    require_text(embedding_set.model_name, 'model_name')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        valid = conn.execute('''SELECT 1 FROM translation_revisions t
                                JOIN parse_revisions p USING(parse_revision_id)
                                JOIN processing_jobs j ON j.version_id=p.version_id
                                WHERE t.translation_revision_id=? AND p.parse_revision_id=?
                                AND p.version_id=? AND j.job_id=? AND j.status='processing' ''',
                             (embedding_set.translation_revision_id, parse_revision_id, version_id, job_id)).fetchone()
        actual = conn.execute('SELECT COUNT(*) FROM chunks WHERE parse_revision_id=? AND translation_revision_id=? AND text IS NOT NULL',
                              (parse_revision_id, embedding_set.translation_revision_id)).fetchone()[0]
        if not valid or actual != chunk_count:
            raise ValueError('index does not match the job and translated chunks')
        conn.execute('INSERT INTO embedding_sets VALUES (?, ?, ?, ?, ?)',
                     (embedding_set.embedding_set_id, embedding_set.translation_revision_id,
                      embedding_set.model_name, embedding_set.dimension, embedding_set.created_at))
        conn.execute('INSERT INTO search_indexes VALUES (?, ?, ?, ?, ?, ?)',
                     (embedding_set.embedding_set_id, version_id, parse_revision_id,
                      embedding_set.translation_revision_id, job_id, chunk_count))
        conn.execute("UPDATE processing_jobs SET status='ready', stage='index', limitations=?, updated_at=? WHERE job_id=?",
                     (json.dumps(limitations, ensure_ascii=False), now_iso(), job_id))


def claim_ingestion_job(db_path: str, job: ProcessingJob) -> None:
    """동일 버전의 동시 등록을 거부한다. 시작 시 중단된 작업의 상태 정리가 필요하다."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM processing_jobs WHERE version_id=? AND status IN ('queued','processing')",
                        (job.version_id,)).fetchone():
            raise ValueError('REQUEST_IN_PROGRESS')
        if conn.execute('SELECT 1 FROM search_indexes WHERE version_id=?', (job.version_id,)).fetchone():
            raise ValueError('version already published')
        conn.execute('INSERT INTO processing_jobs VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (job.job_id, job.version_id, 'processing', 'parse', '[]', job.created_at, job.updated_at))


def create_learning_context(db_path: str, context: LearningContext) -> None:
    require_text(context.context_id, 'context_id')
    if context.goal not in ('understand', 'implement', 'skim'):
        raise ValueError('invalid learning goal')
    if not isinstance(context.known_concepts, list) or any(not isinstance(c, str) or not c.strip() for c in context.known_concepts):
        raise ValueError('known_concepts must contain nonempty strings')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('''SELECT 1 FROM search_indexes WHERE embedding_set_id=? AND version_id=?
                               AND parse_revision_id=? AND translation_revision_id=?''',
                            (context.embedding_set_id, context.version_id, context.parse_revision_id,
                             context.translation_revision_id)).fetchone():
            raise ValueError('context must reference a published index with matching revisions')
        conn.execute('''INSERT INTO learning_contexts
                        (context_id, version_id, parse_revision_id, translation_revision_id,
                         embedding_set_id, goal, known_concepts, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (context.context_id, context.version_id, context.parse_revision_id,
                      context.translation_revision_id, context.embedding_set_id, context.goal,
                      json.dumps(context.known_concepts, ensure_ascii=False), context.created_at))


def get_learning_context(db_path: str, context_id: str) -> Optional[LearningContext]:
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT * FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone()
        if not row:
            return None
        values = dict(row)
        values['known_concepts'] = json.loads(values['known_concepts'])
        return LearningContext(**values)


def _evidence_detail(conn, context_id: str, evidence: Evidence) -> EvidenceDetail:
    row = conn.execute('''SELECT c.*, p.original_filename, l.version_id FROM learning_contexts l
                          JOIN chunks c ON c.parse_revision_id=l.parse_revision_id
                                       AND c.translation_revision_id=l.translation_revision_id
                          JOIN paper_versions p ON p.version_id=l.version_id
                          WHERE l.context_id=? AND c.chunk_id=?''',
                       (context_id, evidence.chunk_id)).fetchone()
    if not row:
        raise ValueError('evidence chunk is outside context')
    # Windows와 Unix 경로 모두에서 파일명만 추출하여 경로 노출을 막는다.
    filename = PureWindowsPath(PurePosixPath(row['original_filename']).name).name
    detail = EvidenceDetail(
        evidence_id=evidence.evidence_id, chunk_id=evidence.chunk_id, version_id=row['version_id'],
        parse_revision_id=row['parse_revision_id'], translation_revision_id=row['translation_revision_id'],
        printed_page_label=row['printed_page_label'], pdf_page=row['pdf_page'],
        quote_ko=evidence.quote_ko, quote_original=evidence.quote_original, file_display_name=filename,
    )
    if detail.quote_original not in row['original_text']:
        raise ValueError('original quote not found')
    if detail.quote_ko is not None and (row['text'] is None or detail.quote_ko not in row['text']):
        raise ValueError('translated quote not found')
    return detail


def save_evidences(db_path: str, context_id: str, evidences: List[Evidence]) -> None:
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone():
            raise ValueError('context not found')
        for evidence in evidences:
            _evidence_detail(conn, context_id, evidence)
            conn.execute('INSERT INTO evidences VALUES (?, ?, ?, ?, ?, ?)',
                         (evidence.evidence_id, context_id, evidence.chunk_id,
                          evidence.quote_ko, evidence.quote_original, now_iso()))


def get_evidences(db_path: str, context_id: str, evidence_ids: List[str]) -> GetEvidenceResult:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError('duplicate evidence IDs')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN')
        if not conn.execute('SELECT 1 FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone():
            raise ValueError('context not found')
        details = []
        for evidence_id in evidence_ids:
            row = conn.execute('SELECT * FROM evidences WHERE context_id=? AND evidence_id=?',
                               (context_id, evidence_id)).fetchone()
            if not row:
                raise ValueError('evidence not found in context')
            details.append(_evidence_detail(conn, context_id, Evidence(
                row['evidence_id'], row['chunk_id'], row['quote_ko'], row['quote_original'],
            )))
        return GetEvidenceResult(tuple(details))
