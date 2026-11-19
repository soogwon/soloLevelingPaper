"""요약 검토·동의·접수는 인증된 웹 세션과 기존 CSRF 검사를 요구한다."""
from typing import Annotated,Any,Literal
from uuid import UUID
from .auth import AuthenticatedPrincipal
from .remote_executions import RemoteExecutionConflict


def create_summary_review_router(require_principal:Any,service):
    from fastapi import APIRouter,Depends,HTTPException,Response,Query
    from pydantic import BaseModel,ConfigDict,Field
    router=APIRouter(prefix='/v1/remote-summaries')
    Principal=Annotated[AuthenticatedPrincipal,Depends(require_principal)]
    class Grant(BaseModel):
        model_config=ConfigDict(extra='forbid')
        notice_version:str=Field(min_length=1,max_length=100)
    def call(principal,response,method,*args):
        response.headers['Cache-Control']='no-store'
        if principal.credential_type!='web_session':raise HTTPException(403,detail={'code':'web_session_required'})
        if service is None:raise HTTPException(503,detail={'code':'summary_unavailable'})
        try:value=getattr(service,method)(principal.user_id,*args)
        except (ValueError,RemoteExecutionConflict):raise HTTPException(409,detail={'code':'summary_review_conflict'}) from None
        except Exception:raise HTTPException(503,detail={'code':'summary_unavailable'}) from None
        if value is None:raise HTTPException(404,detail={'code':'summary_not_found'})
        return value
    @router.get('/reviews/{key}')
    def view(key:UUID,principal:Principal,response:Response):return call(principal,response,'view',str(key))
    @router.post('/reviews/{key}/consents/{purpose}')
    def grant(key:UUID,purpose:Literal['storage','ai_provider'],body:Grant,principal:Principal,response:Response):
        return call(principal,response,'grant',str(key),purpose,body.notice_version)
    @router.post('/reviews/{key}/consents/{purpose}/revoke')
    def revoke(key:UUID,purpose:Literal['storage','ai_provider'],principal:Principal,response:Response):
        return call(principal,response,'revoke',str(key),purpose)
    @router.post('/reviews/{key}/submit')
    def submit(key:UUID,principal:Principal,response:Response):return call(principal,response,'submit',str(key))
    @router.get('/reviews/{key}/result')
    def result(key:UUID,principal:Principal,response:Response):return call(principal,response,'result',str(key))
    @router.post('/reviews/{key}/cancel')
    def cancel(key:UUID,principal:Principal,response:Response):return call(principal,response,'cancel',str(key))
    @router.get('/papers/{paper}/contexts')
    def contexts(paper:UUID,version_id:Annotated[str,Query(min_length=1,max_length=128)],
                 principal:Principal,response:Response,after:UUID|None=None):
        return call(principal,response,'contexts',str(paper),version_id,str(after) if after else None)
    @router.get('/contexts/{context}/latest')
    def latest(context:UUID,principal:Principal,response:Response):
        return call(principal,response,'latest',str(context))
    return router
