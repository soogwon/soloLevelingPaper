"""인증된 원격 메모 도구. 동의 부여 도구와 로컬 경로 인자는 노출하지 않는다."""
from dataclasses import asdict, is_dataclass
import time
from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import RecoveryInput
from solo_leveling.interfaces.http.remote_annotation_consents import AnnotationStorageConsentRequired
from solo_leveling.interfaces.http.remote_annotations import (
    RemoteAnnotationConflict, RemoteAnnotationDeleted, RemoteAnnotationReferenceError,
    RemoteAnnotationRecoveryAmbiguous,
)


def register_annotation_tools(server, queries, service):
    def call(write, operation):
        try:
            token = get_access_token()
            scope = 'annotations:write' if write else 'annotations:read'
            if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                    or type(token.expires_at) is not int or token.expires_at <= time.time()
                    or scope not in token.scopes):
                return {'status': 'not_authorized'}
            owner = queries.owner()
            if owner is None:
                return {'status': 'not_authorized'}
            result = operation(owner)
            if result is None:
                return {'status': 'not_found'}
            if is_dataclass(result):
                result = asdict(result)
            response = {'status': 'ok', 'result': result}
            if isinstance(result, dict) and result.get('learning_record_id') and result.get('context_id'):
                response['web_path'] = '/#/remote-learning/' + str(UUID(result['context_id']))
            return response
        except AnnotationStorageConsentRequired:
            return {'status': 'consent_required', 'message': '인증된 웹에서 이 학습 맥락의 메모 저장 동의를 확인하세요.'}
        except RemoteAnnotationRecoveryAmbiguous:
            return {'status': 'ambiguous', 'message': '복구 후보를 자동 선택하거나 새 키로 재생성하지 마세요.'}
        except RemoteAnnotationConflict:
            return {'status': 'conflict', 'message': '현재 기록과 revision을 다시 조회하세요.'}
        except RemoteAnnotationDeleted:
            return {'status': 'deleted', 'message': '삭제된 기록을 새 키로 재생성하지 마세요.'}
        except RemoteAnnotationReferenceError:
            return {'status': 'not_found'}
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        except Exception:
            return {'status': 'unavailable', 'message': '기존 요청 키로 조회하세요. 자동 재제출하지 마세요.'}

    def context(value):
        return str(UUID(value))

    @server.tool()
    def start_remote_learning(context_id: str) -> dict:
        """사용자가 학습 시작을 명시적으로 요청할 때만 호출한다. 기존 맥락의 기록을 재사용하며 동의나 AI 실행을 시작하지 않는다."""
        return call(True, lambda owner: service.learning.start(owner, context(context_id)))

    @server.tool()
    def get_remote_learning(context_id: str) -> dict:
        """기존 학습 기록만 조회한다. 없는 기록을 만들지 않는다."""
        return call(False, lambda owner: service.learning.get(owner, context(context_id)))

    @server.tool()
    def create_remote_annotation(context_id: str, request_key: str, value: dict) -> dict:
        """명시적 학습 시작과 인증 웹의 맥락 저장 동의 후 메모·하이라이트를 저장한다. 같은 키를 보관하고 응답 유실 시 재생성하지 않는다. value는 kind/body/segments/step/creation_origin이다."""
        return call(True, lambda owner: service.create(owner, context(context_id), request_key, RecoveryInput.model_validate(value).domain()))

    @server.tool()
    def get_remote_annotation(context_id: str, annotation_id: str) -> dict:
        """현재 맥락의 살아 있는 기록과 revision을 조회한다."""
        return call(False, lambda owner: service.store.get_annotation(owner, context(context_id), context(annotation_id)))

    @server.tool()
    def list_remote_annotations(context_id: str, after: str | None = None, limit: int = 20) -> dict:
        """현재 학습 맥락의 메모·하이라이트를 커서로 조회한다. 새로고침은 첫 페이지부터 한다."""
        return call(False, lambda owner: service.store.list_annotations(owner, context(context_id), after=after, limit=limit))

    @server.tool()
    def update_remote_annotation(context_id: str, annotation_id: str, revision: int, body: str, step: str | None = None) -> dict:
        """현재 revision과 유효한 저장 동의로 본문·단계만 수정한다. 근거 구간은 바꾸지 않는다."""
        return call(True, lambda owner: service.update(owner, context(context_id), context(annotation_id), body, step, revision))

    @server.tool()
    def delete_remote_annotation(context_id: str, annotation_id: str, revision: int) -> dict:
        """명시적 삭제 요청으로 현재 revision을 삭제한다. 동의 철회 후에도 삭제할 수 있다."""
        return call(True, lambda owner: service.store.delete_annotation(owner, context(context_id), context(annotation_id), expected_revision=revision))

    @server.tool()
    def lookup_remote_annotation_request(context_id: str, request_key: str) -> dict:
        """응답이 유실된 기존 키의 현재 결과나 삭제 상태만 조회한다."""
        return call(False, lambda owner: service.lookup(owner, context(context_id), request_key))

    @server.tool()
    def recover_remote_annotation_request(context_id: str, value: dict) -> dict:
        """초기 입력 전체로 미확정 결과를 복구한다. 본문 일부 검색이나 새 키 생성으로 대체하지 않는다."""
        return call(False, lambda owner: service.recover(owner, context(context_id), RecoveryInput.model_validate(value).domain()))

    @server.tool()
    def confirm_remote_annotation_request(context_id: str, request_key: str, annotation_id: str, revision: int) -> dict:
        """제품이 동일 맥락·ID·revision의 응답 수신을 검증한 후 확정한다. false면 현재 결과를 재조회한다."""
        return call(True, lambda owner: {'confirmed': service.confirm(owner, context(context_id), request_key, context(annotation_id), revision)})
