"""답변에서 사용한 근거를 기존 SQLite 저장 계약에 연결한다."""

from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError


class SQLiteEvidenceWriter:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def save(self, context_id, evidence):
        repo.save_evidences(self.db_path, context_id, list(evidence))


class SQLiteEvidenceReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, context_id, evidence_ids):
        try:
            return repo.get_evidences(self.db_path, context_id, list(evidence_ids))
        except ResourceNotFoundError:
            raise
        except ValueError:
            raise DataIntegrityError('저장된 근거의 연결 또는 인용문이 올바르지 않습니다.') from None
