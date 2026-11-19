"""Translate a single parse revision in memory while preserving input chunks."""

from dataclasses import replace
import json
import random
import sys
import time
from typing import Callable, Sequence
from uuid import uuid4

from solo_leveling.domain.models import Chunk, TranslationRevision
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationFailureCode,
    TranslationRequest, TranslationSettings, TranslationStatus, require_text, translation_quality_failure,
)
from .classification import classify_translation_source
from .ports import TranslationProvider, TranslationProviderUnavailable
from .retry import RetryBudget, TranslationRetryPolicy


class TranslationService:
    def __init__(
        self, provider: TranslationProvider,
        revision_id_factory: Callable[[], str] | None = None,
        *, retry_policy: TranslationRetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
        quality_retry_once: bool = False,
    ) -> None:
        self.provider = provider
        self.revision_id_factory = revision_id_factory or (lambda: str(uuid4()))
        if retry_policy is not None and not isinstance(retry_policy, TranslationRetryPolicy):
            raise ValueError('올바른 번역 재시도 정책이 필요합니다.')
        self.retry_policy = retry_policy if retry_policy is not None else TranslationRetryPolicy()
        self.sleep, self.clock, self.random_value = sleep, clock, random_value
        if type(quality_retry_once) is not bool or quality_retry_once and self.retry_policy.max_attempts != 1:
            raise ValueError('quality recovery requires a single provider attempt per pass')
        self.quality_retry_once = quality_retry_once

    def with_revision_id(self, revision_id: str) -> 'TranslationService':
        """기존 리비전 재사용 시 재시도 설정과 테스트용 의존성을 보존한다."""
        require_text(revision_id, 'translation_revision_id')
        return TranslationService(self.provider, lambda: revision_id,
            retry_policy=self.retry_policy, sleep=self.sleep, clock=self.clock,
            random_value=self.random_value, quality_retry_once=self.quality_retry_once)

    def _translate_one(self, request: TranslationRequest, settings: TranslationSettings,
                       budget: RetryBudget, batch_id: str, chunk_number: int) -> str:
        policy = self.retry_policy
        started = self.clock()
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self.provider.translate(request, settings)
            except TranslationProviderUnavailable as error:
                delay = 0.0
                retry = (error.retryable and attempt < policy.max_attempts
                         and budget.retries < policy.max_batch_retries)
                if retry:
                    delay = policy.base_delay_seconds * 2 ** (attempt - 1)
                    delay += policy.jitter_seconds * min(1.0, max(0.0, self.random_value()))
                    delay = max(delay, error.retry_after_seconds or 0.0)
                    now = self.clock()
                    retry = (delay <= policy.max_delay_seconds
                             and budget.waited + delay <= policy.max_batch_wait_seconds
                             and now - started + delay < policy.chunk_retry_window_seconds
                             and now - budget.started + delay < policy.batch_retry_window_seconds)
                # 사용자 입력과 외부 예외 문자열 없이 내부 식별자·고정 코드만 남긴다.
                try:
                    print(json.dumps({'event': 'translation_failure', 'batch_id': batch_id,
                        'chunk_number': chunk_number, 'attempt': attempt,
                        'code': error.code.value, 'retry': retry,
                        'delay_seconds': delay if retry else 0.0}), file=sys.stderr, flush=True)
                except (OSError, ValueError):
                    pass
                if not retry:
                    raise
                before = self.clock()
                self.sleep(delay)
                budget.waited += max(delay, self.clock() - before)
                # 스케줄링으로 대기가 길어진 경우에도 기한을 넘겨 새 호출하지 않는다.
                now = self.clock()
                if (now - started >= policy.chunk_retry_window_seconds
                        or now - budget.started >= policy.batch_retry_window_seconds
                        or budget.waited > policy.max_batch_wait_seconds):
                    raise
                budget.retries += 1

    def translate(
        self, chunks: Sequence[Chunk], settings: TranslationSettings,
        *, on_progress: Callable[[int, int], None] | None = None, reusable=None,
    ) -> TranslationBatchResult:
        if not isinstance(settings, TranslationSettings):
            raise ValueError("settings must be TranslationSettings")
        if on_progress is not None and not callable(on_progress):
            raise ValueError("on_progress must be callable")
        inputs = tuple(chunks)
        if not inputs or any(not isinstance(c, Chunk) for c in inputs):
            raise ValueError("a nonempty sequence of Chunk is required")
        snapshots = tuple(replace(c) for c in inputs)
        # Validate the entire batch before the first provider call.
        requests = tuple(TranslationRequest(
            c.chunk_id, c.parse_revision_id, c.original_text, settings.target_language,
        ) for c in snapshots)
        reusable = {} if reusable is None else dict(reusable)
        requests_by_id = {request.chunk_id: request for request in requests}
        if any(not isinstance(item, ChunkTranslationResult) or not item.indexable
               or item.request != requests_by_id.get(chunk_id) for chunk_id, item in reusable.items()):
            raise ValueError("translation_checkpoint_invalid")
        parse_id = requests[0].parse_revision_id
        if any(r.parse_revision_id != parse_id for r in requests):
            raise ValueError("all chunks must belong to one parse revision")
        if len({r.chunk_id for r in requests}) != len(requests):
            raise ValueError("duplicate chunk ID")
        revision_id = self.revision_id_factory()
        require_text(revision_id, "translation_revision_id")
        if any(c.translation_revision_id == revision_id for c in snapshots):
            raise ValueError("retranslation requires a new revision ID")
        revision = TranslationRevision(revision_id, parse_id, settings.provider, settings.model)
        results = []
        budget = RetryBudget(self.clock())
        batch_id = str(uuid4())
        for chunk_number, (chunk, request) in enumerate(zip(snapshots, requests)):
            source_status = classify_translation_source(
                request.original_text, section_id=chunk.section_id)
            if request.chunk_id in reusable:
                result = reusable[request.chunk_id]
            elif source_status is not None:
                result = ChunkTranslationResult(request, request.original_text, status=source_status)
            else:
                chunk_started = self.clock()
                try:
                    text = self._translate_one(request, settings, budget, batch_id, chunk_number)
                except TranslationProviderUnavailable:
                    result = ChunkTranslationResult(
                        request, None, TranslationFailureCode.PROVIDER_UNAVAILABLE)
                else:
                    if not isinstance(text, str):
                        raise TypeError("translation provider must return str")
                    failure = (translation_quality_failure(request.original_text, text)
                               if text.strip() else TranslationFailureCode.EMPTY_TRANSLATION)
                    now = self.clock()
                    expired = (now - chunk_started >= self.retry_policy.chunk_retry_window_seconds
                               or now - budget.started >= self.retry_policy.batch_retry_window_seconds)
                    if failure and self.quality_retry_once and expired:
                        result = ChunkTranslationResult(request,request.original_text,failure,TranslationStatus.ORIGINAL_ONLY)
                    elif failure and self.quality_retry_once:
                        # 원문을 추측해서 재배열하지 않는다. 안전한 진단만 기록하고 지침을 강화한다.
                        diagnostic = {'event':'translation_quality_recovery','chunk_number':chunk_number,
                            'attempt':2,'code':failure.value,
                            'line_breaks':request.original_text.count('\n'),
                            'hyphenated_line_break':'-\n' in request.original_text}
                        try:
                            print(json.dumps(diagnostic),file=sys.stderr,flush=True)
                        except (OSError,ValueError):
                            pass
                        try:
                            text = self._translate_one(replace(request,recovery_policy='layout-aware-v1'),
                                                       settings,budget,batch_id,chunk_number)
                        except TranslationProviderUnavailable:
                            result = ChunkTranslationResult(request,None,TranslationFailureCode.PROVIDER_UNAVAILABLE)
                        else:
                            if not isinstance(text,str):
                                raise TypeError('translation provider must return str')
                            failure = (translation_quality_failure(request.original_text,text) if text.strip()
                                       else TranslationFailureCode.EMPTY_TRANSLATION)
                            result = (ChunkTranslationResult(request,request.original_text,failure,TranslationStatus.ORIGINAL_ONLY)
                                      if failure else ChunkTranslationResult(request,text))
                    else:
                        result = ChunkTranslationResult(request, None if failure else text, failure)
            results.append(result)
            if on_progress is not None:
                on_progress(chunk_number + 1, len(requests))
        return TranslationBatchResult(revision, settings, tuple(results))
