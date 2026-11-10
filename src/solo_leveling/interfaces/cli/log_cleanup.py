"""선택한 만료 로그 묶음을 미리 보고 같은 묶음만 명시적으로 정리한다."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from solo_leveling.infrastructure.log_retention.contracts import BundleId
from solo_leveling.infrastructure.log_retention.policy import RetentionPolicy, closed_name
from solo_leveling.infrastructure.log_retention.preview import inspect_bundle
from .log_inventory import _InventoryParser


def main(argv=None):
    parser = _InventoryParser(description=__doc__)
    parser.add_argument('--log-dir', required=True, type=Path)
    parser.add_argument('--bundle', required=True, help='로그 소유자와 실행 ID (예: worker-<실행 ID>)')
    parser.add_argument('--apply', metavar='TOKEN', help='직전 미리보기의 확인값')
    parser.add_argument('--safe-days', type=int, default=30)
    parser.add_argument('--detail-days', type=int, default=7)
    args = parser.parse_args(argv)
    apply = args.apply is not None
    result = {'mode': 'apply' if apply else 'preview', 'bundle': args.bundle}

    def verified(lease, observed, descriptors, payload):
        record = closed_name(bundle)
        fingerprint = [(name, s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                       for name, s in sorted(observed.items())]
        lock_name = f'slp-log-v1-{bundle.owner}-{bundle.run_id}.lock'
        scope = [(s.st_dev, s.st_ino) for s in (lease.root.lstat(), lease._stat_for_preview(lock_name))]
        token = hashlib.sha256(json.dumps([str(lease.root), scope, policy.safe_days,
                                            policy.detail_days, hashlib.sha256(payload).hexdigest(), fingerprint]).encode()).hexdigest()
        result['bytes'] = sum(s.st_size for name, s in observed.items() if name != record)
        if not apply:
            result['token'] = token
            return 'expired'
        if token != args.apply:
            return 'file_changed'
        return lease.remove_verified(observed, descriptors, record)

    try:
        owner, run_id = args.bundle.split('-', 1)
        bundle = BundleId(owner, run_id)
        if apply and not re.fullmatch('[0-9a-f]{64}', args.apply):
            raise ValueError()
        policy = RetentionPolicy(args.safe_days, args.detail_days)
        status = inspect_bundle(args.log_dir.absolute(), bundle, None, datetime.now(timezone.utc),
                                policy, verified=verified, apply=apply)
    except (ValueError, OSError):
        print(json.dumps({'error_code': 'LOG_CLEANUP_UNAVAILABLE'}))
        return 2
    result['status'] = status
    print(json.dumps(result))
    if status == 'unsupported':
        return 2
    return int(status in ('io_error', 'recovery_required'))


if __name__ == '__main__':
    raise SystemExit(main())
