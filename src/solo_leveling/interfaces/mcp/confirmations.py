"""카드 확인 토큰을 MCP 프로세스 사이에서 공유한다.

Claude Desktop은 같은 MCP 서버를 프로세스 여러 개로 띄우고, 채팅의 도구 호출과 카드
(MCP Apps)의 도구 호출을 서로 다른 프로세스로 보낼 수 있다. 확인 토큰을 프로세스 메모리에만
두면 카드에서 승인해도 다른 프로세스가 토큰을 모르고 거부한다. 이 저장소는 토큰을 데이터
폴더의 SQLite에 두어 어느 프로세스에서든 한 번만 소비되게 한다.

토큰 원문은 저장하지 않고 SHA-256 해시만 보관한다.
"""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

DEFAULT_TTL_SECONDS = 600
DEFAULT_PENDING_LIMIT = 128
# 확인된(confirmed) 토큰은 상태 조회용으로만 남기며 만료 뒤 정리한다.

_SCHEMA = """CREATE TABLE IF NOT EXISTS card_confirmations (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    payload TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed')),
    expires_at REAL NOT NULL
)"""


class ConfirmationLimitExceeded(Exception):
    """한 종류의 대기 토큰이 너무 많다."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _scope(scope: Any) -> str:
    return json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class ConfirmationStore:
    """확인 토큰 발급·소비 저장소. path가 없으면 이 프로세스 메모리에만 둔다(테스트용)."""

    def __init__(self, path: Path | str | None = None, *, clock=time.time):
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self._lock = threading.Lock()
        self._memory = None
        if self.path is None:
            self._memory = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
            self._memory.execute(_SCHEMA)

    @contextmanager
    def _transaction(self):
        try:
            with self._storage_transaction() as conn:
                yield conn
        except (sqlite3.Error, OSError):
            # 경로·DB 내부 오류는 도구 응답에 노출하지 않고 승인 처리를 중단한다.
            raise ToolError('CARD_CONFIRMATION_STORE_UNAVAILABLE: 확인 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요.') from None

    @contextmanager
    def _storage_transaction(self):
        with self._lock:
            if self._memory is not None:
                conn = self._memory
                conn.execute('BEGIN IMMEDIATE')
                try:
                    yield conn
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as conn:
                conn.execute('PRAGMA busy_timeout=10000')
                conn.execute(_SCHEMA)
                # 다른 프로세스와 동시에 소비해도 한 번만 성공하도록 쓰기 잠금을 먼저 잡는다.
                conn.execute('BEGIN IMMEDIATE')
                try:
                    yield conn
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise

    def _purge(self, conn, now: float) -> None:
        conn.execute('DELETE FROM card_confirmations WHERE expires_at <= ?', (now,))

    def issue(self, kind: str, scope: Any, payload: Any = None, *,
              ttl: float = DEFAULT_TTL_SECONDS, limit: int = DEFAULT_PENDING_LIMIT) -> str:
        token = token_urlsafe(32)
        with self._transaction() as conn:
            now = self.clock()
            self._purge(conn, now)
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM card_confirmations WHERE kind=? AND state='pending'",
                (kind,)).fetchone()
            if count >= limit:
                raise ConfirmationLimitExceeded(kind)
            conn.execute(
                "INSERT INTO card_confirmations VALUES (?, ?, ?, ?, 'pending', ?)",
                (_hash(token), kind, _scope(scope),
                 None if payload is None else json.dumps(payload, ensure_ascii=False), now + ttl))
        return token

    def _row(self, conn, kind: str, token: str, state: str):
        return conn.execute(
            'SELECT scope, payload, expires_at FROM card_confirmations '
            'WHERE token_hash=? AND kind=? AND state=?',
            (_hash(token), kind, state)).fetchone()

    @staticmethod
    def _payload(raw):
        return None if raw is None else json.loads(raw)

    def take(self, kind: str, token: str, scope: Any, *, keep_confirmed: bool = False):
        """대기 토큰을 한 번만 소비하고 (True, payload)를 돌려준다. 없거나 다르면 (False, None)."""
        if not isinstance(token, str) or not token:
            return False, None
        with self._transaction() as conn:
            now = self.clock()
            row = self._row(conn, kind, token, 'pending')
            if row is None or row[2] <= now or row[0] != _scope(scope):
                return False, None
            if keep_confirmed:
                conn.execute("UPDATE card_confirmations SET state='confirmed' WHERE token_hash=?",
                             (_hash(token),))
            else:
                conn.execute('DELETE FROM card_confirmations WHERE token_hash=?', (_hash(token),))
            return True, self._payload(row[1])

    def lookup_confirmed(self, kind: str, token: str, scope: Any):
        if not isinstance(token, str) or not token:
            return False, None
        with self._transaction() as conn:
            now = self.clock()
            row = self._row(conn, kind, token, 'confirmed')
            if row is None or row[2] <= now or row[0] != _scope(scope):
                return False, None
            return True, self._payload(row[1])

    def has_pending(self, kind: str, scope: Any) -> bool:
        with self._transaction() as conn:
            now = self.clock()
            return conn.execute(
                "SELECT 1 FROM card_confirmations WHERE kind=? AND scope=? AND state='pending' "
                'AND expires_at > ? LIMIT 1', (kind, _scope(scope), now)).fetchone() is not None

    def pending_count(self, kind: str) -> int:
        with self._transaction() as conn:
            now = self.clock()
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM card_confirmations WHERE kind=? AND state='pending' "
                'AND expires_at > ?', (kind, now)).fetchone()
            return count

    def cancel(self, kind: str, token: str) -> None:
        if not isinstance(token, str) or not token:
            return
        with self._transaction() as conn:
            conn.execute('DELETE FROM card_confirmations WHERE token_hash=? AND kind=?',
                         (_hash(token), kind))
