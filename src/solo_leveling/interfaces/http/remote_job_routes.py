"""웹 세션의 소유권과 선택 판본에 한정한 읽기 전용 작업 조회."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from .auth import AuthenticatedPrincipal
from .remote_job_queries import RemoteJobQueries


def create_web_job_router(require_principal, pool):
    router = APIRouter(prefix='/v1/papers')
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.get('/{paper_id}/processing-jobs')
    def list_jobs(paper_id: UUID, principal: Principal, response: Response,
                  version_id: Annotated[str, Query(min_length=1, max_length=128)],
                  after: UUID | None = None,
                  limit: Annotated[int, Query(ge=1, le=50)] = 20):
        headers = {'Cache-Control': 'no-store'}
        response.headers.update(headers)
        if principal.credential_type != 'web_session':
            raise HTTPException(403, {'code': 'web_session_required'}, headers=headers)
        result = RemoteJobQueries(pool, lambda: principal.user_id).for_paper(
            str(paper_id), version_id, str(after) if after else None, limit)
        if result['status'] != 'ok':
            status, code = {'not_found': (404, 'paper_not_found'),
                            'invalid_argument': (422, 'invalid_job_query')}.get(
                                result['status'], (503, 'processing_jobs_unavailable'))
            raise HTTPException(status, {'code': code}, headers=headers)
        return result

    return router
