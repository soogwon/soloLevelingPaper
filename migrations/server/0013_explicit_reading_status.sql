-- 기존 단계 비율로 추정한 읽기 상태를 명시적 상태로 간주하지 않는다.
ALTER TABLE server_learning_records DROP CONSTRAINT server_learning_records_reading_status_check;
ALTER TABLE server_learning_records ADD CONSTRAINT server_learning_records_reading_status_check
    CHECK (reading_status IN ('unknown', 'not_started', 'reading', 'completed'));
-- 과거 학습 기록의 추정 상태는 보존하되 읽기 상태 조회에는 사용하지 않는다.
-- 명시적 상태를 확인할 수 없는 논문은 새 테이블 행이 없으므로 unknown이다.
CREATE TABLE server_paper_reading_states (
    paper_id UUID PRIMARY KEY REFERENCES server_papers(paper_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('unknown','not_started','reading','completed')),
    basis_version_id TEXT,
    revision INTEGER NOT NULL CHECK (revision > 0),
    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
    action TEXT NOT NULL CHECK (action IN ('initial','start','complete','reopen')),
    operation_id UUID NOT NULL,
    request_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status != 'completed' OR basis_version_id IS NOT NULL)
);
