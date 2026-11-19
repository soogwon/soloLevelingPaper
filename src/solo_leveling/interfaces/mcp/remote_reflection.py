"""원격 성찰은 저장 답변만 사용하며 동의는 웹에서 별도로 받는다."""
import time
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict


def register_remote_reflection(server,queries,service):
    def call(scope,method,*args,**kwargs):
        token=get_access_token()
        if (not isinstance(token,RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at<=time.time() or scope not in token.scopes):
            return {'status':'not_authorized'}
        owner=queries.owner()
        if owner is None:
            return {'status':'not_authorized'}
        try:
            result=getattr(service,method)(owner,*args,**kwargs)
            return {'status':'not_found'} if result is None else {'status':'ok','result':result}
        except RemoteExecutionConflict:
            return {'status':'conflict','message':'최신 질문·답변 revision을 다시 조회하세요. 자동 덮어쓰기는 하지 않습니다.'}
        except ValueError:
            return {'status':'invalid_argument','message':'입력과 성찰 저장 동의를 확인하세요.'}
        except Exception:
            return {'status':'unavailable','message':'같은 키로 다시 확인하세요. 새 키로 자동 저장하지 마세요.'}

    @server.tool()
    def get_reading_questions(context_id:str)->dict:
        """고정 판본의 검수 질문과 현재 저장 답변만 조회한다. 저장·AI 생성은 하지 않는다."""
        return call('reflections:read','questions',context_id)

    @server.tool()
    def get_reading_comparison(context_id:str)->dict:
        """저장된 읽기 전후 답변을 나란히 조회한다. 이해 향상·정답 여부를 추측하지 않는다."""
        return call('reflections:read','comparison',context_id)

    @server.tool()
    def get_reading_storage_consent(context_id:str)->dict:
        """웹에서 기록한 성찰 저장 동의를 조회한다. 이 도구로 동의를 생성하지 않는다."""
        return call('reflections:read','consent',context_id)

    @server.tool()
    def save_reading_answer(context_id:str,request_key:str,question_revision:str,template_id:str,
                            body:str,status:str,expected_revision:int,storage_consent_id:str)->dict:
        """명시적으로 작성한 성찰 답변을 저장한다. 웹 동의와 최신 revision이 필요하며 응답 유실은 같은 키를 쓴다."""
        return call('reflections:write','save',context_id,request_key,question_revision=question_revision,
                    template_id=template_id,body=body,status=status,expected_revision=expected_revision,
                    storage_consent_id=storage_consent_id)

    @server.tool()
    def delete_reading_answer(context_id:str,template_id:str,expected_revision:int)->dict:
        """사용자가 선택한 답변과 과거 요청 이력 본문을 삭제한다. 충돌 시 최신 revision을 다시 조회한다."""
        return call('reflections:write','delete_answer',context_id,template_id,expected_revision)
