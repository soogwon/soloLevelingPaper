"""이름·종료 JSON의 공통 필드와 기간만 판정한다. 실제 종료·삭제는 판단하지 않는다."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re

from .contracts import BundleId, ClosedFile, Kind


MAX_METADATA_BYTES = 16 * 1024
_LOG = re.compile(r'slp-log-v1-(mcp|worker)-(safe|detail|raw|stack)-([0-9a-f]{32})\.log')
_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z')


def log_name(bundle: BundleId, kind: Kind) -> str:
    if kind not in ('safe', 'detail', 'raw', 'stack') or (bundle.owner == 'worker' and kind == 'detail'):
        raise ValueError('UNSUPPORTED_LOG_KIND')
    return f'slp-log-v1-{bundle.owner}-{kind}-{bundle.run_id}.log'


def parse_log_name(name: str) -> tuple[BundleId, Kind] | None:
    match = _LOG.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        return None
    owner, kind, run_id = match.groups()
    if owner == 'worker' and kind == 'detail':
        return None
    return BundleId(owner, run_id), kind


def closed_name(bundle: BundleId) -> str:
    return f'slp-log-v1-{bundle.owner}-{bundle.run_id}.closed.json'


@dataclass(frozen=True)
class MetadataSummary:
    """공통 필드의 구문 검사 결과이며 파일 동일성·종료 증명이 아니다."""
    bundle: BundleId
    closed_at: datetime
    files: tuple[ClosedFile, ...]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('DUPLICATE_FIELD')
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError('INVALID_JSON_CONSTANT')


def parse_metadata_summary(payload: bytes, *, expected: BundleId) -> MetadataSummary:
    """호출자가 제공한 바이트의 공통 필드만 검사한다.

    identity는 존재만 확인하며 내용은 검증하거나 요약에 보관하지 않는다.
    null을 포함한 어떤 값도 동일성 확인을 뜻하지 않는다. OS 검증기는 잠금 아래
    원본을 다시 읽어 검증해야 하며, 이 요약을 종료 증거로 사용할 수 없다.
    """
    try:
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_METADATA_BYTES:
            raise ValueError()
        row = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object,
                         parse_constant=_reject_constant)
        if not isinstance(row, dict) or set(row) != {'schema_version', 'owner', 'run_id', 'closed_at', 'files'}:
            raise ValueError()
        if type(row['schema_version']) is not int or row['schema_version'] != 1:
            raise ValueError()
        bundle = BundleId(row['owner'], row['run_id'])
        if bundle != expected or not isinstance(row['closed_at'], str) or not _UTC.fullmatch(row['closed_at']):
            raise ValueError()
        closed_at = datetime.fromisoformat(row['closed_at'].replace('Z', '+00:00'))
        if not isinstance(row['files'], list) or not 1 <= len(row['files']) <= 4:
            raise ValueError()
        files = []
        kinds = set()
        for item in row['files']:
            if not isinstance(item, dict) or set(item) != {'kind', 'name', 'size_bytes', 'identity'}:
                raise ValueError()
            kind = item['kind']
            if (item['name'] != log_name(bundle, kind) or kind in kinds
                    or type(item['size_bytes']) is not int or not 0 <= item['size_bytes'] <= 2**63 - 1):
                raise ValueError()
            kinds.add(kind)
            files.append(ClosedFile(kind, item['name'], item['size_bytes']))
        return MetadataSummary(bundle, closed_at, tuple(files))
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError('INVALID_LOG_METADATA') from None


@dataclass(frozen=True)
class RetentionPolicy:
    safe_days: int = 30
    detail_days: int = 7
    raw_days: int = 1

    def __post_init__(self):
        if any(type(days) is not int or not 1 <= days <= 36500
               for days in (self.safe_days, self.detail_days, self.raw_days)):
            raise ValueError('INVALID_RETENTION_POLICY')


def retention_status(summary: MetadataSummary, *, now: datetime,
                     policy: RetentionPolicy = RetentionPolicy(), include_raw: bool = False) -> str:
    """기간만 판정한다. expired도 삭제 허가나 사용 종료 증거는 아니다."""
    if (not isinstance(now, datetime) or now.utcoffset() != timedelta(0)
            or type(include_raw) is not bool):
        raise ValueError('INVALID_RETENTION_INPUT')
    age = now - summary.closed_at
    if age < timedelta(0):
        return 'future_timestamp'
    if not summary.files:
        return 'unknown'
    if not include_raw and any(item.kind in ('raw', 'stack') for item in summary.files):
        return 'raw_excluded'
    days = {'safe': policy.safe_days, 'detail': policy.detail_days,
            'raw': policy.raw_days, 'stack': policy.raw_days}
    return 'expired' if all(age >= timedelta(days=days[item.kind]) for item in summary.files) else 'retained'
