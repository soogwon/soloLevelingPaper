"""고정 퀴즈 제출과 저장 시도 복습 카드. 생성·동의는 별도 흐름을 유지한다."""
import hashlib
import hmac
from pathlib import Path
from typing import Any
from uuid import UUID
from mcp.types import ToolAnnotations

URI = 'ui://remote-quiz/attempt.html'
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def register_quiz_cards(server, service, call, access_token):
    def binding(owner, context, quiz):
        return hmac.new(access_token().token.encode(),
                        f'remote-quiz-card-v1:{owner}:{context}:{quiz}'.encode(), hashlib.sha256).hexdigest()

    def scoped(owner, context, quiz, expected):
        context, quiz = str(UUID(context)), str(UUID(quiz))
        current = binding(owner, context, quiz)
        if expected is not None and not hmac.compare_digest(current, expected):
            raise ValueError('remote_quiz_connection_changed')
        return context, quiz, current

    @server.resource(URI, mime_type='text/html;profile=mcp-app')
    def remote_quiz_card():
        return Path(__file__).with_name('assets').joinpath('remote-quiz-attempt.html').read_text(encoding='utf-8')

    @server.tool(meta={'ui': {'resourceUri': URI}}, annotations=READ)
    def show_remote_quiz(context_id: str, quiz_id: str, expected_binding: str | None = None) -> dict:
        """저장한 문항의 제출 카드를 연다. 정답은 숨기며 인증 웹에서 답안 저장 동의를 먼저 확인한다. 생성하지 않는다."""
        def operation(owner):
            ctx, quiz, bound = scoped(owner, context_id, quiz_id, expected_binding)
            value = service.get(owner, ctx, quiz)
            if value is None:
                return None
            return dict(context_id=ctx, quiz_id=quiz, binding=bound, quiz=value,
                        consent=service.consent(owner, ctx, quiz))
        return call('quizzes:read', operation)

    @server.tool(meta={'ui': {'visibility': ['app']}},
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def submit_remote_quiz_card(context_id: str, quiz_id: str, request_key: str, answers: dict[str, Any],
                                storage_consent_id: str, expected_binding: str) -> dict:
        """사용자가 확인한 답안을 같은 키로 저장한다. 응답 유실 후에는 요청 결과 조회를 먼저 사용한다."""
        def operation(owner):
            ctx, quiz, bound = scoped(owner, context_id, quiz_id, expected_binding)
            if (type(answers) is not dict or not 1 <= len(answers) <= 20
                    or any(not k.isascii() or not k.isdecimal() or len(k)>2 or str(int(k))!=k for k in answers)):
                raise ValueError('remote_quiz_answers_invalid')
            attempt = service.submit(owner, ctx, quiz, request_key, {int(k): v for k,v in answers.items()}, storage_consent_id)
            if attempt is None:
                return None
            return dict(context_id=ctx, quiz_id=quiz, binding=bound, request_key=str(UUID(request_key)), attempt=attempt)
        return call('quizzes:write', operation)

    @server.tool(annotations=READ)
    def get_remote_quiz_submission(context_id: str, quiz_id: str, request_key: str,
                                    expected_binding: str | None = None) -> dict:
        """유실된 제출 결과를 같은 키로 읽는다. attempt=null이면 미확인 상태이며 자동 재제출하지 않는다."""
        def operation(owner):
            ctx, quiz, bound = scoped(owner, context_id, quiz_id, expected_binding)
            value = service.attempt_by_key(owner, ctx, quiz, request_key)
            return None if value is None else dict(value, binding=bound)
        return call('quizzes:read', operation)

    @server.tool(meta={'ui': {'resourceUri': URI}}, annotations=READ)
    def show_remote_quiz_review(context_id: str, attempt_id: str) -> dict:
        """저장된 점수·오답·정답과 당시 근거 ID를 복습 카드로 조회한다. 새 생성이나 채점은 없다."""
        def operation(owner):
            ctx = str(UUID(context_id))
            attempt = service.attempt(owner, ctx, str(UUID(attempt_id)))
            if attempt is None:
                return None
            quiz = str(UUID(attempt['quiz_id']))
            return dict(context_id=ctx, quiz_id=quiz, binding=binding(owner, ctx, quiz), attempt=attempt,
                        request_key=attempt['request_key'])
        return call('quizzes:read', operation)
