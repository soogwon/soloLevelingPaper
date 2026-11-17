"""검증된 context와 실행을 실제 원격 검색·출처·원자 저장에 연결한다. 도구 등록은 없다."""
from copy import deepcopy

from solo_leveling.application.evidence_qa.remote_answer import RemoteAnswerPreparationService
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding, RemoteSearchFilters
from solo_leveling.application.evidence_qa.remote_input import question_input_hash
from solo_leveling.application.evidence_qa.remote_search import RemoteContextSearchService, FingerprintCheckedEmbedder
from solo_leveling.application.provider_dispatch import DispatchIntent, DispatchError
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.interfaces.http.remote_executions import validate_execution_scope
from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader
from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch


class RemoteAnswerExecutionError(RuntimeError):
    """본문·원시 저장소 오류를 포함하지 않는 내부 오류."""


class PersistedRemoteAnswerService:
    def __init__(self, resolver, preparation, writer, validate_scope, processing_config):
        self._resolver, self._preparation, self._writer = resolver, preparation, writer
        self._validate_scope = validate_scope
        self._config = deepcopy(processing_config)

    def execute(self, intent, question, context_id, *, filters=RemoteSearchFilters(), top_k=5):
        if not isinstance(intent, DispatchIntent):
            raise RemoteAnswerExecutionError('REMOTE_EXECUTION_INVALID')
        try:
            binding = self._resolver(context_id)
            if (not isinstance(binding, RemoteSearchBinding) or binding.context_id != context_id
                    or binding.owner_user_id != intent.owner_user_id):
                raise RemoteAnswerExecutionError('REMOTE_EXECUTION_SCOPE_LOST')
            expected = question_input_hash(binding, question, filters, top_k, self._config)
            if expected != intent.input_hash:
                raise RemoteAnswerExecutionError('REMOTE_EXECUTION_INPUT_CONFLICT')
            if self._validate_scope(intent) is not True:
                raise RemoteAnswerExecutionError('REMOTE_EXECUTION_SCOPE_LOST')
            prepared = self._preparation.prepare(question, context_id, filters=filters, top_k=top_k)
            if prepared.binding != binding:
                raise RemoteAnswerExecutionError('REMOTE_EXECUTION_SCOPE_LOST')
        except (RemoteAnswerExecutionError, DispatchError):
            # 예산/동의 거부·usage unknown은 고정 내부 전송 오류로 유지한다.
            # 저장소/제공자의 원시 오류를 공개하거나 근거 부족으로 바꾸지 않는다.
            raise
        except Exception:
            raise RemoteAnswerExecutionError('REMOTE_ANSWER_PREPARATION_FAILED') from None
        try:
            # 저장 전 준비 결과를 반환하지 않고 commit이 성공한 저장 결과만 반환한다.
            if self._validate_scope(intent) is not True:
                raise RemoteAnswerExecutionError('REMOTE_EXECUTION_SCOPE_LOST')
            return self._writer.save(intent, prepared)
        except Exception:
            raise RemoteAnswerExecutionError('REMOTE_ANSWER_SAVE_FAILED') from None


def create_persisted_remote_answer_service(pool, resolve_authenticated_context, embedding_runtime,
                                          generator, processing_config, *, semantic_verification=None, check_allowed=lambda: True, id_factory=None):
    """resolver는 현재 인증 owner를 해석하는 필수 포트다. 실제 생성기는 별도 dispatch gate가 필요하다."""
    def validate(intent):
        if check_allowed() is not True:
            return False
        with pool.connection() as conn:
            return validate_execution_scope(conn, intent)

    search = RemoteContextSearchService(resolve_authenticated_context,
        PostgresRemoteScopedSearch(pool, FingerprintCheckedEmbedder(embedding_runtime)))
    preparation = RemoteAnswerPreparationService(search, PostgresRemoteSourceReader(pool), generator,
                                                 semantic_verification=semantic_verification, id_factory=id_factory)
    return PersistedRemoteAnswerService(resolve_authenticated_context, preparation,
                                       PostgresRemoteAnswerStore(pool), validate, processing_config)
