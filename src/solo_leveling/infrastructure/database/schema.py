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

CREATE TABLE IF NOT EXISTS sections (
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    section_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    parent_section_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    level INTEGER NOT NULL CHECK (level >= 1),
    start_pdf_page INTEGER NOT NULL CHECK (start_pdf_page >= 1),
    end_pdf_page INTEGER NOT NULL CHECK (end_pdf_page >= start_pdf_page),
    start_chunk_index INTEGER,
    end_chunk_index INTEGER,
    PRIMARY KEY (parse_revision_id, section_id),
    UNIQUE (parse_revision_id, ordinal),
    FOREIGN KEY (parse_revision_id, parent_section_id)
        REFERENCES sections(parse_revision_id, section_id),
    CHECK (start_chunk_index IS NULL OR start_chunk_index >= 0),
    CHECK ((start_chunk_index IS NULL) = (end_chunk_index IS NULL)),
    CHECK (end_chunk_index IS NULL OR end_chunk_index >= start_chunk_index)
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
CREATE INDEX IF NOT EXISTS idx_sections_revision_order ON sections(parse_revision_id, ordinal);
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

-- 기존 PDF의 명시적 재파싱 요청(#116). 같은 version_id 아래 새 파싱 리비전을 만들되
-- 요청 당시 기본 색인을 고정해 게시 경쟁을 감지한다.
CREATE TABLE IF NOT EXISTS reparse_requests (
    request_key TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    base_parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    base_embedding_set_id TEXT NOT NULL REFERENCES search_indexes(embedding_set_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    new_parse_revision_id TEXT REFERENCES parse_revisions(parse_revision_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reparse_locks (
    version_id TEXT PRIMARY KEY REFERENCES paper_versions(version_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES processing_jobs(job_id),
    base_parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    base_embedding_set_id TEXT NOT NULL REFERENCES search_indexes(embedding_set_id),
    new_parse_revision_id TEXT REFERENCES parse_revisions(parse_revision_id),
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
    base_embedding_set_id TEXT REFERENCES search_indexes(embedding_set_id),
    new_translation_revision_id TEXT REFERENCES translation_revisions(translation_revision_id),
    new_embedding_set_id TEXT,
    created_at TEXT NOT NULL
);

-- 복구 호출의 멱등 접수 기록(#222). 본문·키 원문은 로그로 내보내지 않으며 최종 작업 상태는
-- processing_jobs/search_indexes에서 복원한다. 완료 전 새 색인 ID는 잠금 행에도 연결한다.
CREATE TABLE IF NOT EXISTS translation_recovery_requests (
    request_key TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    base_embedding_set_id TEXT NOT NULL REFERENCES search_indexes(embedding_set_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES processing_jobs(job_id),
    new_embedding_set_id TEXT,
    created_at TEXT NOT NULL
);

-- 기존 claim 경로와 멱등 요청 경로가 함께 쓰는 복구 색인 예약 소유권 원장.
-- 실패·중단 뒤 잠금이 지워져도 행을 유지해 지연 정리가 새 작업의 컬렉션을 삭제하지 못하게 한다.
CREATE TABLE IF NOT EXISTS translation_recovery_embedding_set_reservations (
    embedding_set_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES processing_jobs(job_id),
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

-- 번역 복구 완료 뒤 사용자가 선택한 색인과 학습 맥락의 멱등 연결(#223).
-- 사용자 입력 본문이나 known_concepts를 이 선택 기록에 중복 저장하지 않는다.
CREATE TABLE IF NOT EXISTS translation_recovery_context_selections (
    selection_key TEXT PRIMARY KEY,
    request_key TEXT NOT NULL REFERENCES translation_recovery_requests(request_key),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    version_id TEXT NOT NULL REFERENCES paper_versions(version_id),
    embedding_set_id TEXT NOT NULL REFERENCES search_indexes(embedding_set_id),
    context_id TEXT NOT NULL UNIQUE REFERENCES learning_contexts(context_id),
    created_at TEXT NOT NULL
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

-- 원문 출처 연결형 키워드·중요 문장(#229). summary claim과 별도 수명주기를 갖는다.
-- collection이 없으면 미작성 상태다. 일부 kind만 저장된 collection은 부분 작성 상태다.
CREATE TABLE IF NOT EXISTS paper_insight_collections (
    collection_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id),
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (context_id, scope)
);
CREATE TABLE IF NOT EXISTS paper_insights (
    collection_id TEXT NOT NULL REFERENCES paper_insight_collections(collection_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    insight_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('keyword', 'important_sentence')),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    content_kind TEXT NOT NULL CHECK (content_kind IN ('extracted', 'generated')),
    producer TEXT NOT NULL CHECK (producer IN ('extractor', 'model', 'user')),
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT REFERENCES translation_revisions(translation_revision_id),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    quote_original TEXT NOT NULL,
    producer_version TEXT,
    PRIMARY KEY (collection_id, position),
    CHECK (start_offset >= 0 AND end_offset > start_offset),
    CHECK ((content_kind = 'extracted' AND producer = 'extractor') OR
           (content_kind = 'generated' AND producer IN ('model', 'user')))
);
CREATE INDEX IF NOT EXISTS idx_paper_insights_chunk ON paper_insights(chunk_id);

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
    submitted_at TEXT NOT NULL,
    -- 같은 제출 시각의 순서를 정하는 영속 저장 순번(#176). 저장할 때 (기존 최댓값 + 1)을 같은 쓰기 트랜잭션에서 붙이고
    -- 한 번 붙으면 바꾸지 않는다. 암묵적 rowid는 INTEGER PRIMARY KEY가 없는 표에서 VACUUM·복원 때 바뀔 수 있어 쓰지 않는다.
    -- 기존 DB에는 아래 이관(_migrate_quiz_attempt_seq)이 열을 더하고 현재 순서대로 채운 뒤 인덱스를 만든다.
    attempt_seq INTEGER
);
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
CREATE INDEX IF NOT EXISTS idx_annotations_page ON annotations(context_id, created_at, annotation_id);
-- 전체 기록함(#206)은 모든 학습 맥락을 최신순 키셋으로 훑는다.
CREATE INDEX IF NOT EXISTS idx_annotations_library_page ON annotations(created_at DESC, annotation_id DESC);
-- 생성 요청 이력은 메모 삭제 뒤에도 남겨 재시도로 삭제된 메모가 복원되지 않게 한다.
-- 로컬 DB·맥락 범위이며, 맥락 삭제 시 함께 제거한다. 메모 본문은 저장하지 않는다.
CREATE TABLE IF NOT EXISTS annotation_create_requests (
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id) ON DELETE CASCADE,
    request_key TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (context_id, request_key)
);
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
-- 청크 소속을 게시 트랜잭션 또는 실제 벡터 컬렉션에서 확인한 색인만 기록한다.
-- 이 표에 없는 구버전 멤버십은 현재 번역 결과로 추측하지 않고 미확인으로 취급한다.
CREATE TABLE IF NOT EXISTS search_index_chunk_snapshots (
    embedding_set_id TEXT PRIMARY KEY REFERENCES embedding_sets(embedding_set_id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('publish', 'vector_store'))
);
-- 게시 함수가 벡터 저장소에서 검증한 정확한 ID 집합을 게시 트랜잭션 안에서 기록한다.
-- 이전 버전의 자동 추론 트리거는 translated_text가 없는 행까지 포함할 수 있으므로 제거한다.
DROP TRIGGER IF EXISTS trg_embedding_set_chunks;
-- 이 기능 이전에 만든 임베딩 세트의 집합은 init_db가 번역 결과 이관 뒤에 게시 기록과 맞는지 확인하고 채운다(_migrate_search_index_chunks).
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

-- ask 실행 기록(#119). 요청 키마다 한 행이고, 소유자 토큰을 가진 하나의 실행만 진행한다.
-- 임대(lease_expires_at)가 지나면 그 실행은 더 이상 갱신·결과 저장을 할 수 없고, 같은 키로 자동 재시작하지 않는다.
-- 시각은 모두 고정 폭 UTC 문자열(YYYY-MM-DDTHH:MM:SS.ffffffZ)이라 문자열 비교가 시각 비교와 같다.
CREATE TABLE IF NOT EXISTS ask_executions (
    request_key TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    context_id TEXT NOT NULL REFERENCES learning_contexts(context_id) ON DELETE CASCADE,
    owner_token TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    failure_code TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    finished_at TEXT,
    result_expires_at TEXT,
    key_expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ask_executions_key_expiry ON ask_executions(key_expires_at);

-- 중앙 서버 동기화는 기존 로컬 도메인 테이블과 분리한다. 토큰, 원문, 절대경로와
-- 전체 payload는 이 저장소에 넣지 않는다.
CREATE TABLE IF NOT EXISTS server_sync_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_instance_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS server_sync_consents (
    consent_scope_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    local_object_id TEXT NOT NULL REFERENCES papers(paper_id),
    notice_version TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_server_sync_consents_object
    ON server_sync_consents(account_id, object_type, local_object_id);

CREATE TABLE IF NOT EXISTS sync_outbox (
    operation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    consent_scope_id TEXT NOT NULL REFERENCES server_sync_consents(consent_scope_id),
    object_type TEXT NOT NULL,
    local_object_id TEXT NOT NULL REFERENCES papers(paper_id),
    local_subobject_id TEXT,
    operation TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'syncing', 'synced', 'retryable_failure', 'action_required', 'conflict'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_ready
    ON sync_outbox(account_id, status, next_attempt_at, created_at);

CREATE TABLE IF NOT EXISTS server_object_mappings (
    account_id TEXT NOT NULL,
    source_instance_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    local_object_id TEXT NOT NULL REFERENCES papers(paper_id),
    server_object_id TEXT NOT NULL,
    server_revision INTEGER NOT NULL CHECK (server_revision >= 1),
    synced_at TEXT NOT NULL,
    PRIMARY KEY (account_id, source_instance_id, object_type, local_object_id),
    UNIQUE (account_id, object_type, server_object_id)
);

CREATE TABLE IF NOT EXISTS server_subobject_mappings (
    account_id TEXT NOT NULL,
    source_instance_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    local_object_id TEXT NOT NULL REFERENCES papers(paper_id),
    local_subobject_id TEXT NOT NULL,
    server_object_id TEXT NOT NULL,
    server_revision INTEGER NOT NULL CHECK (server_revision >= 1),
    synced_at TEXT NOT NULL,
    PRIMARY KEY (account_id, source_instance_id, object_type, local_object_id, local_subobject_id),
    UNIQUE (account_id, object_type, server_object_id)
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
        _migrate_translation_recovery(conn)
        _migrate_search_index_chunks(conn)
        _migrate_quiz_attempt_seq(conn)
        _migrate_server_sync_outbox(conn)
        # 기존 생성된 DB에 컬럼을 추가하는 형식.
        # 초기값은 NULL로 번역 설정을 임의로 채우지 않는다.
        with conn:
            if 'producer_version' not in {r['name'] for r in conn.execute('PRAGMA table_info(paper_insights)')}:
                conn.execute('ALTER TABLE paper_insights ADD COLUMN producer_version TEXT')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_revisions)')}
            for name in ('prompt_version', 'target_language'):
                if name not in columns:
                    conn.execute(f'ALTER TABLE translation_revisions ADD COLUMN {name} TEXT')
    finally:
        conn.close()


def _migrate_server_sync_outbox(conn: sqlite3.Connection) -> None:
    """기존 논문 outbox에 하위 학습 기록 식별자를 반복 안전하게 추가한다."""
    with conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(sync_outbox)')}
        if 'local_subobject_id' not in columns:
            conn.execute('ALTER TABLE sync_outbox ADD COLUMN local_subobject_id TEXT')


def _migrate_translation_recovery(conn: sqlite3.Connection) -> None:
    """기존 복구 잠금에 고정 기준·계획 색인 열을 반복 안전하게 추가한다."""
    # sqlite3 연결은 첫 DML 전까지 암묵 트랜잭션을 시작하지 않는다. ALTER/DROP도
    # 이후 소유권 검증과 함께 롤백되도록 첫 스키마 변경 전에 명시적으로 잠근다.
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_recovery_locks)')}
        if 'base_embedding_set_id' not in columns:
            conn.execute('ALTER TABLE translation_recovery_locks ADD COLUMN base_embedding_set_id TEXT')
        if 'new_embedding_set_id' not in columns:
            conn.execute('ALTER TABLE translation_recovery_locks ADD COLUMN new_embedding_set_id TEXT')
        request_columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_recovery_requests)')}
        if 'new_embedding_set_id' not in request_columns:
            conn.execute('ALTER TABLE translation_recovery_requests ADD COLUMN new_embedding_set_id TEXT')
        conn.execute('DROP INDEX IF EXISTS uq_translation_recovery_embedding_set_owner')
        conn.execute('''INSERT OR IGNORE INTO translation_recovery_embedding_set_reservations
            (embedding_set_id, job_id, created_at)
            SELECT embedding_set_id, job_id, MIN(created_at) FROM (
                SELECT new_embedding_set_id AS embedding_set_id, job_id, created_at
                FROM translation_recovery_requests WHERE new_embedding_set_id IS NOT NULL
                UNION ALL
                SELECT new_embedding_set_id AS embedding_set_id, job_id, created_at
                FROM translation_recovery_locks WHERE new_embedding_set_id IS NOT NULL
            ) GROUP BY embedding_set_id, job_id''')
        conflict = conn.execute('''SELECT 1 FROM (
                SELECT new_embedding_set_id AS embedding_set_id, job_id
                FROM translation_recovery_requests WHERE new_embedding_set_id IS NOT NULL
                UNION
                SELECT new_embedding_set_id AS embedding_set_id, job_id
                FROM translation_recovery_locks WHERE new_embedding_set_id IS NOT NULL
            ) legacy WHERE NOT EXISTS (
                SELECT 1 FROM translation_recovery_embedding_set_reservations owner
                WHERE owner.embedding_set_id=legacy.embedding_set_id
                  AND owner.job_id=legacy.job_id
            ) LIMIT 1''').fetchone()
        if conflict:
            raise sqlite3.IntegrityError('conflicting recovery embedding set owners')


_SEARCH_INDEXES_COLUMNS = ('embedding_set_id', 'version_id', 'parse_revision_id', 'translation_revision_id',
                           'job_id', 'chunk_count')


def _migrate_translation_results(conn: sqlite3.Connection) -> None:
    """translated_text 컬럼을 더하고, 성공 행의 번역 본문을 chunks.text에서 채운다. 반복 실행해도 같은 결과다."""
    with conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(translation_results)')}
        if 'translated_text' not in columns:
            conn.execute('ALTER TABLE translation_results ADD COLUMN translated_text TEXT')
        # 옛 사본은 그 번역 리비전이 가리키는 파싱 리비전의 청크일 때만 쓴다(다른 파싱 리비전의 사본은 이관하지 않는다).
        conn.execute(
            'UPDATE translation_results SET translated_text=(SELECT c.text FROM chunks c '
            'JOIN translation_revisions t ON t.translation_revision_id=c.translation_revision_id '
            'AND t.parse_revision_id=c.parse_revision_id '
            'WHERE c.chunk_id=translation_results.chunk_id '
            'AND c.translation_revision_id=translation_results.translation_revision_id) '
            'WHERE failure_code IS NULL AND translated_text IS NULL')
        # 결과 행이 없는 옛 성공 청크(chunks.text만 있던 DB)를 결과 표로 옮긴다. 이미 있는 행은 건드리지 않는다.
        conn.execute(
            'INSERT OR IGNORE INTO translation_results (translation_revision_id, chunk_id, failure_code, translated_text) '
            'SELECT c.translation_revision_id, c.chunk_id, NULL, c.text FROM chunks c '
            'JOIN translation_revisions t ON t.translation_revision_id=c.translation_revision_id '
            'AND t.parse_revision_id=c.parse_revision_id '
            'WHERE c.text IS NOT NULL AND c.translation_revision_id IS NOT NULL')


def _migrate_quiz_attempt_seq(conn: sqlite3.Connection) -> None:
    """quiz_attempts.attempt_seq(영속 저장 순번)를 더하고 비어 있는 행을 현재 저장 순서(rowid)대로 채운다(#176).

    한 쓰기 트랜잭션에서 하며 반복 실행해도 같은 결과다. 이미 채워진 순번은 바꾸지 않고, 비어 있는 행에는 기존 최댓값 뒤로
    이어 붙인다. 순번은 현재 보이는 순서(제출 시각, 같은 시각이면 저장 순서)를 그대로 보존한다. 조회 인덱스는 순번 열로 만들고,
    순번 이전의 인덱스는 새 인덱스가 대신하므로 지운다.
    """
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if 'attempt_seq' not in {row['name'] for row in conn.execute('PRAGMA table_info(quiz_attempts)')}:
            conn.execute('ALTER TABLE quiz_attempts ADD COLUMN attempt_seq INTEGER')
        missing = [row[0] for row in conn.execute(
            'SELECT rowid FROM quiz_attempts WHERE attempt_seq IS NULL ORDER BY rowid')]
        if missing:
            base = conn.execute('SELECT COALESCE(MAX(attempt_seq), 0) FROM quiz_attempts').fetchone()[0]
            conn.executemany('UPDATE quiz_attempts SET attempt_seq=? WHERE rowid=?',
                             [(base + offset + 1, rowid) for offset, rowid in enumerate(missing)])
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_attempts_seq ON quiz_attempts(attempt_seq)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_quiz_attempts_context_seq '
                     'ON quiz_attempts(context_id, step, submitted_at, attempt_seq)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_quiz_attempts_context_time_seq '
                     'ON quiz_attempts(context_id, submitted_at, attempt_seq)')
        conn.execute('DROP INDEX IF EXISTS idx_quiz_attempts_context')
        conn.execute('DROP INDEX IF EXISTS idx_quiz_attempts_context_time')


def _migrate_search_index_chunks(conn: sqlite3.Connection) -> None:
    """출처를 확인할 수 없는 구버전 청크 소속을 미확인 상태로 만든다.

    현재 번역 성공 행과 게시 개수가 같아도 게시 뒤 같은 개수의 다른 청크로 바뀌었을 수 있다. 따라서 DB 값만으로
    과거 게시 집합을 복원하지 않는다. 실제 벡터 컬렉션을 확인하는 런타임 이관 또는 신규 게시 트랜잭션이 출처 표식을
    남긴 집합만 보존한다. 기존 답변·최종 근거·검증 이력은 삭제하지 않으며 반복 실행 결과가 같다.
    """
    with conn:
        conn.execute(
            'DELETE FROM search_index_chunks WHERE embedding_set_id NOT IN '
            '(SELECT embedding_set_id FROM search_index_chunk_snapshots)')


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
