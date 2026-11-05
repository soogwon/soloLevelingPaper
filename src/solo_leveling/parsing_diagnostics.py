"""PDF 회전 필터의 수치만 기록한다. 본문·파일 경로·PDF 메타데이터는 받지 않는다."""

from dataclasses import dataclass
import json
import re
import sys


@dataclass(frozen=True)
class RotationMetrics:
    total_chars: int
    excluded_chars: int

    def __post_init__(self):
        if (type(self.total_chars) is not int or type(self.excluded_chars) is not int
                or not 0 <= self.excluded_chars <= self.total_chars <= 2**63 - 1):
            raise ValueError('invalid rotation counts')

    @property
    def excluded_ratio(self) -> float:
        return round(self.excluded_chars / self.total_chars, 6) if self.total_chars else 0.0


def valid_rotation_record(record):
    if not isinstance(record, dict) or set(record) != {
            'event', 'parse_id', 'pdf_page', 'total_chars', 'excluded_chars', 'excluded_ratio'}:
        return False
    if (record['event'] != 'pdf_rotation' or not isinstance(record['parse_id'], str)
            or re.fullmatch(r'[0-9a-f]{32}', record['parse_id']) is None
            or type(record['pdf_page']) is not int or not 1 <= record['pdf_page'] <= 2**31 - 1
            or type(record['excluded_ratio']) not in (int, float)):
        return False
    try:
        metrics = RotationMetrics(record['total_chars'], record['excluded_chars'])
    except ValueError:
        return False
    return record['excluded_ratio'] == metrics.excluded_ratio


def emit_rotation_metrics(parse_id, pdf_page, metrics):
    record = dict(event='pdf_rotation', parse_id=parse_id, pdf_page=pdf_page,
                  total_chars=metrics.total_chars, excluded_chars=metrics.excluded_chars,
                  excluded_ratio=metrics.excluded_ratio)
    if not valid_rotation_record(record):
        return
    try:
        sys.stderr.write(json.dumps(record) + '\n')
        sys.stderr.flush()
    except Exception:
        # 진단 실패로 추출·등록을 실패시키거나 원문을 다른 경로에 출력하지 않는다.
        pass
