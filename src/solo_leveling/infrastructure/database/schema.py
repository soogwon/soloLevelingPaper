"""
infrastructure/database — SQLite 스키마

22번 문서: "SQLite가 원문·페이지·삭제 상태의 기준이다. ChromaDB는 재구축
가능한 검색 파생 데이터다." → 이 스키마가 진실의 원천(source of truth)이다.

"SQLite FK는 연결마다 활성화하고 테스트한다" — get_connection()에서 매번 PRAGMA로 켠다.
"""
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT,
    source_kind TEXT NOT NULL DEFAULT 'local_file',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_versions (
    version_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(paper_id),
    file_hash TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parse_revisions (
    parse_revision_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    page_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translation_revisions (
    translation_revision_id TEXT PRIMARY KEY,
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    chunk_index INTEGER NOT NULL,
    original_text TEXT NOT NULL,
    text TEXT,
    translation_revision_id TEXT REFERENCES translation_revisions(translation_revision_id),
    printed_page_label TEXT,
    pdf_page INTEGER NOT NULL,
    section_id TEXT
);

CREATE TABLE IF NOT EXISTS embedding_sets (
    embedding_set_id TEXT PRIMARY KEY,
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    limitations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_parse_revision ON chunks(parse_revision_id);
CREATE INDEX IF NOT EXISTS idx_versions_paper ON paper_versions(paper_id);
CREATE INDEX IF NOT EXISTS idx_jobs_version ON processing_jobs(version_id);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # 연결마다 활성화
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
