"""파일을 열거나 URL을 발급하지 않고 근거의 PDF 이동 가능 여부만 판정한다."""
from typing import Protocol

from solo_leveling.application.evidence_qa.evidence import EvidenceService
from solo_leveling.application.evidence_qa.errors import DataIntegrityError, InvalidArgumentError
from solo_leveling.domain.evidence_qa import require_text
from solo_leveling.domain.pdf_navigation import (
    FileAccessResult, FileAccessStatus, NavigationReason, PdfNavigationTarget, PdfSourceIdentity,
)


class PdfSourceReader(Protocol):
    def get(self, context_id: str) -> PdfSourceIdentity: ...


class PdfNavigationService:
    def __init__(self, evidence: EvidenceService, sources: PdfSourceReader):
        self.evidence = evidence
        self.sources = sources

    def get(self, context_id: str, evidence_id: str, *,
            access: FileAccessResult | None = None) -> PdfNavigationTarget:
        try:
            require_text(context_id, 'context_id')
            require_text(evidence_id, 'evidence_id')
            if access is not None and not isinstance(access, FileAccessResult):
                raise ValueError('invalid access')
        except ValueError:
            raise InvalidArgumentError('PDF 위치 조회 입력이 올바르지 않습니다.') from None

        # 없는/잘못된 DB를 기존 근거 조회 경로가 먼저 생성하지 않도록 읽기 전용으로 확인한다.
        source = self.sources.get(context_id)
        if not isinstance(source, PdfSourceIdentity) or source.context_id != context_id:
            raise DataIntegrityError('PDF 위치의 저장 출처 연결이 올바르지 않습니다.')
        # 기존 context 소속·리비전·인용문 검사는 그대로 수행한다.
        detail, = self.evidence.get(context_id, (evidence_id,)).evidence
        if (source.version_id != detail.version_id
                or source.parse_revision_id != detail.parse_revision_id
                or detail.pdf_page > source.page_count):
            raise DataIntegrityError('PDF 위치의 저장 출처 연결이 올바르지 않습니다.')

        reason = self._reason(source, detail.pdf_page, access)
        return PdfNavigationTarget(detail.version_id, detail.parse_revision_id,
                                   detail.pdf_page, detail.printed_page_label, reason)

    @staticmethod
    def _reason(source, page, access):
        if access is None:
            return NavigationReason.ACCESS_NOT_CHECKED
        failures = {
            FileAccessStatus.MISSING: NavigationReason.FILE_MISSING,
            FileAccessStatus.DENIED: NavigationReason.ACCESS_DENIED,
            FileAccessStatus.EXPIRED: NavigationReason.ACCESS_EXPIRED,
            FileAccessStatus.UNAVAILABLE: NavigationReason.ACCESS_UNAVAILABLE,
        }
        if access.status in failures:
            return failures[access.status]
        if access.version_id != source.version_id:
            return NavigationReason.VERSION_MISMATCH
        if access.file_hash != source.file_hash:
            return NavigationReason.FILE_HASH_MISMATCH
        if page > access.page_count:
            raise DataIntegrityError('PDF 위치의 페이지 범위가 올바르지 않습니다.')
        if access.page_count != source.page_count:
            raise DataIntegrityError('PDF 파일과 파싱의 페이지 수가 일치하지 않습니다.')
        return None
