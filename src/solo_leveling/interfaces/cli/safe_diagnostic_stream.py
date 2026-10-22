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
vector_search worker_request_received worker_result_send'''.split())


class SafeDiagnosticStream:
    encoding = 'utf-8'

    def __init__(self, output):
        self.output = output
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
                    if not self.dropping:
                        self._record(self.pending)
                    self.pending = ''
                    self.dropping = False
        return len(text)

    def _record(self, line):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            return
        fields = {'request_id', 'span_id', 'stage', 'event', 'elapsed_ms', 'error_code'}
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

    def flush(self):
        with self.lock:
            self.output.flush()
