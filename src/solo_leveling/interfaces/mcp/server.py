"""B 서비스를 읽기 중심 MCP 도구로 노출한다."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, TypedDict

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from solo_leveling.application.evidence_qa.serialization import (
    serialize_answer_response, serialize_stored_evidence_result,
)
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.application.evidence_qa.ports import DefaultContextStore
from solo_leveling.domain.evidence_qa import require_text
from .errors import to_tool_error


class AnswerService(Protocol):
    def answer(self, question: str, *, context_id: str, top_k: int,
               pdf_pages: tuple[int, ...], section_ids: tuple[str, ...]): ...


class EvidenceService(Protocol):
    def get(self, context_id: str, evidence_ids: list[str]): ...


class IngestionService(Protocol):
    def add_local_pdf(self, relative_path: str, request_key: str) -> dict: ...

    def get_status(self, job_id: str) -> dict: ...


@dataclass(frozen=True)
class MCPServices:
    answer: AnswerService
    evidence: EvidenceService
    ingestion: IngestionService | None = None
    contexts: DefaultContextStore | None = None


class Focus(BaseModel):
    """물리 PDF 페이지와 섹션 ID의 교집합으로 검색 범위를 제한한다."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    pdf_pages: list[int] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)


class PaperSource(BaseModel):
    """설정된 import 루트 아래의 PDF 한 개만 지정한다."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['local_file']
    relative_path: str


class ClaimOutput(TypedDict):
    claim_id: str
    text: str
    evidence_ids: list[str]


class CitationOutput(TypedDict):
    evidence_id: str
    chunk_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    printed_page_label: str | None
    pdf_page: int
    quote_ko: str | None
    quote_original: str


class AskPaperOutput(TypedDict):
    context_id: str
    verification_level: str
    status: str
    answer_ko: str
    claims: list[ClaimOutput]
    citations: list[CitationOutput]
    reason_code: str | None


class EvidenceOutput(CitationOutput):
    file_display_name: str


class GetEvidenceOutput(TypedDict):
    evidence: list[EvidenceOutput]


class AddPaperOutput(TypedDict):
    paper_id: str
    version_id: str
    job_id: str
    status: str


class PaperStatusOutput(TypedDict):
    job_id: str
    version_id: str
    status: str
    stage: str | None
    capabilities: list[str]
    limitations: list[str]
    result_available: bool


class StartLearningOutput(TypedDict):
    context_id: str
    version_id: str
    goal: str
    known_concepts: list[str]
    status: Literal['ready']
    capabilities: list[str]


def _answer_payload(response) -> AskPaperOutput:
    serialized = serialize_answer_response(response)
    return {
        'context_id': serialized['context_id'],
        'verification_level': serialized['verification_level'],
        **serialized['answer'],
    }


def create_server(services: MCPServices) -> FastMCP:
    """저장소 수명이나 환경 설정에 관여하지 않고 MCP 도구만 구성한다."""
    server = FastMCP('solo-leveling-paper',
        instructions='등록된 논문에서 한국어 근거 답변과 원문 근거를 조회합니다.',
        log_level='ERROR')

    if services.ingestion is not None:
        @server.tool(name='add_paper', structured_output=True,
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                        idempotentHint=True, openWorldHint=True))
        async def add_paper(source: PaperSource, request_key: str) -> AddPaperOutput:
            """import 루트의 PDF를 관리 저장소로 복사하고 등록 작업을 시작합니다."""
            try:
                result = await anyio.to_thread.run_sync(
                    lambda: services.ingestion.add_local_pdf(source.relative_path, request_key))
                return {key: result[key] for key in ('paper_id', 'version_id', 'job_id', 'status')}
            except Exception as error:
                raise to_tool_error(error) from None

        @server.tool(name='get_paper_status', structured_output=True,
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                        idempotentHint=True, openWorldHint=False))
        async def get_paper_status(job_id: str) -> PaperStatusOutput:
            """등록·파싱·번역·색인 작업 상태와 사용 가능한 기능을 조회합니다."""
            try:
                result = await anyio.to_thread.run_sync(
                    lambda: services.ingestion.get_status(job_id))
                capabilities = (['start_learning']
                                if result['status'] == 'ready' else [])
                return {
                    'job_id': result['job_id'], 'version_id': result['version_id'],
                    'status': result['status'], 'stage': result['stage'],
                    'capabilities': capabilities, 'limitations': result['limitations'],
                    'result_available': result['result_available'],
                }
            except Exception as error:
                raise to_tool_error(error) from None

    if services.contexts is not None:
        @server.tool(name='start_learning', structured_output=True,
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                        idempotentHint=True, openWorldHint=False))
        async def start_learning(
            version_id: str,
            goal: Literal['understand', 'implement', 'skim'] = 'understand',
            known_concepts: list[str] | None = None,
        ) -> StartLearningOutput:
            """게시 완료된 논문 버전의 기본 학습 맥락을 생성하거나 재사용합니다."""
            try:
                try:
                    require_text(version_id, 'version_id')
                    concepts = []
                    for value in known_concepts or []:
                        require_text(value, 'known_concept')
                        concepts.append(value.strip())
                    if len(set(concepts)) != len(concepts):
                        raise ValueError('known_concepts에 중복 값이 있습니다.')
                except ValueError as error:
                    raise InvalidArgumentError(str(error)) from None
                context = await anyio.to_thread.run_sync(
                    lambda: services.contexts.get_or_create_learning_context(
                        version_id, goal, concepts,
                    ))
                return {
                    'context_id': context.context_id,
                    'version_id': context.version_id,
                    'goal': context.goal,
                    'known_concepts': list(context.known_concepts),
                    'status': 'ready',
                    'capabilities': ['ask_paper', 'get_evidence'],
                }
            except Exception as error:
                raise to_tool_error(error) from None

    @server.tool(name='ask_paper', structured_output=True,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=True))
    async def ask_paper(context_id: str, question: str,
                        standalone_question: str | None = None,
                        focus: Focus | None = None,
                        top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5) -> AskPaperOutput:
        """고정된 학습 맥락에서 질문하고 주장별 한국어·원문 근거를 반환합니다."""
        try:
            try:
                require_text(question, 'question')
                if standalone_question is not None:
                    require_text(standalone_question, 'standalone_question')
            except ValueError as error:
                raise InvalidArgumentError(str(error)) from None
            effective_question = standalone_question if standalone_question is not None else question
            pages = tuple(focus.pdf_pages) if focus is not None else ()
            sections = tuple(focus.section_ids) if focus is not None else ()
            response = await anyio.to_thread.run_sync(lambda: services.answer.answer(
                effective_question, context_id=context_id, top_k=top_k,
                pdf_pages=pages, section_ids=sections))
            return _answer_payload(response)
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(name='get_evidence', structured_output=True,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False))
    async def get_evidence(context_id: str, evidence_ids: list[str]) -> GetEvidenceOutput:
        """답변에 저장된 근거의 한국어 번역·원문·페이지·파일명을 재검색 없이 조회합니다."""
        try:
            result = await anyio.to_thread.run_sync(
                lambda: services.evidence.get(context_id, evidence_ids))
            return serialize_stored_evidence_result(result)
        except Exception as error:
            raise to_tool_error(error) from None

    return server
