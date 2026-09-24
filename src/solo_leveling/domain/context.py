"""검색 전 학습 맥락 준비 과정의 상태 계약."""

from solo_leveling.domain.models import JobStatus


class ContextNotReadyError(ValueError):
    """검색 가능한 색인이 없어 기존 작업 상태 안내가 필요한 경우."""

    def __init__(self, version_id: str, job_id: str | None, status: JobStatus | None):
        super().__init__('논문 색인이 준비되지 않았습니다. 등록 작업 상태를 확인해주세요.')
        self.version_id = version_id
        self.job_id = job_id
        self.status = status
