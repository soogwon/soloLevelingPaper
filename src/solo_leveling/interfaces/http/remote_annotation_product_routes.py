"""웹 세션·CSRF 아래에서만 학습 시작과 맥락 저장 동의를 허용한다."""
from dataclasses import asdict, is_dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, Query
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal
from .remote_annotation_recovery_routes import RecoveryInput, SafeRecoveryRoute
from .remote_annotation_consents import AnnotationStorageConsentRequired
from .remote_annotations import RemoteAnnotationConflict, RemoteAnnotationDeleted, RemoteAnnotationReferenceError
from .remote_learning import RemoteLearningConflict


class GrantInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notice_version: str = Field(min_length=1, max_length=100)


class RevokeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    consent_id: UUID


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, gt=0)


class ProgressInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    status: Literal['reading', 'completed']
    pdf_page: int | None = Field(strict=True, gt=0)
    expected_revision: int = Field(strict=True, gt=0)


class UpdateInput(RevisionInput):
    body: str = Field(max_length=5000)
    step: str | None = None


def create_annotation_product_router(require_principal, service):
    router = APIRouter(prefix='/v1/remote-annotation-products/contexts', route_class=SafeRecoveryRoute)
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def call(principal, response, operation):
        headers = {'Cache-Control': 'no-store'}
        response.headers.update(headers)
        if principal.credential_type != 'web_session':
            raise HTTPException(403, {'code': 'web_session_required'}, headers=headers)
        try:
            value = operation(principal.user_id)
        except AnnotationStorageConsentRequired:
            raise HTTPException(403, {'code': 'annotation_storage_consent_required'}, headers=headers) from None
        except (RemoteAnnotationConflict, RemoteLearningConflict):
            raise HTTPException(409, {'code': 'annotation_conflict'}, headers=headers) from None
        except RemoteAnnotationDeleted:
            raise HTTPException(410, {'code': 'annotation_deleted'}, headers=headers) from None
        except RemoteAnnotationReferenceError:
            raise HTTPException(404, {'code': 'annotation_not_found'}, headers=headers) from None
        except ValueError:
            raise HTTPException(422, {'code': 'annotation_input_invalid'}, headers=headers) from None
        except Exception:
            raise HTTPException(503, {'code': 'annotation_unavailable'}, headers=headers) from None
        if value is None:
            raise HTTPException(404, {'code': 'annotation_not_found'}, headers=headers)
        return asdict(value) if is_dataclass(value) else value

    @router.get('')
    def contexts(paper_id: UUID, version_id: Annotated[str, Query(min_length=1, max_length=128)], principal: Principal, response: Response,
                 after: UUID | None = None, limit: int = 20):
        return call(principal, response, lambda owner: service.learning.list_for_paper(
            owner, str(paper_id), str(version_id), after=str(after) if after else None, limit=limit))

    @router.get('/dashboard')
    def dashboard(principal: Principal, response: Response, after: UUID | None = None,
                  limit: Annotated[int, Query(ge=1, le=50)] = 20):
        return call(principal, response, lambda owner: service.learning.dashboard(
            owner, after=str(after) if after else None, limit=limit))

    @router.post('/{context}/start')
    def start(context: UUID, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.learning.start(owner, str(context)))

    @router.get('/{context}/learning')
    def learning(context: UUID, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.learning.get(owner, str(context)))

    @router.post('/{context}/progress')
    def progress(context: UUID, body: ProgressInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.learning.set_progress(
            owner, str(context), request_key=str(body.request_key), status=body.status,
            pdf_page=body.pdf_page, expected_revision=body.expected_revision))

    @router.get('/{context}/consent')
    def consent(context: UUID, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.consents.view(owner, str(context)))

    @router.post('/{context}/consent')
    def grant(context: UUID, body: GrantInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.consents.grant(owner, str(context), body.notice_version))

    @router.post('/{context}/consent/revoke')
    def revoke(context: UUID, body: RevokeInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.consents.revoke(owner, str(context), str(body.consent_id)))

    @router.post('/{context}/requests/{key}')
    def create(context: UUID, key: str, body: RecoveryInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.create(owner, str(context), key, body.domain()))

    @router.get('/{context}/annotations')
    def listing(context: UUID, principal: Principal, response: Response, after: UUID | None = None,
                limit: int = 20, kind: Literal['note', 'highlight'] | None = None):
        return call(principal, response, lambda owner: service.store.list_annotations(
            owner, str(context), after=str(after) if after else None, limit=limit,
            kind=kind, include_total=True))

    @router.get('/{context}/annotations/{annotation_id}')
    def get(context: UUID, annotation_id: UUID, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.store.get_annotation(owner, str(context), str(annotation_id)))

    @router.patch('/{context}/annotations/{annotation_id}')
    def update(context: UUID, annotation_id: UUID, body: UpdateInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.update(
            owner, str(context), str(annotation_id), body.body, body.step, body.revision))

    @router.delete('/{context}/annotations/{annotation_id}')
    def delete(context: UUID, annotation_id: UUID, body: RevisionInput, principal: Principal, response: Response):
        return call(principal, response, lambda owner: service.store.delete_annotation(
            owner, str(context), str(annotation_id), expected_revision=body.revision))

    return router
