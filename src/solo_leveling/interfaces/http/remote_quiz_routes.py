"""퀴즈 답안 저장 동의는 웹 세션에서만 기록하며 모델의 확인 주장은 받지 않는다."""
from typing import Annotated
from uuid import UUID
from .auth import AuthenticatedPrincipal
from .remote_executions import RemoteExecutionConflict


def create_quiz_router(require_principal,service):
    from fastapi import APIRouter,Depends,HTTPException,Response
    from pydantic import BaseModel,ConfigDict,Field
    router=APIRouter(prefix='/v1/remote-quizzes')
    Principal=Annotated[AuthenticatedPrincipal,Depends(require_principal)]
    class Grant(BaseModel):
        model_config=ConfigDict(extra='forbid')
        notice_version:str=Field(min_length=1,max_length=100)
        quiz_revision:str=Field(min_length=64,max_length=64)
    def call(principal,response,method,*args):
        response.headers['Cache-Control']='no-store'
        if principal.credential_type!='web_session':
            raise HTTPException(403,detail={'code':'web_session_required'})
        if service is None:
            raise HTTPException(503,detail={'code':'quiz_unavailable'})
        try:
            value=getattr(service,method)(principal.user_id,*args)
        except (ValueError,RemoteExecutionConflict):
            raise HTTPException(409,detail={'code':'quiz_conflict'}) from None
        except Exception:
            raise HTTPException(503,detail={'code':'quiz_unavailable'}) from None
        if value is None:
            raise HTTPException(404,detail={'code':'quiz_not_found'})
        return value
    @router.get('/{context}/{quiz_id}/consent')
    def view(context:UUID,quiz_id:UUID,principal:Principal,response:Response):
        return call(principal,response,'consent',str(context),str(quiz_id))
    @router.post('/{context}/{quiz_id}/consent')
    def grant(context:UUID,quiz_id:UUID,body:Grant,principal:Principal,response:Response):
        return call(principal,response,'grant_storage',str(context),str(quiz_id),body.notice_version,body.quiz_revision)
    @router.post('/{context}/{quiz_id}/consent/revoke')
    def revoke(context:UUID,quiz_id:UUID,principal:Principal,response:Response):
        return call(principal,response,'revoke_storage',str(context),str(quiz_id))
    return router
