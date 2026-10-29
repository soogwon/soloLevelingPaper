"""허용된 단계 로그만 보존하며 임의 stderr 문자열은 저장하지 않는다."""

import json
import math
import re
from threading import RLock


STAGES = frozenset('''answer ask_paper context_lookup continuation_lookup
default_context_lookup diagnostic_probe embedding_model_load index_lookup
startup_embedding_import chroma_validation embedding_encode embedding_library_import
embedding_memory_cache_hit embedding_model_construct embedding_process_response
embedding_process_wait embedding_worker evidence_quality evidence_save
evidence_validation generation_api_call generation_repair generation query_embedding
vector_search worker_request_received worker_result_send
semantic_verification semantic_api_call'''.split())

EXTRACTION_REASONS = frozenset('''NO_EXTRACTION_RISK STANDALONE_NUMBER EMPTY_BODY
SUPPORT_NOT_FOUND SUPPORT_LOCATION_UNRESOLVED UNFINISHED_TAIL UNRESOLVED_PREFIX'''.split())
REPAIR_OUTCOMES = frozenset(('REPAIR_ADOPTED', 'REPAIR_PARTIAL', 'REPAIR_NO_USABLE_CLAIMS',
                           'REPAIR_UNAVAILABLE', 'REPAIR_INVALID'))
VERIFICATION_FAILURES = frozenset('''GENERATION_FORMAT_INVALID GENERATION_JSON_INVALID
GENERATION_SCHEMA_INVALID DRAFT_INVALID EMPTY_EVIDENCE_IDS DUPLICATE_EVIDENCE_ID
UNKNOWN_EVIDENCE_ID SUPPORT_REFERENCE_INVALID'''.split())


class SafeDiagnosticStream:
    encoding = 'utf-8'

    def __init__(self, output, *, detailed=False):
        self.output = output
        self.detailed = detailed
        self.pending = ''
        self.dropping = False
        self.lock = RLock()

    def fileno(self):
        # 네이티브 출력은 필터를 거칠 수 없으므로 폐기된 stderr 설명자를 사용한다.
        return 2

    def isatty(self):
        return False

    def write(self, text):
        with self.lock:
            for part in text.splitlines(keepends=True):
                if len(self.pending) + len(part) > 4096:
                    self.pending = ''
                    self.dropping = True
                if not self.dropping:
                    self.pending += part
                if part.endswith(('\n', '\r')):
                    try:
                        if not self.dropping:
                            self._record(self.pending)
                    finally:
                        # 출력 실패가 다음 로그와 합쳐져 추가 누락을 만들지 않게 한다.
                        self.pending = ''
                        self.dropping = False
        return len(text)

    def _record(self, line):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            return
        if isinstance(record, dict) and record.get('event') == 'chroma_close':
            # 수집 소유자의 종료 실패만 보존하며 원본 예외·추가 필드는 거부한다.
            if (set(record) == {'event', 'code'} and isinstance(record['code'], str)
                    and record['code'] in ('CHROMA_CLOSE_FAILED', 'CHROMA_CLOSE_UNSUPPORTED')):
                self.output.write(json.dumps(record) + '\n')
            return
        if isinstance(record, dict) and record.get('event') == 'semantic_startup':
            # 시작 진단만 별도 고정 계약을 허용하며 임의 설정값은 보존하지 않는다.
            if (set(record) == {'event', 'enabled', 'connected', 'prompt_version'}
                    and type(record['enabled']) is bool
                    and type(record['connected']) is bool
                    and record['prompt_version'] in (None, 'semantic-v1', 'semantic-v2', 'semantic-v3', 'semantic-v4', 'semantic-v5', 'semantic-v6', 'semantic-v7')):
                self.output.write(json.dumps(record) + '\n')
            return
        fields = {'request_id', 'span_id', 'stage', 'event', 'elapsed_ms', 'error_code'}
        if isinstance(record, dict) and record.get('stage') == 'semantic_diagnostic':
            if record.get('event') == 'assessment':
                from solo_leveling.semantic_diagnostics import valid_assessment, basic_assessment
                details = {key: value for key, value in record.items() if key not in fields}
                if (set(record) == fields | set(details) and fields <= set(record)
                        and valid_assessment(details)
                        and (self.detailed or basic_assessment(record['check_item'], record['status']))
                        and all(isinstance(record[key], str) and re.fullmatch('[0-9a-f]{32}', record[key])
                                for key in ('request_id', 'span_id'))
                        and type(record['elapsed_ms']) is int and record['elapsed_ms'] == 0
                        and record['error_code'] is None):
                    self.output.write(json.dumps(record) + '\n')
                return
            self._semantic_record(record, fields)
            return
        if isinstance(record, dict) and record.get('stage') == 'extraction_diagnostic':
            self._extraction_record(record, fields)
            return
        if not isinstance(record, dict) or set(record) != fields:
            return
        if not all(isinstance(record[key], str) and re.fullmatch('[0-9a-f]{32}', record[key])
                   for key in ('request_id', 'span_id')):
            return
        if not isinstance(record['stage'], str) or record['stage'] not in STAGES:
            return
        if record['event'] not in ('start', 'end', 'error'):
            return
        if record['error_code'] not in (None, 'STAGE_FAILED', 'CANCELLED'):
            return
        elapsed = record['elapsed_ms']
        if type(elapsed) not in (int, float) or not 0 <= elapsed <= 1e15:
            return
        if not math.isfinite(elapsed):
            return
        self.output.write(json.dumps(record) + '\n')

    def _extraction_record(self, record, fields):
        """본문이나 근거 식별자가 없는 고정 판정 이벤트만 기본 로그에 보존한다."""
        if 'detail_code' in record:
            if (record.get('event') != 'quality' or record.get('reason_code') != 'UNFINISHED_TAIL'
                    or record['detail_code'] not in ('NEXT_CANDIDATE_ABSENT', 'NEXT_NOT_CITED',
                        'LEFT_TAIL_START_MISSING', 'LEFT_TAIL_END_MISSING', 'NEXT_NOT_CONTINUATION',
                        'NEXT_SENTENCE_END_ABSENT', 'RIGHT_PREFIX_START_MISSING', 'RIGHT_PREFIX_END_MISSING')):
                return
            fields = fields | {'detail_code'}
        if set(record) != fields | {'attempt_id', 'claim_number', 'round_number', 'reason_code'}:
            return
        if not all(isinstance(record[key], str) and re.fullmatch('[0-9a-f]{32}', record[key])
                   for key in ('request_id', 'span_id', 'attempt_id')):
            return
        if type(record['elapsed_ms']) is not int or record['elapsed_ms'] != 0 or record['error_code'] is not None:
            return
        if type(record['round_number']) is not int or record['round_number'] not in (0, 1):
            return
        event, reason, number = record['event'], record['reason_code'], record['claim_number']
        if not isinstance(reason, str):
            return
        if event == 'repair_outcome':
            if number is not None or reason not in REPAIR_OUTCOMES:
                return
        elif event == 'verification_failure':
            # 초안 전체의 형식 오류는 주장 번호를 특정하지 못할 수 있다.
            if reason not in VERIFICATION_FAILURES:
                return
            if number is not None and (type(number) is not int or not 1 <= number <= 10000):
                return
        elif event in ('quality', 'claim_excluded'):
            if type(number) is not int or not 1 <= number <= 10000 or reason not in EXTRACTION_REASONS:
                return
        else:
            return
        self.output.write(json.dumps(record) + '\n')

    def _semantic_record(self, record, fields):
        """고정 코드와 요청 식별자만 보존하며 추가 필드가 있으면 폐기한다."""
        from solo_leveling.domain.semantic_verification import (
            VerificationDetail, AUDIT_CHECK_LOCATIONS, CHECK_DETAIL_CODES, COVERAGE_REJECTION_REASONS)
        if 'rejection_reason' in record:
            if (record.get('reason_code') != 'COVERAGE_REFERENCE_INVALID'
                    or not isinstance(record['rejection_reason'], str)
                    or record['rejection_reason'] not in COVERAGE_REJECTION_REASONS):
                return
            fields = fields | {'rejection_reason'}
        if 'check_item' in record:
            if (record.get('reason_code') not in {code.value for code in CHECK_DETAIL_CODES}
                    or not isinstance(record['check_item'], str)
                    or record['check_item'] not in AUDIT_CHECK_LOCATIONS):
                return
            fields = fields | {'check_item'}
        if set(record) != fields | {'reason_code'}:
            return
        if not all(isinstance(record[key], str) and re.fullmatch('[0-9a-f]{32}', record[key])
                   for key in ('request_id', 'span_id')):
            return
        if (record['event'] != 'detail' or type(record['elapsed_ms']) is not int
                or record['elapsed_ms'] != 0 or record['error_code'] is not None
                or not isinstance(record['reason_code'], str)
                or record['reason_code'] not in {code.value for code in VerificationDetail}):
            return
        self.output.write(json.dumps(record) + '\n')

    def flush(self):
        with self.lock:
            self.output.flush()
