"""저장된 학습 정보 조회와 대시보드 리소스를 연결한다."""

from importlib.resources import files
from typing import Annotated, Literal, Protocol
from typing_extensions import TypedDict

import anyio
from mcp.types import ToolAnnotations
from pydantic import Field

from .errors import to_tool_error

DASHBOARD_URI = 'ui://solo-leveling/learning-dashboard.html'
Step = Literal['preview', 'structure', 'deep_read', 'review']


class DashboardService(Protocol):
    def get(self, context_id: str) -> dict: ...


class ProcessingOutput(TypedDict):
    job_id: str
    status: Literal['queued', 'processing', 'ready', 'failed', 'interrupted']
    stage: str | None
    limitations: list[str]


class ProgressOutput(TypedDict):
    recorded: bool
    current_step: Step
    completed_steps: list[Step]
    total_steps: int
    completion_ratio: float
    last_studied_at: str | None


class SummaryItemOutput(TypedDict):
    kind: Literal['claim', 'method', 'result', 'limitation']
    text: str
    evidence_ids: list[str]


class SummaryOutput(TypedDict):
    summary_id: str
    created_at: str
    items: list[SummaryItemOutput]


class QuizScoreOutput(TypedDict):
    step: Step
    attempt_count: int
    latest_attempt_id: str | None
    latest_correct: int | None
    latest_total: int | None
    latest_score: float | None
    best_score: float | None


class DashboardOutput(TypedDict):
    context_id: str
    version_id: str
    paper_id: str
    title: str | None
    file_display_name: str
    goal: Literal['understand', 'implement', 'skim']
    processing: ProcessingOutput
    progress: ProgressOutput
    summary: SummaryOutput | None
    quiz_scores: list[QuizScoreOutput]


def register_dashboard(server, reader: DashboardService):
    @server.resource(DASHBOARD_URI, name='learning-dashboard', title='학습 대시보드',
        mime_type='text/html;profile=mcp-app',
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def dashboard_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath(
            'assets/learning-dashboard.html').read_text(encoding='utf-8')

    @server.tool(name='get_learning_dashboard', structured_output=True,
        meta={'ui': {'resourceUri': DASHBOARD_URI}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False))
    async def get_learning_dashboard(
        context_id: Annotated[str, Field(strict=True, min_length=1)],
    ) -> DashboardOutput:
        """기존 context_id의 저장된 요약·학습 진행·퀴즈 결과를 조회합니다.

        처리 상태는 이 맥락의 색인을 게시한 작업 기준이며 최신 재처리 작업과 다를 수 있습니다.
        진행률은 완료한 학습 단계 비율이며 논문 처리율이나 이해도 평가가 아닙니다.
        요약 미생성·진행 기록 없음·퀴즈 기록 없음은 정상 상태입니다.
        조회는 생성·채점·단계 완료를 수행하지 않습니다. 메모·상세 화면은 후속 기능입니다.
        화면 미지원 시에도 같은 정보를 structuredContent와 text로 제공합니다.
        """
        try:
            return await anyio.to_thread.run_sync(lambda: reader.get(context_id))
        except Exception as error:
            raise to_tool_error(error) from None
