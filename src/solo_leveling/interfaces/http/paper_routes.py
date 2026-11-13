"""로컬 기기의 논문 메타데이터 동기화 API."""

from datetime import datetime, timezone
from pathlib import PureWindowsPath
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.annotation import (
    MAX_BODY_LENGTH, MAX_QUOTE_LENGTH, MAX_SEGMENTS, SegmentInput,
    validate_annotation_fields, validate_segments,
)

from .papers import (
    AnnotationConflict, AnnotationMetadata, IdempotencyConflict, LearningRecordConflict,
    LearningRecordMetadata, PaperMetadata, PaperNotFound, PaperSelectionConflict, PaperStore,
    PaperStoreUnavailable, SummaryConflict, SummaryMetadata,
)


_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


def _normalize_file_or_version(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or "\x00" in value or "/" in value or "\\" in value or PureWindowsPath(value).drive:
        raise ValueError("파일명과 버전 ID에는 경로를 사용할 수 없습니다.")
    return value


def create_paper_router(require_principal: Any, store: PaperStore):
    from fastapi import APIRouter, Depends, Header, HTTPException, status
    from fastapi.exceptions import RequestValidationError
    from fastapi.routing import APIRoute
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    class PaperCreate(BaseModel):
        model_config = ConfigDict(extra="forbid")

        source_instance_id: UUID
        local_paper_id: str = Field(min_length=1, max_length=128)
        title: str | None = Field(default=None, max_length=1000)
        authors: list[str] | None = Field(default=None, max_length=100)
        publication_year: int | None = Field(default=None, ge=1000, le=2100)
        original_created_at: datetime

        @field_validator("local_paper_id")
        @classmethod
        def validate_local_id(cls, value):
            value = value.strip()
            if not value or "\x00" in value or "/" in value or "\\" in value or PureWindowsPath(value).drive:
                raise ValueError("local_paper_id가 올바르지 않습니다.")
            return value

        @field_validator("title")
        @classmethod
        def normalize_title(cls, value):
            if value is None:
                return None
            if "\x00" in value:
                raise ValueError("title에 NUL 문자를 사용할 수 없습니다.")
            return value.strip() or None

        @field_validator("authors")
        @classmethod
        def normalize_authors(cls, value):
            if value is None:
                return None
            normalized = []
            for author in value:
                if not isinstance(author, str) or "\x00" in author or not author.strip() or len(author) > 300:
                    raise ValueError("authors 항목이 올바르지 않습니다.")
                normalized.append(author.strip())
            return normalized or None

        @field_validator("original_created_at")
        @classmethod
        def require_timezone(cls, value):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("original_created_at에는 시간대가 필요합니다.")
            try:
                return value.astimezone(timezone.utc)
            except OverflowError:
                raise ValueError("UTC로 변환할 수 없는 등록 시각입니다.") from None

    class PaperView(BaseModel):
        paper_id: str
        revision: int
        sync_status: str
        server_updated_at: datetime

    class PositionView(BaseModel):
        model_config = ConfigDict(extra="forbid")

        chunk_id: str | None = Field(default=None, min_length=1, max_length=128)
        pdf_page: int | None = Field(default=None, ge=1)
        section_id: str | None = Field(default=None, min_length=1, max_length=128)
        section_number: str | None = Field(default=None, min_length=1, max_length=50)
        section_title: str | None = Field(default=None, min_length=1, max_length=500)
        printed_page_label: str | None = Field(default=None, min_length=1, max_length=50)

        @field_validator("chunk_id", "section_id", "section_number", "section_title", "printed_page_label")
        @classmethod
        def normalize_position_text(cls, value):
            if value is not None and ("\x00" in value or not value.strip()):
                raise ValueError("학습 위치 문자열이 올바르지 않습니다.")
            return value.strip() if value is not None else None

        @model_validator(mode="after")
        def require_position_value(self):
            if not any(value is not None for value in self.model_dump().values()):
                raise ValueError("학습 위치에는 하나 이상의 값이 필요합니다.")
            return self

    class LearningRecordUpsert(BaseModel):
        model_config = ConfigDict(extra="forbid")

        source_instance_id: UUID
        selected_version_id: str | None = Field(default=None, min_length=1, max_length=128)
        file_name: str | None = Field(default=None, min_length=1, max_length=500)
        last_position: PositionView | None = None
        last_studied_at: datetime | None = None
        completed_steps: list[Literal["preview", "structure", "deep_read", "review"]] = Field(
            default_factory=list, max_length=len(LEARNING_STEPS)
        )
        source_updated_at: datetime

        @field_validator("last_studied_at", "source_updated_at")
        @classmethod
        def require_timestamp_timezone(cls, value):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("학습 시각에는 시간대가 필요합니다.")
            try:
                return value.astimezone(timezone.utc) if value is not None else None
            except OverflowError:
                raise ValueError("UTC로 변환할 수 없는 학습 시각입니다.") from None

        @field_validator("selected_version_id", "file_name")
        @classmethod
        def reject_unsafe_text(cls, value):
            return _normalize_file_or_version(value)

        @model_validator(mode="after")
        def validate_learning_times(self):
            if self.last_studied_at is not None and self.last_studied_at > self.source_updated_at:
                raise ValueError("마지막 학습 시각은 원본 갱신 시각보다 늦을 수 없습니다.")
            return self

    class LearningRecordView(BaseModel):
        learning_record_id: UUID
        revision: int
        sync_status: Literal["stored"]
        server_updated_at: datetime

    class SummaryItemUpsert(BaseModel):
        model_config = ConfigDict(extra="forbid")

        kind: Literal["claim", "method", "result", "limitation"]
        text: str = Field(min_length=1, max_length=10000)
        evidence_ids: list[str] = Field(min_length=1, max_length=100)

        @field_validator("text")
        @classmethod
        def normalize_text(cls, value):
            if "\x00" in value or not value.strip():
                raise ValueError("요약 본문이 올바르지 않습니다.")
            return value.strip()

        @field_validator("evidence_ids")
        @classmethod
        def validate_evidence_ids(cls, values):
            if any(not value.strip() or "\x00" in value or len(value) > 128 for value in values):
                raise ValueError("evidence_id가 올바르지 않습니다.")
            normalized = [value.strip() for value in values]
            if len(set(normalized)) != len(normalized):
                raise ValueError("evidence_id가 중복되었습니다.")
            return normalized

    class SummaryUpsert(BaseModel):
        model_config = ConfigDict(extra="forbid")

        source_instance_id: UUID
        selected_version_id: str | None = Field(max_length=128)
        scope: Literal["paper"]
        author_type: Literal["mcp", "user"]
        items: list[SummaryItemUpsert] = Field(min_length=1, max_length=100)
        original_created_at: datetime
        source_updated_at: datetime

        _version_id = field_validator("selected_version_id")(_normalize_file_or_version)

        @field_validator("original_created_at", "source_updated_at")
        @classmethod
        def require_summary_timezone(cls, value):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("요약 시각에는 시간대가 필요합니다.")
            try:
                return value.astimezone(timezone.utc)
            except OverflowError:
                raise ValueError("UTC로 변환할 수 없는 요약 시각입니다.") from None

        @model_validator(mode="after")
        def validate_summary_times(self):
            if self.original_created_at > self.source_updated_at:
                raise ValueError("요약 생성 시각은 원본 갱신 시각보다 늦을 수 없습니다.")
            return self

    class SummaryView(BaseModel):
        summary_id: UUID
        revision: int
        sync_status: Literal["stored"]
        server_updated_at: datetime

    class AnnotationSegmentUpsert(BaseModel):
        model_config = ConfigDict(extra="forbid")

        chunk_id: str = Field(min_length=1, max_length=128)
        parse_revision_id: str = Field(min_length=1, max_length=128)
        translation_revision_id: str | None = Field(default=None, min_length=1, max_length=128)
        target: Literal["original", "translation"]
        start_offset: int = Field(strict=True, ge=0)
        end_offset: int = Field(strict=True, gt=0)
        quote_snapshot: str = Field(min_length=1, max_length=MAX_QUOTE_LENGTH)

    class AnnotationUpsert(BaseModel):
        model_config = ConfigDict(extra="forbid")

        source_instance_id: UUID
        selected_version_id: str | None = Field(default=None, max_length=128)
        kind: Literal["note", "highlight"]
        body: str = Field(default="", max_length=MAX_BODY_LENGTH)
        step: str | None = Field(default=None, max_length=128)
        segments: list[AnnotationSegmentUpsert] = Field(default_factory=list, max_length=MAX_SEGMENTS)
        original_created_at: datetime
        source_updated_at: datetime

        _version_id = field_validator("selected_version_id")(_normalize_file_or_version)

        @field_validator("original_created_at", "source_updated_at")
        @classmethod
        def require_annotation_timezone(cls, value):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("메모 시각에는 시간대가 필요합니다.")
            return value.astimezone(timezone.utc)

        @model_validator(mode="after")
        def validate_annotation(self):
            if self.original_created_at > self.source_updated_at:
                raise ValueError("메모 생성 시각은 갱신 시각보다 늦을 수 없습니다.")
            validate_annotation_fields(self.kind, self.body, self.step, len(self.segments))
            validate_segments([SegmentInput(**segment.model_dump(exclude={"parse_revision_id"}))
                               for segment in self.segments])
            return self

    class AnnotationView(BaseModel):
        annotation_id: UUID
        revision: int
        sync_status: Literal["stored"]
        server_updated_at: datetime

    class PaperRoute(APIRoute):
        def get_route_handler(self):
            handle = super().get_route_handler()

            async def register_request(request):
                try:
                    return await handle(request)
                except RequestValidationError:
                    # 거부된 본문에는 로컬 경로나 원문이 있을 수 있다.
                    raise HTTPException(status_code=422, detail={"code": "invalid_paper_request"}) from None

            return register_request

    router = APIRouter(prefix="/v1/mcp/papers", tags=["paper-sync"], route_class=PaperRoute)
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.post("", response_model=PaperView, status_code=status.HTTP_201_CREATED)
    def register(
        payload: PaperCreate,
        principal: Principal,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ):
        if principal.credential_type != "device":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "device_credential_required"})
        if not _KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_idempotency_key"})
        metadata = PaperMetadata(
            str(payload.source_instance_id), payload.local_paper_id, payload.title,
            tuple(payload.authors) if payload.authors is not None else None,
            payload.publication_year, payload.original_created_at,
        )
        try:
            return PaperView(**store.register(principal.user_id, idempotency_key, metadata).__dict__)
        except IdempotencyConflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "idempotency_conflict"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None

    @router.put("/{paper_id}/learning-records/{local_record_id}", response_model=LearningRecordView)
    def upsert_learning_record(
        paper_id: UUID,
        local_record_id: str,
        payload: LearningRecordUpsert,
        principal: Principal,
    ):
        if principal.credential_type != "device":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "device_credential_required"})
        local_record_id = local_record_id.strip()
        if (not local_record_id or len(local_record_id) > 128 or "\x00" in local_record_id
                or "/" in local_record_id or "\\" in local_record_id or PureWindowsPath(local_record_id).drive):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_learning_record_id"})
        completed = set(payload.completed_steps)
        if len(completed) != len(payload.completed_steps):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_completed_steps"})
        reading_status = ("not_started" if payload.last_studied_at is None else
                          "completed" if completed == set(LEARNING_STEPS) else "reading")
        metadata = LearningRecordMetadata(
            str(payload.source_instance_id), local_record_id, payload.selected_version_id,
            payload.file_name, payload.last_position.model_dump(exclude_none=True) if payload.last_position else None,
            payload.last_studied_at, reading_status, payload.source_updated_at,
        )
        try:
            result = store.upsert_learning_record(principal.user_id, str(paper_id), metadata)
        except LearningRecordConflict:
            raise HTTPException(status_code=409, detail={"code": "learning_record_conflict"}) from None
        except PaperNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail={"code": "paper_not_found"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None
        return LearningRecordView(**result.__dict__)

    @router.put(
        "/{paper_id}/learning-records/{local_record_id}/summaries/{local_summary_id}",
        response_model=SummaryView,
    )
    def upsert_summary(
        paper_id: UUID,
        local_record_id: str,
        local_summary_id: str,
        payload: SummaryUpsert,
        principal: Principal,
    ):
        if principal.credential_type != "device":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "device_credential_required"})
        identifiers = (local_record_id.strip(), local_summary_id.strip())
        if any(not value or len(value) > 128 or "\x00" in value or "/" in value or "\\" in value
               or PureWindowsPath(value).drive for value in identifiers):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_summary_identity"})
        metadata = SummaryMetadata(
            str(payload.source_instance_id), identifiers[0], identifiers[1], payload.scope,
            payload.author_type, tuple(item.model_dump() for item in payload.items),
            payload.original_created_at, payload.source_updated_at, payload.selected_version_id,
        )
        try:
            result = store.upsert_summary(principal.user_id, str(paper_id), metadata)
        except SummaryConflict:
            raise HTTPException(status_code=409, detail={"code": "summary_conflict"}) from None
        except PaperNotFound:
            raise HTTPException(status_code=404, detail={"code": "learning_record_not_found"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=503, detail={"code": "paper_store_unavailable"}) from None
        return SummaryView(**result.__dict__)

    @router.put(
        "/{paper_id}/learning-records/{local_record_id}/annotations/{local_annotation_id}",
        response_model=AnnotationView,
    )
    def upsert_annotation(
        paper_id: UUID,
        local_record_id: str,
        local_annotation_id: str,
        payload: AnnotationUpsert,
        principal: Principal,
    ):
        if principal.credential_type != "device":
            raise HTTPException(status_code=403, detail={"code": "device_credential_required"})
        identifiers = (local_record_id.strip(), local_annotation_id.strip())
        if any(not value or len(value) > 128 or "\x00" in value or "/" in value or "\\" in value
               or PureWindowsPath(value).drive for value in identifiers):
            raise HTTPException(status_code=422, detail={"code": "invalid_annotation_identity"})
        metadata = AnnotationMetadata(
            str(payload.source_instance_id), identifiers[0], identifiers[1],
            payload.selected_version_id, payload.kind, payload.body, payload.step,
            tuple(item.model_dump() for item in payload.segments), payload.original_created_at,
            payload.source_updated_at,
        )
        try:
            result = store.upsert_annotation(principal.user_id, str(paper_id), metadata)
        except AnnotationConflict:
            raise HTTPException(status_code=409, detail={"code": "annotation_conflict"}) from None
        except PaperNotFound:
            raise HTTPException(status_code=404, detail={"code": "learning_record_not_found"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=503, detail={"code": "paper_store_unavailable"}) from None
        return AnnotationView(**result.__dict__)

    return router


def create_web_paper_router(require_principal: Any, store: PaperStore):
    """외부 웹이 로그인 사용자의 서버 확정 논문만 페이지 단위로 조회한다."""
    from fastapi import APIRouter, Depends, HTTPException, Query, status
    from pydantic import BaseModel

    class PublishedView(BaseModel):
        year: int
        month: None = None
        day: None = None
        precision: Literal["year"] = "year"

    class PaperListItemView(BaseModel):
        paper_id: str
        title: str | None
        authors: list[str] | None
        file_name: str | None
        published: PublishedView | None
        selected_learning_record_id: str | None
        selected_version_id: str | None
        last_position: dict[str, Any] | None
        last_studied_at: datetime | None
        reading_status: Literal["not_started", "reading", "completed", "unknown"]
        collection_id: str | None

    class PaperPageView(BaseModel):
        items: list[PaperListItemView]
        page: int
        page_size: int
        total_count: int
        total_pages: int

    class VersionView(BaseModel):
        version_id: str
        file_name: str | None
        created_at: datetime | None = None

    class AvailabilityView(BaseModel):
        available: bool
        reason: str
        message: str

    class PaperDetailView(PaperListItemView):
        version: VersionView | None
        summary: dict[str, Any] | None
        file_access: AvailabilityView
        resume: AvailabilityView

    router = APIRouter(prefix="/v1/papers", tags=["web-papers"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.get("", response_model=PaperPageView)
    def list_papers(
        principal: Principal,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        reading_status: Literal["not_started", "reading", "completed", "unknown"] | None = None,
        collection_id: UUID | None = None,
        unassigned: bool = False,
        sort: Literal["recent"] = "recent",
        page: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "web_session_required"})
        normalized_query = q.strip() if q is not None else None
        if q is not None and (not normalized_query or "\x00" in q):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_paper_query"})
        if collection_id is not None and unassigned:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "collection_filter_conflict"})
        try:
            collection_filters = {}
            if collection_id is not None:
                collection_filters["collection_id"] = str(collection_id)
            if unassigned:
                collection_filters["unassigned"] = True
            result = store.list_papers(
                principal.user_id, query=normalized_query, reading_status=reading_status,
                page=page, limit=limit,
                **collection_filters,
            )
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None
        return PaperPageView(
            items=[PaperListItemView(
                paper_id=item.paper_id, title=item.title, authors=list(item.authors) if item.authors else None,
                file_name=item.file_name,
                published=({"year": item.publication_year, "month": None, "day": None, "precision": "year"}
                           if item.publication_year is not None else None),
                selected_learning_record_id=item.selected_learning_record_id,
                selected_version_id=item.selected_version_id, last_position=item.last_position,
                last_studied_at=item.last_studied_at, reading_status=item.reading_status,
                collection_id=item.collection_id,
            ) for item in result.items],
            page=result.page, page_size=result.page_size,
            total_count=result.total_count, total_pages=result.total_pages,
        )

    @router.get("/{paper_id}", response_model=PaperDetailView)
    def get_paper(
        paper_id: UUID,
        principal: Principal,
        learning_record_id: UUID | None = None,
        version_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    ):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "web_session_required"})
        try:
            version_id = _normalize_file_or_version(version_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_version_id"}) from None
        try:
            detail = store.get_paper(
                principal.user_id, str(paper_id),
                learning_record_id=str(learning_record_id) if learning_record_id else None,
                version_id=version_id,
            )
        except PaperSelectionConflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "paper_selection_conflict"}) from None
        except PaperNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail={"code": "paper_not_found"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None
        item = detail.paper
        published = ({"year": item.publication_year, "month": None, "day": None, "precision": "year"}
                     if item.publication_year is not None else None)
        has_record = item.selected_learning_record_id is not None
        return PaperDetailView(
            paper_id=item.paper_id, title=item.title,
            authors=list(item.authors) if item.authors else None, file_name=item.file_name,
            published=published, selected_learning_record_id=item.selected_learning_record_id,
            selected_version_id=item.selected_version_id, last_position=item.last_position,
            last_studied_at=item.last_studied_at, reading_status=item.reading_status,
            collection_id=item.collection_id,
            version=(VersionView(version_id=item.selected_version_id, file_name=item.file_name)
                     if item.selected_version_id else None),
            summary=({
                "summary_id": detail.summary.summary_id,
                "created_at": detail.summary.created_at,
                "author_type": detail.summary.author_type,
                "items": list(detail.summary.items),
            } if detail.summary is not None else None),
            file_access=AvailabilityView(
                available=False, reason="not_uploaded",
                message="이 원문 버전의 PDF가 서버에 업로드되지 않았어요.",
            ),
            resume=AvailabilityView(
                available=has_record,
                reason="resume_code_available" if has_record else "learning_record_missing",
                message=("이어하기 코드를 만든 뒤 연결된 로컬 MCP에서 준비 상태를 확인하세요."
                         if has_record else "저장된 학습 기록이 없어요."),
            ),
        )

    return router
