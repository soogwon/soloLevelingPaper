"""MCP 도구 사이의 식별자 전달과 학습 맥락 흐름을 검증한다."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.evidence_qa import (
    AnswerResult,
    AnswerStatus,
    Citation,
    Claim,
    EvidenceDetail,
    GetEvidenceResult,
    RetrievalMethod,
    RetrievedChunk,
    SearchResult,
    SearchScope,
)
from solo_leveling.domain.models import Chunk, JobStatus, LearningContext
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def run(awaitable):
    return asyncio.run(awaitable)


def payload(result):
    return result[1]


class WorkflowIngestion:
    def add_local_pdf(self, relative_path: str, request_key: str) -> dict:
        assert relative_path == 'paper.pdf'
        assert request_key == 'request-1'
        return {
            'paper_id': 'paper-1',
            'version_id': 'version-1',
            'job_id': 'job-1',
            'status': 'processing',
        }

    def get_status(self, job_id: str) -> dict:
        assert job_id == 'job-1'
        return {
            'job_id': job_id,
            'version_id': 'version-1',
            'status': 'ready',
            'stage': 'index',
            'limitations': [],
            'result_available': True,
        }


class WorkflowContexts:
    def __init__(self):
        self.contexts: dict[tuple[str, str, tuple[str, ...]], LearningContext] = {}

    def get_or_create_learning_context(
        self, version_id: str, goal: str, known_concepts: list[str],
    ) -> LearningContext:
        key = (version_id, goal, tuple(known_concepts))
        if key not in self.contexts:
            self.contexts[key] = LearningContext(
                'context-1', version_id, 'parse-1', 'translation-1',
                goal=goal, known_concepts=list(known_concepts),
                embedding_set_id='embedding-1',
            )
        return self.contexts[key]


class WorkflowAnswer:
    def __init__(self):
        self.context_id: str | None = None

    def answer(self, question: str, *, context_id: str, top_k: int,
               pdf_pages: tuple[int, ...], section_ids: tuple[str, ...]):
        assert question == '구현할 때 핵심 구성 요소는 무엇인가요?'
        assert top_k == 5
        assert pdf_pages == section_ids == ()
        self.context_id = context_id
        chunk = Chunk(
            'chunk-1', 'parse-1', 0, 'Original evidence.', '한국어 근거.',
            'translation-1', pdf_page=2,
        )
        search = SearchResult(
            question,
            SearchScope('version-1', 'parse-1', 'translation-1'),
            RetrievalMethod.VECTOR,
            'embedding-1',
            (RetrievedChunk(chunk, 1.0, 1),),
        )
        citation = Citation(
            'evidence-1', 'chunk-1', 'version-1', 'parse-1', 'translation-1',
            None, 2, '한국어 근거.', 'Original evidence.',
        )
        result = AnswerResult(
            AnswerStatus.OK,
            '핵심 구성 요소에 대한 답변입니다.',
            (Claim('claim-1', '핵심 구성 요소입니다.', ('evidence-1',)),),
            (citation,),
            None,
        )
        return SimpleNamespace(context_id=context_id, search=search, result=result)


class WorkflowEvidence:
    def __init__(self):
        self.request: tuple[str, list[str]] | None = None

    def get(self, context_id: str, evidence_ids: list[str]) -> GetEvidenceResult:
        self.request = (context_id, evidence_ids)
        return GetEvidenceResult((EvidenceDetail(
            'evidence-1', 'chunk-1', 'version-1', 'parse-1', 'translation-1',
            None, 2, '한국어 근거.', 'Original evidence.', 'paper.pdf',
        ),))


def test_paper_registration_to_evidence_lookup_flow():
    answer = WorkflowAnswer()
    evidence = WorkflowEvidence()
    server = create_server(MCPServices(
        answer, evidence, WorkflowIngestion(), WorkflowContexts(),
    ))

    added = payload(run(server.call_tool('add_paper', {
        'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'},
        'request_key': 'request-1',
    })))
    status = payload(run(server.call_tool(
        'get_paper_status', {'job_id': added['job_id']},
    )))
    context = payload(run(server.call_tool('start_learning', {
        'version_id': status['version_id'],
        'goal': 'implement',
        'known_concepts': ['Transformer', 'PyTorch'],
    })))
    response = payload(run(server.call_tool('ask_paper', {
        'context_id': context['context_id'],
        'question': '구현할 때 핵심 구성 요소는 무엇인가요?',
    })))
    details = payload(run(server.call_tool('get_evidence', {
        'context_id': response['context_id'],
        'evidence_ids': [response['citations'][0]['evidence_id']],
    })))

    assert status['capabilities'] == ['start_learning']
    assert context['goal'] == 'implement'
    assert context['known_concepts'] == ['Transformer', 'PyTorch']
    assert answer.context_id == context['context_id']
    assert evidence.request == (context['context_id'], ['evidence-1'])
    assert details['evidence'][0]['file_display_name'] == 'paper.pdf'


def test_processing_paper_cannot_start_learning():
    class ProcessingContexts:
        def get_or_create_learning_context(self, version_id, goal, known_concepts):
            raise ContextNotReadyError(version_id, 'job-1', JobStatus.PROCESSING)

    server = create_server(MCPServices(
        WorkflowAnswer(), WorkflowEvidence(), contexts=ProcessingContexts(),
    ))

    with pytest.raises(ToolError) as caught:
        run(server.call_tool('start_learning', {
            'version_id': 'version-1', 'goal': 'understand',
        }))

    message = str(caught.value)
    assert 'PAPER_NOT_READY' in message
    assert 'job_id=job-1' in message
    assert 'status=processing' in message
