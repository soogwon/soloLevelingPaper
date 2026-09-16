"""
infrastructure/database — 리포지토리

도메인 모델(dataclass)과 SQLite 행 사이를 변환한다.
processing_jobs.limitations는 JSON 문자열로 저장한다(SQLite에 배열 타입이 없어서).
"""
import json
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
)
from solo_leveling.infrastructure.database.schema import get_connection


# ── papers / versions / parse revisions ─────────────────────────
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


# ── chunks ───────────────────────────────────────────────────────
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


# ── processing jobs ──────────────────────────────────────────────
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
