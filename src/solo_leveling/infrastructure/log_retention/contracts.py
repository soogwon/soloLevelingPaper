"""OS 구현에 앞서 정의하는 공통 타입. 삭제 권한은 제공하지 않는다."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
import re


Owner = Literal['mcp', 'worker']
Kind = Literal['safe', 'detail', 'raw', 'stack']


@dataclass(frozen=True)
class BundleId:
    owner: Owner
    run_id: str

    def __post_init__(self):
        if (self.owner not in ('mcp', 'worker') or not isinstance(self.run_id, str)
                or re.fullmatch('[0-9a-f]{32}', self.run_id) is None):
            raise ValueError('INVALID_BUNDLE_ID')


class BundleLease(Protocol):
    """부모만 보유하는 핸들. 해제 후 재사용과 자식 상속을 허용하지 않는다."""
    @property
    def root(self) -> Path: ...
    @property
    def bundle_id(self) -> BundleId: ...
    @property
    def role(self) -> Literal['writer', 'inspector']: ...
    def release(self) -> None: ...


@dataclass(frozen=True)
class LeaseResult:
    status: Literal['acquired', 'busy', 'missing', 'unsupported', 'io_error']
    lease: BundleLease | None = None

    def __post_init__(self):
        if (self.status not in ('acquired', 'busy', 'missing', 'unsupported', 'io_error')
                or (self.status == 'acquired') != (self.lease is not None)):
            raise ValueError('INVALID_LEASE_RESULT')


class BundleLocks(Protocol):
    """잠금은 비차단 1회만 시도하며 inspector는 기존 파일만 연다."""
    def try_acquire_writer(self, root: Path, bundle_id: BundleId) -> LeaseResult: ...
    def try_acquire_inspector(self, root: Path, bundle_id: BundleId) -> LeaseResult: ...


@dataclass(frozen=True)
class ClosedFile:
    kind: Kind
    name: str
    size_bytes: int


class BundlePublisher(Protocol):
    """유효한 writer lease로만 게시한다. OS 구현에서 동일성 정보를 수집한다."""
    def publish_closed(self, lease: BundleLease, files: tuple[ClosedFile, ...],
                       closed_at: datetime) -> Literal[
                           'published', 'invalid_state', 'unsupported', 'io_error']: ...
