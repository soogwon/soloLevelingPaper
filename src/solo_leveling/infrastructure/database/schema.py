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

CREATE TABLE IF NOT EXISTS ingestion_requests (
    request_key TEXT PRIMARY KEY,
    input_fingerprint TEXT NOT NULL,
    paper_id TEXT NOT NULL REFERENCES papers(paper_id),
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    created_at TEXT NOT NULL
);

-- 번역 결과는 (번역 리비전, 청크)마다 한 행이다(#30). 성공 행은 failure_code가 비고 translated_text가 있다.
-- 이전 DB에서는 번역 본문이 chunks.text에만 있었으므로 init_db가 translated_text를 채운다(chunks.text는 아직 지우지 않는다).
CREATE TABLE IF NOT EXISTS translation_results (
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    failure_code TEXT,
    translated_text TEXT,
    PRIMARY KEY (translation_revision_id, chunk_id)
);

-- 저장된 벡터를 다시 읽어 검증한 후에만 색인 정보를 게시한다.
-- 논문 버전에 색인이 여러 개 있을 수 있다(#30: 번역 실패 구간을 복구한 새 색인). 새 학습이 고르는 기본 색인은
-- is_default=1인 행이고 버전당 하나만 허용한다(부분 유일 인덱스는 init_db가 이관 뒤에 만든다).
-- 기존 context는 자신이 가리키는 embedding_set_id의 색인을 그대로 쓴다.
CREATE TABLE IF NOT EXISTS search_indexes (
    embedding_set_id TEXT PRIMARY KEY REFERENCES embedding_sets(embedding_set_id),
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
    is_default INTEGER NOT NULL DEFAULT 1 CHECK (is_default IN (0, 1))
);

-- 번역 복구 작업 잠금(#30). 논문 버전당 진행 중인 복구는 하나만 허용한다(version_id가 기본 키).
-- 잠금 행은 복구 게시나 실패 처리와 같은 트랜잭션에서 지우고, 작업이 중단돼 남은 행은 다음 시작 때 치운다.
CREATE TABLE IF NOT EXISTS translation_recovery_locks (
    version_id TEXT PRIMARY KEY REFERENCES paper_versions(version_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES processing_jobs(job_id),
    base_translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    new_translation_revision_id TEXT REFERENCES translation_revisions(translation_revision_id),
    created_at TEXT NOT NULL
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


-- 근거 연결형 요약(M1-05). 맥락(context)마다 전체(paper)·섹션별 요약을 하나씩 둔다.
-- 항목의 evidence_ids는 같은 맥락의 evidences를 가리키며, 저장할 때 리포지토리가 확인한다.
CREATE TABLE IF NOT EXISTS summaries (
    summary_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (context_id, scope)
);
CREATE TABLE IF NOT EXISTS summary_items (
    summary_id TEXT NOT NULL REFERENCES summaries(summary_id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    PRIMARY KEY (summary_id, position)
);

-- 근거 기반 단계별 퀴즈(M1-09). 맥락(context)마다 학습 단계별 퀴즈를 하나씩 둔다.
-- 문항의 evidence_ids는 같은 맥락의 evidences를 가리키며, 저장할 때 리포지토리가 확인한다.
-- 객관식은 choices(JSON 배열)와 correct_index, 단답형은 answer와 grading_criteria를 가진다.
CREATE TABLE IF NOT EXISTS quizzes (
    quiz_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    step TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (context_id, step)
);
CREATE TABLE IF NOT EXISTS quiz_questions (
    quiz_id TEXT NOT NULL REFERENCES quizzes(quiz_id),
    position INTEGER NOT NULL,
    question_type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    choices TEXT NOT NULL DEFAULT '[]',
    correct_index INTEGER,
    answer TEXT NOT NULL,
    grading_criteria TEXT NOT NULL DEFAULT '',
    evidence_ids TEXT NOT NULL,
    PRIMARY KEY (quiz_id, position)
);

-- 퀴즈 시도(M1-10). 제출 시점의 문항 내용을 복사해 두므로 quizzes/quiz_questions에는 연결하지 않는다.
-- (퀴즈를 다시 만들면 quizzes 행이 교체되는데, 과거 시도의 점수와 오답 근거는 계속 볼 수 있어야 한다.)
-- 답안의 evidence_ids는 같은 맥락의 evidences를 가리키며, 저장할 때 리포지토리가 확인한다.
CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    quiz_id TEXT NOT NULL,
    step TEXT NOT NULL,
    correct_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    submitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_context ON quiz_attempts(context_id, step, submitted_at);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_context_time ON quiz_attempts(context_id, submitted_at);
CREATE TABLE IF NOT EXISTS quiz_attempt_answers (
    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(attempt_id),
    position INTEGER NOT NULL,
    question_type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    choices TEXT NOT NULL DEFAULT '[]',
    selected_index INTEGER,
    submitted_text TEXT,
    correct_answer TEXT NOT NULL,
    grading_criteria TEXT NOT NULL DEFAULT '',
    is_correct INTEGER NOT NULL,
    evidence_ids TEXT NOT NULL,
    PRIMARY KEY (attempt_id, position)
);

-- 메모·하이라이트(M1-11, #56). 기록 하나에 선택 구간이 0개 이상 붙는다(공통 기록 + 선택 구간 두 테이블).
-- 학습 맥락(context)마다 따로 저장하고 다른 맥락에 자동으로 공유하지 않는다.
-- 위치는 저장된 청크 텍스트의 문자 위치(코드 포인트, 0부터, [start_offset, end_offset))와 인용문 스냅샷으로 고정한다.
-- 메모가 가리키는 맥락·청크는 삭제를 막는다(RESTRICT). 메모를 지우면 소속 구간도 같은 트랜잭션에서 함께 지운다.
-- 본문과 인용문은 사용자 데이터라 로그에 남기지 않는다.
CREATE TABLE IF NOT EXISTS annotations (
    annotation_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'highlight')),
    body TEXT NOT NULL DEFAULT '',
    step TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_context ON annotations(context_id, step, created_at);
CREATE TABLE IF NOT EXISTS annotation_segments (
    annotation_id TEXT NOT NULL REFERENCES annotations(annotation_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    target TEXT NOT NULL CHECK (target IN ('original', 'translation')),
    translation_revision_id TEXT REFERENCES translation_revisions(translation_revision_id),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    quote_snapshot TEXT NOT NULL,
    PRIMARY KEY (annotation_id, position),
    CHECK (start_offset >= 0 AND end_offset > start_offset)
);
CREATE INDEX IF NOT EXISTS idx_annotation_segments_chunk ON annotation_segments(chunk_id);
-- 퀴즈 제출 요청 기록(#110). 같은 request_key의 제출은 한 번만 저장한다.
-- 시도와 같은 트랜잭션에서 쓰므로 완료된 요청만 남고, 예약 행은 없다. 이전 시도에는 요청 키를 만들어 넣지 않는다.
-- input_hash는 context_id·step·quiz_id·답안으로 계산한 해시라 답안 본문은 저장하지 않는다.
CREATE TABLE IF NOT EXISTS quiz_submit_requests (
    request_key TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(attempt_id),
    created_at TEXT NOT NULL
);

-- 의미 검증 이력(#83). 검증 실행 한 번과 그때 검사한 주장·참조를 저장한다.
-- 질문·청크 원문·번역·키·전체 API 응답은 저장하지 않고, 주장 문구와 청크 참조(chunk_id + 원문 해시)만 남긴다.
-- 최종 근거(evidences)에는 FK를 걸지 않는다: 제외된 주장만 참조한 근거는 최종 근거 테이블에 없을 수 있다.
-- 맥락을 지우면 그 맥락의 이력(실행·주장·참조)만 함께 지워진다(CASCADE). 이력이 참조하는 청크는 지울 수 없다(RESTRICT).
-- 이력이 근거로 삼을 수 있는 청크는 색인 게시 당시의 성공 청크 집합(search_index_chunks)뿐이다.
CREATE TABLE IF NOT EXISTS search_index_chunks (
    embedding_set_id TEXT NOT NULL REFERENCES embedding_sets(embedding_set_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    PRIMARY KEY (embedding_set_id, chunk_id)
);
-- 임베딩 세트를 만드는 순간(색인 게시와 같은 트랜잭션) 그 번역 리비전의 성공 청크 집합을 기록한다.
CREATE TRIGGER IF NOT EXISTS trg_embedding_set_chunks AFTER INSERT ON embedding_sets
BEGIN
    INSERT OR IGNORE INTO search_index_chunks (embedding_set_id, chunk_id)
    SELECT NEW.embedding_set_id, r.chunk_id
    FROM translation_results r
    JOIN translation_revisions t ON t.translation_revision_id = r.translation_revision_id
    JOIN chunks c ON c.chunk_id = r.chunk_id AND c.parse_revision_id = t.parse_revision_id
    WHERE r.translation_revision_id = NEW.translation_revision_id AND r.failure_code IS NULL;
END;
-- 이 기능 이전에 만든 임베딩 세트는 현재의 성공 청크 집합으로 한 번 채운다(이미 기록된 세트는 건드리지 않는다).
INSERT OR IGNORE INTO search_index_chunks (embedding_set_id, chunk_id)
SELECT e.embedding_set_id, r.chunk_id
FROM embedding_sets e
JOIN translation_revisions t ON t.translation_revision_id = e.translation_revision_id
JOIN translation_results r ON r.translation_revision_id = e.translation_revision_id AND r.failure_code IS NULL
JOIN chunks c ON c.chunk_id = r.chunk_id AND c.parse_revision_id = t.parse_revision_id
WHERE NOT EXISTS (SELECT 1 FROM search_index_chunks x WHERE x.embedding_set_id = e.embedding_set_id);
CREATE TABLE IF NOT EXISTS verification_runs (
    run_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    embedding_set_id TEXT NOT NULL REFERENCES embedding_sets(embedding_set_id),
    model TEXT NOT NULL,
    instruction_version TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    level TEXT NOT NULL CHECK (level IN ('structural_only', 'model_checked')),
    failure_code TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verification_runs_context ON verification_runs(context_id, created_at);
CREATE TABLE IF NOT EXISTS verification_claims (
    run_id TEXT NOT NULL REFERENCES verification_runs(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    claim_id TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('supported', 'partially_supported', 'contradicted', 'undetermined')),
    reason TEXT NOT NULL,
    retained INTEGER NOT NULL CHECK (retained IN (0, 1)),
    PRIMARY KEY (run_id, position),
    UNIQUE (run_id, claim_id)
);
CREATE TABLE IF NOT EXISTS verification_claim_refs (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    ref_position INTEGER NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, position, ref_position),
    FOREIGN KEY (run_id, position) REFERENCES verification_claims(run_id, position) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_verification_refs_chunk ON verification_claim_refs(chunk_id);

-- 읽기 전·후 질문 답변(M1-07/M1-08). 학습 맥락(세션)·시점·질문 템플릿마다 한 행이고, 다시 쓰면 같은 행을 고친다.
-- 질문 문구는 코드의 템플릿이 정하므로 저장하지 않는다. 본문은 사용자 데이터라 로그에 남기지 않는다.
CREATE TABLE IF NOT EXISTS reflection_answers (
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    phase TEXT NOT NULL,
    template_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (context_id, phase, template_id)
);

-- 학습 맥락별 진행 상태. context는 고정 값이고 진행 상태는 계속 바뀌므로 분리한다.
-- 진행 행이 없는 맥락은 조회할 때 기본값(첫 단계)으로 본다.
CREATE TABLE IF NOT EXISTS learning_progress (
    context_id TEXT PRIMARY KEY REFERENCES learning_contexts(context_id),
    current_step TEXT NOT NULL,
    completed_steps TEXT NOT NULL DEFAULT '[]',
    resume_position TEXT,
    last_studied_at TEXT,
    updated_at TEXT NOT NULL
);
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
        _migrate_translation_results(conn)
        _migrate_search_indexes(conn)
        # 기존 생성된 DB에 컬럼을 추가하는 형식.
        # 초기값은 NULL로 번역 설정을 임의로 채우지 않는다.
        with conn:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_revisions)')}
            for name in ('prompt_version', 'target_language'):
                if name not in columns:
                    conn.execute(f'ALTER TABLE translation_revisions ADD COLUMN {name} TEXT')
    finally:
        conn.close()


_SEARCH_INDEXES_COLUMNS = ('embedding_set_id', 'version_id', 'parse_revision_id', 'translation_revision_id',
                           'job_id', 'chunk_count')


def _migrate_translation_results(conn: sqlite3.Connection) -> None:
    """translated_text 컬럼을 더하고, 성공 행의 번역 본문을 chunks.text에서 채운다. 반복 실행해도 같은 결과다."""
    with conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_results)')}
        if 'translated_text' not in columns:
            conn.execute('ALTER TABLE translation_results ADD COLUMN translated_text TEXT')
        conn.execute(
            'UPDATE translation_results SET translated_text=(SELECT c.text FROM chunks c '
            'WHERE c.chunk_id=translation_results.chunk_id '
            'AND c.translation_revision_id=translation_results.translation_revision_id) '
            'WHERE failure_code IS NULL AND translated_text IS NULL')


def _migrate_search_indexes(conn: sqlite3.Connection) -> None:
    """버전당 하나 제약(UNIQUE)을 풀고 is_default를 더한다. SQLite는 제약을 직접 지울 수 없어 표를 새로 만들어 교체한다.

    한 트랜잭션에서 하고, 기존 행은 모두 기본 색인(is_default=1)으로 옮긴다. 외래 키 확인에 실패하면 되돌린다.
    이미 새 모양이면 아무것도 하지 않는다. 마지막에 버전당 기본 색인 하나를 보장하는 부분 유일 인덱스를 만든다.
    """
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(search_indexes)')}
    if 'is_default' not in columns:
        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')  # 표를 바꾸는 동안만 끈다(트랜잭션 밖에서만 바뀐다).
        try:
            conn.execute('BEGIN')
            conn.execute(_SEARCH_INDEXES_NEW_DDL)
            conn.execute(f"INSERT INTO search_indexes_new ({', '.join(_SEARCH_INDEXES_COLUMNS)}, is_default) "
                         f"SELECT {', '.join(_SEARCH_INDEXES_COLUMNS)}, 1 FROM search_indexes")
            conn.execute('DROP TABLE search_indexes')
            conn.execute('ALTER TABLE search_indexes_new RENAME TO search_indexes')
            if conn.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.IntegrityError('search_indexes migration broke a foreign key')
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')
    with conn:
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_search_indexes_default '
                     'ON search_indexes(version_id) WHERE is_default = 1')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_search_indexes_version ON search_indexes(version_id)')


_SEARCH_INDEXES_NEW_DDL = """CREATE TABLE search_indexes_new (
    embedding_set_id TEXT PRIMARY KEY REFERENCES embedding_sets(embedding_set_id),
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
    is_default INTEGER NOT NULL DEFAULT 1 CHECK (is_default IN (0, 1))
)"""
