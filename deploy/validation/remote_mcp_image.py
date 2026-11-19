"""설치 이미지에서 제품 조회 경로를 조립한다. DB·인증 서버에 접속하지 않는다."""
import asyncio
import sys
import os
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.settings import RemoteMcpSettings

settings = RemoteMcpSettings(
    enabled=True, product_reads_enabled=True,
    resource_url='https://mcp.example.com/mcp', audience='https://mcp.example.com/mcp',
    issuer='https://auth.example.com/', jwks_url='https://auth.example.com/jwks',
    allowed_hosts=('mcp.example.com',), allowed_client_ids=('synthetic-client',),
)
# 진짜 조회 객체의 import와 전용 풀 조립까지 검사하며 lifespan은 시작하지 않는다.
app = create_app(remote_mcp_settings=settings)
assert app is not None
assert 'solo_leveling.interfaces.mcp.server' not in sys.modules
from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
class Queries:
    pass
bundle = create_remote_mcp(settings, product_queries=Queries())
names = {tool.name for tool in asyncio.run(bundle.server.list_tools())}
assert {'list_remote_papers', 'open_remote_paper', 'get_remote_question', 'get_learning_path', 'get_prerequisites'} <= names
assert 'submit_paper_question' not in names
assert 'open_remote_pdf_registration' in names
registration = asyncio.run(bundle.server.call_tool('open_remote_pdf_registration', {}))
assert 'https://mcp.example.com/#/uploads' in str(registration)
assert 'PDF 등록' in str(asyncio.run(bundle.server.read_resource('ui://remote-paper/register-pdf.html')))
print('REMOTE_PRODUCT_IMAGE_OK; no database or provider calls')

# 동의 웹과 MCP 쓰기 기능을 함께 켠 조립도 네트워크 없이 확인한다.
os.environ.update({
    'REMOTE_ANNOTATIONS_ENABLED': 'true',
    'REMOTE_ANNOTATION_WEB_DATABASE_URL': 'postgresql://web:synthetic@127.0.0.1/test',
    'REMOTE_ANNOTATION_MCP_DATABASE_URL': 'postgresql://mcp:synthetic@127.0.0.1/test',
    'REMOTE_ANNOTATION_SERVICE_ID': 'image-verification',
})
annotation_app = create_app(remote_mcp_settings=settings)
assert any(getattr(route, 'path', '') == '/v1/remote-annotation-products/contexts/{context}/consent'
           for route in annotation_app.routes)
from solo_leveling.interfaces.http.remote_annotation_products import RemoteAnnotationProducts
annotation_bundle = create_remote_mcp(settings, product_queries=Queries(),
    annotation_service=RemoteAnnotationProducts(None, 'image-verification'))
annotation_names = {tool.name for tool in asyncio.run(annotation_bundle.server.list_tools())}
assert {'start_remote_learning', 'create_remote_annotation', 'list_remote_annotations',
        'update_remote_annotation', 'delete_remote_annotation',
        'recover_remote_annotation_request', 'confirm_remote_annotation_request',
        'list_remote_evidence_annotations', 'show_remote_evidence_detail', 'show_remote_paper_detail'} <= annotation_names
assert not any('consent' in name for name in annotation_names)
assert {'annotations:read', 'annotations:write'} <= set(annotation_bundle.server.settings.auth.required_scopes)
print('REMOTE_ANNOTATION_IMAGE_OK; no database or provider calls')

assert '원격 근거 상세' in str(asyncio.run(annotation_bundle.server.read_resource('ui://remote-evidence/detail.html')))

assert '원격 논문 상세' in str(asyncio.run(annotation_bundle.server.read_resource('ui://remote-paper/detail-v3.html')))

from solo_leveling.knowledge import load_graph
assert load_graph().revision_id

# 취소 역할·카드가 이미지에 포함됐는지 DB 접속 없이 검사한다.
os.environ.update({
    'REMOTE_JOB_CANCELLATIONS_ENABLED': 'true',
    'REMOTE_JOB_CANCELLATION_DATABASE_URL': 'postgresql://cancel:synthetic@127.0.0.1/test',
})
assert create_app(remote_mcp_settings=settings) is not None
from solo_leveling.interfaces.http.processing_cancellations import PostgresProcessingCancellationStore
cancel_bundle = create_remote_mcp(settings, product_queries=Queries(),
    cancellation_service=PostgresProcessingCancellationStore(None))
assert {'prepare_remote_job_cancellation', 'confirm_remote_job_cancellation',
        'get_remote_job_cancellation'} <= {tool.name for tool in asyncio.run(cancel_bundle.server.list_tools())}
assert '처리 작업 취소 확인' in str(asyncio.run(cancel_bundle.server.read_resource('ui://remote-jobs/cancel.html')))
print('REMOTE_JOB_CANCEL_IMAGE_OK; no database or provider calls')

# 실제 배포처럼 성찰 기능을 켜서 도메인·MCP 모듈 누락을 검출한다.
os.environ.update({
    'REMOTE_REFLECTIONS_ENABLED': 'true',
    'REMOTE_REFLECTION_WEB_DATABASE_URL': 'postgresql://reflection_web:synthetic@127.0.0.1/test',
    'REMOTE_REFLECTION_MCP_DATABASE_URL': 'postgresql://reflection_mcp:synthetic@127.0.0.1/test',
})
reflection_app = create_app(remote_mcp_settings=settings)
assert any(getattr(route, 'path', '').startswith('/v1/remote-reflections/')
           for route in reflection_app.routes)
from solo_leveling.interfaces.http.remote_reflection import PostgresRemoteReflection
reflection_bundle = create_remote_mcp(settings, product_queries=Queries(),
    reflection_service=PostgresRemoteReflection(None))
assert {'get_reading_questions', 'get_reading_comparison', 'get_reading_storage_consent',
        'save_reading_answer', 'delete_reading_answer'} <= {
    tool.name for tool in asyncio.run(reflection_bundle.server.list_tools())}
print('REMOTE_REFLECTION_IMAGE_OK; no database or provider calls')

# 퀴즈 검토 링크·상태 도구만 조립하며 실제 저장소나 생성기는 연결하지 않는다.
from solo_leveling.interfaces.http.remote_quiz_generation_reviews import PostgresQuizGenerationReviews
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
quiz_reviews = PostgresQuizGenerationReviews(None, settings=dict(provider='synthetic', model='synthetic',
    prompt_version='v1', language='ko', budget_policy_version='synthetic'),
    policy=RemoteExecutionPolicy('synthetic', 120, 60, 1, 1000, 100), window_key='test',
    review_seconds=60, web_origin='https://example.invalid')
quiz_review_bundle = create_remote_mcp(settings, product_queries=Queries(), quiz_review_service=quiz_reviews)
assert {'prepare_remote_quiz_generation', 'get_remote_quiz_generation_review'} <= {
    tool.name for tool in asyncio.run(quiz_review_bundle.server.list_tools())}
assert any(getattr(route, 'path', '') == '/v1/remote-quiz-generation/reviews/{key}'
           for route in create_app(remote_mcp_settings=settings, remote_quiz_reviews=quiz_reviews).routes)
print('REMOTE_QUIZ_REVIEW_IMAGE_OK; no database or provider calls')


# 퀴즈 제출 조립은 로컬 SQLite 저장소를 가져오지 않고도 가능해야 한다.
from solo_leveling.interfaces.http.remote_quiz import PostgresRemoteQuiz
from solo_leveling.interfaces.mcp.remote_quiz import register_remote_quiz
from mcp.server.fastmcp import FastMCP
from types import SimpleNamespace
from pathlib import Path
register_remote_quiz(FastMCP('합성 퀴즈 이미지'), SimpleNamespace(owner=lambda: None), PostgresRemoteQuiz(None))
assert Path('/app/src/solo_leveling/interfaces/mcp/assets/remote-quiz-attempt.html').is_file()
print('REMOTE_QUIZ_IMAGE_READY')

# 요약 활성화 조립·웹 조회·내보내기 import를 네트워크 없는 이미지에서 확인한다.
import json
from solo_leveling.interfaces.http.remote_summary_settings import load_summary_settings
from solo_leveling.interfaces.http.remote_summary_reviews import PostgresSummaryReviews
from solo_leveling.application.summary.remote_export import export_summary
os.environ.update({
    'REMOTE_SUMMARIES_ENABLED':'true',
    'REMOTE_SUMMARY_DATABASE_URL':'postgresql://summary:synthetic@127.0.0.1/test',
    'REMOTE_SUMMARY_SETTINGS_JSON':json.dumps(dict(
        policy=dict(version='summary-test',timeout_seconds=120,stage_timeout_seconds=60,max_calls=1,max_input_tokens=1000,max_output_tokens=100),
        settings=dict(provider='openai',model='synthetic',prompt_version='remote-summary-v1',language='ko',budget_policy_version='summary-test'),
        window_key='test',review_seconds=60,web_origin='https://example.invalid')),
})
summary_settings = load_summary_settings()
summary_app = create_app(remote_mcp_settings=settings)
assert any(getattr(route,'path','')=='/v1/remote-summaries/contexts/{context}/latest' for route in summary_app.routes)
summary_bundle = create_remote_mcp(settings,product_queries=Queries(),
    summary_service=PostgresSummaryReviews(None,**summary_settings.arguments))
assert {'prepare_remote_summary','get_latest_remote_summary','export_remote_summary'} <= {
    tool.name for tool in asyncio.run(summary_bundle.server.list_tools())}
assert callable(export_summary)
print('REMOTE_SUMMARY_IMAGE_OK; no database or provider calls')

# 퀴즈도 실제 환경 설정으로 시작하며 기존 요약·성찰 연결과 함께 조립한다.
from solo_leveling.interfaces.http.remote_quiz_settings import load_quiz_settings
os.environ.update({
    'REMOTE_QUIZZES_ENABLED':'true',
    'REMOTE_QUIZ_DATABASE_URL':'postgresql://quiz:synthetic@127.0.0.1/test',
    'REMOTE_QUIZ_SETTINGS_JSON':json.dumps(dict(
        policy=dict(version='quiz-test',timeout_seconds=120,stage_timeout_seconds=60,max_calls=1,max_input_tokens=1000,max_output_tokens=100),
        settings=dict(provider='openai',model='synthetic',prompt_version='remote-quiz-generation-v1',language='ko',budget_policy_version='quiz-test'),
        window_key='test',review_seconds=60,web_origin='https://example.invalid')),
})
quiz_settings = load_quiz_settings()
quiz_app = create_app(remote_mcp_settings=settings)
assert any(getattr(route,'path','')=='/v1/remote-quizzes/{context}/{quiz_id}/consent' for route in quiz_app.routes)
quiz_bundle = create_remote_mcp(settings, product_queries=Queries(), quiz_service=PostgresRemoteQuiz(None),
    quiz_review_service=PostgresQuizGenerationReviews(None,**quiz_settings.arguments))
quiz_names = {tool.name for tool in asyncio.run(quiz_bundle.server.list_tools())}
assert {'prepare_remote_quiz_generation','get_quiz','submit_quiz'} <= quiz_names
print('REMOTE_QUIZ_BOOTSTRAP_IMAGE_OK; no database or provider calls')
