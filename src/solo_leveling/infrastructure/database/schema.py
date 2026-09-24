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
    prompt_version TEXT,
    target_language TEXT,
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_versions_paper_hash ON paper_versions(paper_id, file_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_version ON processing_jobs(version_id);

CREATE TABLE IF NOT EXISTS translation_results (
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    failure_code TEXT,
    PRIMARY KEY (translation_revision_id, chunk_id)
);

-- 저장된 벡터를 다시 읽어 검증한 후에만 색인 정보를 게시한다.
CREATE TABLE IF NOT EXISTS search_indexes (
    embedding_set_id TEXT PRIMARY KEY REFERENCES embedding_sets(embedding_set_id),
    version_id TEXT NOT NULL UNIQUE REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0)
);

CREATE TABLE IF NOT EXISTS learning_contexts (
    context_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    embedding_set_id TEXT NOT NULL REFERENCES search_indexes(embedding_set_id),
    goal TEXT NOT NULL CHECK (goal IN ('understand', 'implement', 'skim')),
    known_concepts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS default_learning_contexts (
    embedding_set_id TEXT PRIMARY KEY REFERENCES search_indexes(embedding_set_id),
    context_id TEXT NOT NULL UNIQUE REFERENCES learning_contexts(context_id)
);

CREATE TABLE IF NOT EXISTS evidences (
    evidence_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    quote_ko TEXT,
    quote_original TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidences_context ON evidences(context_id);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # 연결마다 활성화
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        # 기존 생성된 DB에 컬럼을 추가하는 형식.
        # 초기값은 NULL로 번역 설정을 임의로 채우지 않는다.
        with conn:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_revisions)')}
            for name in ('prompt_version', 'target_language'):
                if name not in columns:
                    conn.execute(f'ALTER TABLE translation_revisions ADD COLUMN {name} TEXT')
    finally:
        conn.close()
