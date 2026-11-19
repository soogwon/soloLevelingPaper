"""고정 원격 퀴즈의 조회·제출·이력. AI 생성과 검수 퀴즈 쓰기는 노출하지 않는다."""
from typing import Any
import time
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict


def register_remote_quiz(server,queries,service):
    def call(scope,method,*args,**kwargs):
        token=get_access_token()
        if (not isinstance(token,RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at<=time.time() or scope not in token.scopes):
            return {'status':'not_authorized'}
        owner=queries.owner()
        if owner is None:return {'status':'not_authorized'}
        try:
            value=(method(owner) if callable(method) else getattr(service,method)(owner,*args,**kwargs))
            return {'status':'not_found'} if value is None else {'status':'ok','result':value}
        except RemoteExecutionConflict:
            return {'status':'conflict','message':'같은 키의 퀴즈나 답안을 바꿀 수 없습니다. 기존 시도를 조회하세요.'}
        except ValueError:
            return {'status':'invalid_argument','message':'퀴즈 판본·답안 형식·저장 동의를 확인하세요.'}
        except Exception:
            return {'status':'unavailable','message':'같은 키로 확인하세요. 자동 재응시하지 마세요.'}

    @server.tool()
    def get_quiz(context_id:str,quiz_id:str)->dict:
        """이미 저장한 고정 퀴즈만 조회한다. 정답·채점 기준은 제출 전에 보여주지 않으며 새 생성은 없다."""
        return call('quizzes:read','get',context_id,quiz_id)

    @server.tool()
    def submit_quiz(context_id:str,quiz_id:str,request_key:str,answers:dict[str,Any],storage_consent_id:str)->dict:
        """동의한 답안을 같은 요청 키로 한 번 저장한다. 키는 문항 위치의 십진수 문자열이며 새 키는 의도적 재응시다."""
        if (type(answers) is not dict or not 1<=len(answers)<=20
                or any(not isinstance(k,str) or not k.isascii() or not k.isdecimal()
                       or len(k)>2 or str(int(k))!=k for k in answers)):
            return {'status':'invalid_argument'}
        return call('quizzes:write','submit',context_id,quiz_id,request_key,
                    {int(k):v for k,v in answers.items()},storage_consent_id)

    @server.tool()
    def get_quiz_attempt(context_id:str,attempt_id:str)->dict:
        """소유한 저장 시도와 제출 당시 문항·정답·근거 ID를 조회한다. 재채점하지 않는다."""
        return call('quizzes:read','attempt',context_id,attempt_id)

    @server.tool()
    def list_quiz_attempts(context_id:str,limit:int=20,cursor:str|None=None)->dict:
        """최대 100건씩 제출 이력을 읽는다. cursor는 같은 맥락의 이전 응답에 있던 값만 사용한다."""
        return call('quizzes:read','history',context_id,limit=limit,cursor=cursor)

    @server.tool()
    def get_quiz_step_scores(context_id:str)->dict:
        """단계별 시도 횟수·최신/최고 점수를 읽는다. 시도가 없으면 점수는 null이다."""
        return call('quizzes:read','scores',context_id)

    @server.tool()
    def get_wrong_answer_review(context_id:str,attempt_id:str)->dict:
        """저장 시도의 오답과 고정 근거 ID를 반환한다. 근거 본문을 추측하지 않으며 새 호출은 없다."""
        result=call('quizzes:read','attempt',context_id,attempt_id)
        if result.get('status')=='ok':
            payload=result['result']
            result['result']={**payload,'results':[r for r in payload['results'] if not r['is_correct']]}
        return result

    @server.tool()
    def get_remote_quiz_generation_result(context_id: str, request_key: str) -> dict:
        """기존 생성 요청 키로 상태와 정답을 숨긴 완료 퀴즈 ID를 조회한다. 새 생성·전송은 없다."""
        return call('quizzes:read', 'generation_result', context_id, request_key)

    from .remote_quiz_cards import register_quiz_cards
    register_quiz_cards(server, service, call, get_access_token)
