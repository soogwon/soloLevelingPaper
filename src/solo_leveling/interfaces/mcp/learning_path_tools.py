"""검수 KG의 학습 경로·선수 개념 조회 도구를 등록한다."""

from typing import Annotated, Literal, Protocol
from typing_extensions import TypedDict

import anyio
from mcp.types import ToolAnnotations
from pydantic import Field

from .errors import to_tool_error

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


class LearningPathReader(Protocol):
    def get_learning_path(self, context_id: str, known_concepts: list[str] | None,
                          mode: str) -> dict: ...

    def get_prerequisites(self, context_id: str, concept: str) -> dict: ...


class PaperLocationOutput(TypedDict):
    pdf_page: int
    section_hint: str | None
    search_phrase: str | None


class NeededForOutput(TypedDict):
    concept_id: str
    name_ko: str


class PathStepOutput(TypedDict):
    order: int
    concept_id: str
    name_ko: str
    name_en: str
    description_ko: str
    role: Literal['target', 'required', 'optional']
    reason_ko: str
    needed_for: NeededForOutput | None
    prerequisite_ids: list[str]
    paper_locations: list[PaperLocationOutput]


class SkippedOutput(TypedDict):
    concept_id: str
    name_ko: str
    reason: Literal['known', 'only_needed_by_known']


class ReviewedVersionOutput(TypedDict):
    label: str
    source_url: str | None


class LearningPathOutput(TypedDict):
    context_id: str
    graph_status: Literal['ready', 'related_version', 'no_graph']
    graph_revision_id: str | None
    reviewed_version: ReviewedVersionOutput | None
    goal: Literal['understand', 'implement', 'skim']
    mode: Literal['compact', 'expanded']
    steps: list[PathStepOutput]
    skipped: list[SkippedOutput]
    unresolved_concepts: list[str]


class ConceptOutput(TypedDict):
    concept_id: str
    name_ko: str
    name_en: str
    description_ko: str


class PrerequisiteOutput(TypedDict):
    concept_id: str
    name_ko: str
    rationale_ko: str
    source_reference: str | None


class RequiredByOutput(TypedDict):
    concept_id: str
    name_ko: str


class PrerequisitesOutput(TypedDict):
    context_id: str
    graph_status: Literal['ready', 'related_version', 'no_graph']
    graph_revision_id: str | None
    reviewed_version: ReviewedVersionOutput | None
    concept: ConceptOutput | None
    prerequisites: list[PrerequisiteOutput]
    required_by: list[RequiredByOutput]
    paper_locations: list[PaperLocationOutput]


def register_learning_path(server, reader: LearningPathReader):
    @server.tool(name='get_learning_path', structured_output=True, annotations=READ_ONLY)
    async def get_learning_path(
        context_id: Annotated[str, Field(strict=True, min_length=1)],
        known_concepts: list[Annotated[str, Field(strict=True)]] | None = None,
        mode: Literal['compact', 'expanded'] = 'compact',
    ) -> LearningPathOutput:
        """학습 맥락의 논문·목적에 맞춘 검수 개념 학습 순서를 조회합니다.

        steps는 선수 개념부터 목표 개념까지의 순서입니다. role은 target(목표), required(필수 선수),
        optional(선택)입니다. compact는 target·required만, expanded는 선택 개념까지 포함합니다.
        known_concepts를 생략하면 start_learning에 저장한 값을 쓰고, 지정하면 이번 조회에만 씁니다.
        아는 개념과 그 개념에만 필요했던 선수는 skipped로 돌려주며 이해를 검증한 결과가 아닙니다.
        그래프와 맞지 않는 이름은 unresolved_concepts로 돌려주고 경로에 반영하지 않습니다.
        graph_status=no_graph이면 이 논문에 검수 그래프가 없습니다. 경로를 추측하지 말고 ask_paper로 안내하세요.
        graph_status=related_version이면 사용자의 PDF는 검수한 판본과 다른 판본(arXiv 번호는 같음)입니다.
        개념 순서는 그대로 쓸 수 있지만 페이지가 다를 수 있어 paper_locations는 비어 있습니다.
        reviewed_version의 판본으로 받으면 페이지 근거까지 볼 수 있다고 안내하세요.
        선수 관계는 팀이 검수한 교육적 판단이며 논문 본문의 주장이 아닙니다.
        paper_locations의 pdf_page는 'PDF 기준 N페이지'로 표시하세요. search_phrase는 사용자가 자기 PDF에서
        검색해 찾을 수 있는 논문 속 용어·소제목이니 그대로 안내하세요.
        """
        try:
            return await anyio.to_thread.run_sync(
                lambda: reader.get_learning_path(context_id, known_concepts, mode))
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(name='get_prerequisites', structured_output=True, annotations=READ_ONLY)
    async def get_prerequisites(
        context_id: Annotated[str, Field(strict=True, min_length=1)],
        concept: Annotated[str, Field(strict=True, min_length=1)],
    ) -> PrerequisitesOutput:
        """개념 하나의 직접 선수 개념과 그 이유, 이 개념을 필요로 하는 개념을 조회합니다.

        concept은 개념 ID, 한글·영문 이름 또는 별칭이며 정확히 일치해야 합니다(부분 검색 없음).
        더 깊은 선수는 반환된 concept_id로 다시 호출하세요. 그래프에 없는 개념은 NOT_FOUND입니다.
        graph_status=no_graph이면 이 논문에 검수 그래프가 없습니다.
        graph_status=related_version이면 검수 판본과 다른 판본이라 paper_locations를 비워 둡니다.
        """
        try:
            return await anyio.to_thread.run_sync(
                lambda: reader.get_prerequisites(context_id, concept))
        except Exception as error:
            raise to_tool_error(error) from None
