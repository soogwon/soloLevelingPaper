"""웹 세션·CSRF 경계 안에서만 복구 검토와 접수를 허용한다."""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from .auth import AuthenticatedPrincipal


class PrepareReprocessing(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_id: UUID
    operation: Literal['translation_recovery','reparse']


class ReprocessingConsent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notice_version: Literal['remote-reprocessing-v1']


def create_reprocessing_router(require_principal, service):
    router=APIRouter(prefix='/v1/reprocessing')
    Principal=Annotated[AuthenticatedPrincipal,Depends(require_principal)]
    def call(principal,response,method,*args):
        headers={'Cache-Control':'no-store'}
        response.headers.update(headers)
        if principal.credential_type!='web_session':raise HTTPException(403,detail={'code':'web_session_required'},headers=headers)
        if service is None:raise HTTPException(503,detail={'code':'reprocessing_unavailable'},headers=headers)
        try:value=getattr(service,method)(principal.user_id,*args)
        except ValueError:raise HTTPException(409,detail={'code':'reprocessing_review_conflict'},headers=headers) from None
        except Exception:raise HTTPException(503,detail={'code':'reprocessing_unavailable'},headers=headers) from None
        if value is None:raise HTTPException(404,detail={'code':'reprocessing_not_found'},headers=headers)
        return value
    @router.get('/contexts/{context}/{operation}')
    def draft(context:UUID,operation:Literal['translation_recovery','reparse'],principal:Principal,response:Response):
        return call(principal,response,'draft',str(context),operation)
    @router.post('/reviews/{key}')
    def prepare(key:UUID,body:PrepareReprocessing,principal:Principal,response:Response):
        return call(principal,response,'prepare',str(key),str(body.context_id),body.operation)
    @router.get('/reviews/{key}')
    def view(key:UUID,principal:Principal,response:Response):return call(principal,response,'view',str(key))
    @router.post('/reviews/{key}/consents/{purpose}')
    def grant(key:UUID,purpose:Literal['storage','ai_provider'],body:ReprocessingConsent,principal:Principal,response:Response):
        return call(principal,response,'grant',str(key),purpose,body.notice_version)
    @router.post('/reviews/{key}/consents/{purpose}/revoke')
    def revoke(key:UUID,purpose:Literal['storage','ai_provider'],principal:Principal,response:Response):
        return call(principal,response,'cancel',str(key),purpose)
    @router.post('/reviews/{key}/submit')
    def submit(key:UUID,principal:Principal,response:Response):return call(principal,response,'submit',str(key))
    @router.post('/reviews/{key}/cancel')
    def cancel(key:UUID,principal:Principal,response:Response):return call(principal,response,'cancel',str(key))
    return router
