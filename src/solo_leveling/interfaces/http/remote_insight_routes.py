"""명시적인 기기 인증 이관 진입점."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from .auth import AuthenticatedPrincipal
from .remote_insights import InsightConflict, InsightImport, InsightMappingRequest


def create_insight_import_router(require_principal, store):
    router = APIRouter(prefix='/v1/remote-insights')

    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def import_request(context_id, body, response, principal, operation):
        headers = {'Cache-Control': 'no-store'}
        response.headers.update(headers)
        if principal.credential_type != 'device':
            raise HTTPException(403, detail={'code': 'device_auth_required'}, headers=headers)
        try:
            result = operation(principal.user_id, principal.device_id, context_id, body)
        except InsightConflict as error:
            raise HTTPException(409, detail={'code': str(error)}, headers=headers) from None
        except Exception:
            raise HTTPException(503, detail={'code': 'insight_import_unavailable'}, headers=headers) from None
        if result is None:
            raise HTTPException(404, detail={'code': 'insight_context_not_found'}, headers=headers)
        return result

    @router.post('/contexts/{context_id}/imports/map-sources')
    def map_sources(context_id: UUID, body: InsightMappingRequest, response: Response, principal: Principal):
        return import_request(context_id, body, response, principal, store.map_sources)

    @router.post('/contexts/{context_id}/imports/preview')
    def preview(context_id: UUID, body: InsightImport, response: Response, principal: Principal):
        return import_request(context_id, body, response, principal, lambda *args: store.save(*args, preview=True))

    @router.post('/contexts/{context_id}/imports')
    def save(context_id: UUID, body: InsightImport, response: Response, principal: Principal):
        return import_request(context_id, body, response, principal, store.save)

    return router
