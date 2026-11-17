"""MCP의 검토 준비와 이미 웹 동의를 마친 질문 접수. confirmed 입력은 받지 않는다."""
import time
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict


def register_question_tools(server,queries,reviews):
    def call(method,*args,**kwargs):
        token=get_access_token()
        if (not isinstance(token,RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at<=time.time()
                or 'questions:write' not in token.scopes):return {'status':'not_authorized'}
        owner=queries.owner()
        if owner is None:return {'status':'not_authorized'}
        try:
            result=getattr(reviews,method)(owner,*args,**kwargs)
            return {'status':'not_found'} if result is None else result
        except RemoteExecutionConflict:return {'status':'conflict','message':'같은 요청 키의 입력을 변경할 수 없습니다.'}
        except ValueError:return {'status':'review_required','message':'인증된 웹에서 현재 동의와 질문 상태를 확인하세요. 새 키로 자동 재제출하지 마세요.'}
        except Exception:return {'status':'unavailable','message':'같은 요청 키로 상태를 확인하세요. 자동 재제출하지 마세요.'}
    @server.tool()
    def prepare_remote_question(request_key:str,context_id:str,question:str,pages:list[int]=[],sections:list[str]=[],strict:bool=False,top_k:int=5)->dict:
        """질문과 검색 조건을 서버에 검토 초안으로 저장한다. UUID 요청 키를 보관한다. AI는 호출하지 않으며 인증 웹에서 저장·AI 전송 동의를 따로 받아야 한다."""
        return call('prepare',request_key,context_id,question,pages=pages,sections=sections,strict=strict,top_k=top_k)
    @server.tool()
    def submit_remote_question(request_key:str)->dict:
        """인증 웹에서 두 동의를 마친 기존 질문만 접수한다. 모델의 동의 주장으로 대체하지 않는다. 응답 유실 시 같은 키로 기존 상태를 조회한다."""
        return call('submit',request_key)
