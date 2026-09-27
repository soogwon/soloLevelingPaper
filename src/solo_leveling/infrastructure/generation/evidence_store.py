"""답변에서 사용한 근거를 기존 SQLite 저장 계약에 연결한다."""

from solo_leveling.infrastructure.database import repository as repo


class SQLiteEvidenceWriter:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def save(self, context_id, evidence):
        repo.save_evidences(self.db_path, context_id, list(evidence))
