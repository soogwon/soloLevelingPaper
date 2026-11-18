"""취소 전용 권한과 현재 연결에 묶인 명시적 확인·읽기 전용 복구 도구."""
import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import UUID
from typing import Any
from pydantic import StrictInt, StrictBool
from mcp.types import ToolAnnotations
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.processing_jobs import ProcessingJobConflict, ProcessingJobNotFound
from solo_leveling.interfaces.http.remote_job_queries import RemoteJobQueries


def register_job_cancellation_tools(server, queries, service):
    uri='ui://remote-jobs/cancel.html'
    def call(write, operation):
        try:
            token=get_access_token()
            if (not isinstance(token,RemoteIdentityAccessToken) or token.verified_identity() is None
                    or type(token.expires_at) is not int or token.expires_at <= time.time()
                    or 'papers:read' not in token.scopes or (write and 'jobs:cancel' not in token.scopes)):
                return {'status':'not_authorized'}
            owner=queries.owner()
            if owner is None:
                return {'status':'not_authorized'}
            result=operation(owner,token)
            return {'status':'not_found'} if result is None else {'status':'ok','result':result}
        except ProcessingJobNotFound:
            return {'status':'not_found'}
        except ProcessingJobConflict:
            return {'status':'conflict','message':'작업을 다시 조회하고 새 revision에서 확인하세요. 자동 취소 재시도는 하지 마세요.'}
        except (ValueError,TypeError,AttributeError):
            return {'status':'invalid_argument'}
        except Exception:
            return {'status':'unavailable','message':'같은 요청 키로 결과만 조회하세요. 새 취소를 자동 전송하지 마세요.'}

    def signature(owner, token, job, key, revision):
        payload=json.dumps(['cancel-v1',owner,job,key,revision],separators=(',',':'))
        return hmac.new(token.token.encode(),payload.encode(),hashlib.sha256).hexdigest()

    @server.resource(uri,mime_type='text/html;profile=mcp-app')
    def cancellation_card():
        return Path(__file__).with_name('assets').joinpath('remote-job-cancellation.html').read_text()

    @server.tool(meta={'ui':{'resourceUri':uri}},annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    def prepare_remote_job_cancellation(job_id: str, request_key: str) -> dict[str,Any]:
        """취소 대상을 조회해 확인 카드와 텍스트를 반환한다. 아직 취소하지 않는다. 사용자 확인 전 confirm을 호출하지 않는다."""
        def operation(owner,token):
            job,key=str(UUID(job_id)),str(UUID(request_key))
            recovered=service.lookup(owner,job,key)
            if recovered is not None:
                return {'job_id':job,'request_key':key,'recovered':recovered,'can_cancel':False}
            response=RemoteJobQueries(service._pool,lambda:owner,True).get(job)
            if response['status']=='not_found':
                return None
            if response['status']!='ok':
                raise RuntimeError('job_unavailable')
            current=response['result']
            return {'job_id':job,'request_key':key,'job':current,
                    'can_cancel':current['status'] in ('queued','processing'),
                    'expected_revision':current['revision'],
                    'binding':signature(owner,token,job,key,current['revision']),
                    'notice':'이 작업의 새 실행과 게시를 중단합니다. 이미 시작된 외부 호출·발생 비용은 되돌리지 않습니다. 취소하려면 확인 버튼을 직접 누르세요.'}
        return call(True,operation)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=False))
    def confirm_remote_job_cancellation(job_id: str, request_key: str, expected_revision: StrictInt,
                                       binding: str, confirmed: StrictBool) -> dict[str,Any]:
        """사용자가 지정 작업의 취소를 명시적으로 확인한 경우만 호출한다. 같은 키·revision을 유지하고 불명확한 응답은 조회로 복구한다."""
        def operation(owner,token):
            job,key=str(UUID(job_id)),str(UUID(request_key))
            if confirmed is not True or expected_revision < 1:
                raise ValueError('confirmation_required')
            if not hmac.compare_digest(signature(owner,token,job,key,expected_revision),binding):
                return {'connection_changed':True}
            return service.cancel(owner,job,key,expected_revision)
        return call(True,operation)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    def get_remote_job_cancellation(job_id: str, request_key: str) -> dict[str,Any]:
        """기존 취소 요청만 조회한다. 미존재·응답 유실·재로그인 시 새 취소를 만들지 않는다."""
        return call(False,lambda owner,token:service.lookup(owner,str(UUID(job_id)),str(UUID(request_key))))
