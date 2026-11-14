"""학습 단계와 독립적인 로컬 논문 읽기 상태. 같은 논문의 모든 맥락이 공유한다."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError, ResourceNotFoundError
from solo_leveling.infrastructure.database.schema import get_connection


class ReadingStateConflict(Exception):
    """조회 이후 다른 요청이 상태를 변경했다."""


@dataclass(frozen=True)
class ReadingState:
    paper_id: str
    status: str
    revision: int
    updated_at: str


class ReadingStateService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, paper_id: str) -> ReadingState:
        with closing(get_connection(self.db_path)) as conn:
            return self._load(conn, paper_id)

    @staticmethod
    def _load(conn, paper_id):
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise InvalidArgumentError('논문 ID가 필요합니다.')
        row = conn.execute('SELECT * FROM paper_reading_states WHERE paper_id=?', (paper_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError('논문을 찾을 수 없습니다.')
        return ReadingState(**dict(row))

    @staticmethod
    def _write(conn, current, status):
        if current.status == status:
            return current
        # 빠른 연속 변경이나 시스템 시계 역행에도 동기화 순서를 보존한다.
        previous = datetime.fromisoformat(current.updated_at.replace('Z', '+00:00'))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        stamp = max(datetime.now(timezone.utc), previous + timedelta(microseconds=1)).isoformat()
        conn.execute('UPDATE paper_reading_states SET status=?, revision=revision+1, updated_at=? WHERE paper_id=?',
                     (status, stamp, current.paper_id))
        return ReadingState(current.paper_id, status, current.revision + 1, stamp)

    def start(self, version_id: str) -> ReadingState:
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT paper_id FROM paper_versions WHERE version_id=?', (version_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError('논문을 찾을 수 없습니다.')
            current = self._load(conn, row['paper_id'])
            return self._write(conn, current, 'reading') if current.status in ('unknown', 'not_started') else current

    def set_completed(self, paper_id: str, completed: bool, expected_revision: int) -> ReadingState:
        if type(completed) is not bool or type(expected_revision) is not int or expected_revision < 1:
            raise InvalidArgumentError('상태와 조회한 revision이 필요합니다.')
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            current = self._load(conn, paper_id)
            if current.revision != expected_revision:
                raise ReadingStateConflict()
            if not completed and current.status != 'completed':
                raise InvalidArgumentError('완료된 논문만 완료를 취소할 수 있습니다.')
            return self._write(conn, current, 'completed' if completed else 'reading')
