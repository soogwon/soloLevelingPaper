"""로그 본문을 읽거나 파일을 변경하지 않고 보관 검토 목록을 만든다."""

from datetime import datetime, timezone
import argparse
import json
import math
from pathlib import Path
import re
import stat
import time

from solo_leveling.infrastructure.log_retention.policy import RetentionPolicy, parse_log_name
from solo_leveling.infrastructure.log_retention.preview import inspect_mcp_bundle


_HEX = r'[0-9a-f]{32}'
_NAMES = re.compile(
    rf'(mcp-\d{{8}}T\d{{6}}Z-{_HEX}|worker-{_HEX}|'
    rf'worker-stack-{_HEX}|import-stack-{_HEX}-{_HEX})\.log')


def _linked(info):
    # Windows junction 등 재분석 지점도 심볼릭 링크와 함께 제외한다.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400)


def inventory(directory: Path, *, review_days=30, max_bytes=104857600,
              max_files=100, now=None, retention_policy=None):
    """목록과 경고를 반환하고 선택한 경우 MCP 묶음의 보관 상태도 검사한다."""
    if (type(review_days) not in (int, float) or not math.isfinite(review_days)
            or review_days <= 0 or type(max_bytes) is not int or max_bytes <= 0
            or type(max_files) is not int or max_files <= 0):
        raise ValueError('검토 기준은 유한한 양수여야 합니다.')
    root = directory.absolute()
    # 상위 디렉터리를 포함하여 링크를 거치는 경로는 받지 않는다.
    for part in (*reversed(root.parents), root):
        if _linked(part.lstat()):
            raise ValueError('링크 경로는 검사할 수 없습니다.')
    if not root.is_dir():
        raise ValueError('로그 디렉터리가 필요합니다.')
    timestamp = time.time() if now is None else now
    rows = []
    bundles = {}
    skipped = 0
    unreadable = 0
    for path in root.iterdir():
        parsed = parse_log_name(path.name)
        bundle_files = None
        if retention_policy is not None and parsed and parsed[0].owner == 'mcp':
            bundle_files = bundles.setdefault(parsed[0], {})
            bundle_files[path.name] = None
        if parsed is None and not _NAMES.fullmatch(path.name):
            skipped += 1
            continue
        try:
            info = path.lstat()
            if (_linked(info) or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1):
                skipped += 1
                continue
            if bundle_files is not None:
                bundle_files[path.name] = info
            age = max(0, timestamp - info.st_mtime)
            rows.append({
                'name': path.name, 'bytes': info.st_size,
                'modified_at_unix': info.st_mtime,
                'kind': parsed[1] if parsed is not None else ('raw_stack' if path.name.startswith(
                    ('worker-stack-', 'import-stack-')) else 'unknown'),
                'activity': 'unknown',
                'age_review': age >= review_days * 86400,
            })
        except OSError:
            # 파일명이나 원본 예외를 오류 응답으로 노출하지 않는다.
            unreadable += 1
    if retention_policy is not None:
        instant = datetime.fromtimestamp(timestamp, timezone.utc)
        statuses = {}
        for bundle, observed in bundles.items():
            status = inspect_mcp_bundle(root, bundle, observed, instant, retention_policy)
            statuses[bundle] = status
            if status == 'io_error':
                unreadable += 1
        for row in rows:
            parsed = parse_log_name(row['name'])
            status = statuses.get(parsed[0], 'worker_excluded') if parsed else 'legacy_unknown'
            row['retention_status'] = status
            if status in ('expired', 'retained', 'raw_excluded', 'future_timestamp'):
                row['activity'] = 'closed'
            elif status == 'lock_busy':
                row['activity'] = 'busy'
    rows.sort(key=lambda row: (row['modified_at_unix'], row['name']))
    total = sum(row['bytes'] for row in rows)
    result = {'mode': 'preview_only', 'policy': {'review_days': review_days,
            'max_bytes': max_bytes, 'max_files': max_files},
            'files': rows, 'total_bytes': total, 'file_count': len(rows),
            'bytes_exceeded': total > max_bytes, 'count_exceeded': len(rows) > max_files,
            'skipped_count': skipped, 'unreadable_count': unreadable,
            'complete': unreadable == 0}
    if retention_policy is not None:
        result['policy'].update(safe_days=retention_policy.safe_days, detail_days=retention_policy.detail_days)
    return result


class _InventoryParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse의 오류 설명에는 사용자가 입력한 경로나 옵션 값이 포함될 수 있다.
        self.exit(2, '{"error_code": "LOG_INVENTORY_UNAVAILABLE"}\n')


def main(argv=None):
    parser = _InventoryParser(description='진단 로그 보관 검토 미리보기')
    parser.add_argument('--log-dir', required=True, type=Path)
    parser.add_argument('--review-days', type=float, default=30)
    parser.add_argument('--max-bytes', type=int, default=104857600)
    parser.add_argument('--max-files', type=int, default=100)
    parser.add_argument('--retention', action='store_true', help='MCP 종료 기록과 보관 기간 검사')
    parser.add_argument('--safe-days', type=int)
    parser.add_argument('--detail-days', type=int)
    args = parser.parse_args(argv)
    try:
        overrides = {k: v for k, v in (('safe_days', args.safe_days), ('detail_days', args.detail_days))
                     if v is not None}
        if overrides and not args.retention:
            raise ValueError('보관 기간 설정에는 --retention이 필요합니다.')
        policy = RetentionPolicy(**overrides) if args.retention else None
        result = inventory(args.log_dir, retention_policy=policy, review_days=args.review_days,
                           max_bytes=args.max_bytes, max_files=args.max_files)
    except (OSError, ValueError):
        print(json.dumps({'error_code': 'LOG_INVENTORY_UNAVAILABLE'}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
