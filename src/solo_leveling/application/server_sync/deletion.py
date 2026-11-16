"""서버 삭제 의도를 먼저 영속화하고 같은 키로 재접수한다. 로컬 자료는 보존한다."""

from contextlib import closing
import sqlite3
from uuid import UUID, uuid4

from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


class DeletionUnavailable(Exception):
    """결과 미확정. 새 키로 우회하지 않고 기존 요청을 확인해야 한다."""


class ServerPaperDeletionService:
    def __init__(self, db_path, transport):
        self.db_path = str(db_path)
        self.transport = transport
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS client_deletion_intents (
                connection_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                server_paper_id TEXT NOT NULL, request_key TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', request_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(connection_id,paper_id), UNIQUE(connection_id,request_key))""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def preview(self, paper_id):
        connection = self.transport.connection_id()
        with closing(self._connect()) as conn:
            paper = conn.execute("SELECT title FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
            mappings = conn.execute(
                "SELECT server_object_id FROM server_object_mappings WHERE account_id=? AND object_type='paper' AND local_object_id=?",
                (connection, paper_id),
            ).fetchall()
            if paper is None or len(mappings) != 1:
                raise KeyError("deletion_target_not_found")
        return {
            "paper_id": paper_id,
            "server_paper_id": mappings[0][0],
            "connection_id": connection,
            "title": paper[0] or "제목 없는 논문",
        }

    def confirm(self, preview):
        current = self.preview(preview["paper_id"])
        if current != preview:
            raise ServerConnectionRequired()
        # 네트워크 호출 또는 로컬 행 제거 전에 별도 삭제 의도를 커밋한다.
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO client_deletion_intents
                (connection_id,paper_id,server_paper_id,request_key) VALUES (?,?,?,?)""",
                (current["connection_id"], current["paper_id"], current["server_paper_id"], str(uuid4())),
            )
            existing = conn.execute(
                "SELECT * FROM client_deletion_intents WHERE connection_id=? AND paper_id=?",
                (current["connection_id"], current["paper_id"]),
            ).fetchone()
            if existing["server_paper_id"] != current["server_paper_id"]:
                raise DeletionUnavailable()
        # 승인 시점의 연결과 대상을 사용한다. 이후 바뀐 계정의 요청을 대신 보내지 않는다.
        return self._exchange(dict(existing), "GET" if existing["state"] != "pending" else "PUT")

    def _intent(self, paper_id):
        connection = self.transport.connection_id()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM client_deletion_intents WHERE connection_id=? AND paper_id=?", (connection, paper_id)
            ).fetchone()
        if row is None:
            raise KeyError("deletion_request_not_found")
        return dict(row)

    def _exchange(self, intent, method):
        path = f"/v1/papers/{intent['server_paper_id']}/deletion-requests/{intent['request_key']}"
        options = {"expected_connection_id": intent["connection_id"], "timeout": 20}
        if method == "PUT":
            options["json"] = {"confirmed": True, "notice_version": "server-paper-deletion-v1"}
        try:
            response = self.transport.request(method, path, **options)
            try:
                if response.status_code != 200:
                    raise DeletionUnavailable()
                result = response.json()
            finally:
                response.close()
            if self.transport.connection_id() != intent["connection_id"]:
                raise ServerConnectionRequired()
            if (
                result.get("paper_id") != intent["server_paper_id"]
                or result.get("status") not in {"blocking", "cleaning", "failed", "complete"}
                or result.get("backup_erasure_confirmed") is not False
            ):
                raise DeletionUnavailable()
            UUID(result["request_id"])
            if intent["request_id"] is not None and intent["request_id"] != result["request_id"]:
                raise DeletionUnavailable()
        except ServerConnectionRequired:
            raise
        except Exception:
            raise DeletionUnavailable() from None
        with closing(self._connect()) as conn, conn:
            # 오래된 응답이 완료 표식을 낮추지 않게 한다.
            conn.execute(
                """UPDATE client_deletion_intents SET state=?,request_id=?
                WHERE connection_id=? AND paper_id=? AND state!='complete' """,
                (result["status"], result["request_id"], intent["connection_id"], intent["paper_id"]),
            )
            saved = conn.execute(
                "SELECT state FROM client_deletion_intents WHERE connection_id=? AND paper_id=?",
                (intent["connection_id"], intent["paper_id"]),
            ).fetchone()
        if self.transport.connection_id() != intent["connection_id"]:
            raise ServerConnectionRequired()
        return {
            "paper_id": intent["paper_id"],
            "status": saved["state"],
            "backup_erasure_confirmed": False,
            "local_data_preserved": True,
        }

    def retry(self, paper_id):
        """이미 승인해 기록한 동일 요청만 재전송한다. 새 의도를 만들지 않는다."""
        intent = self._intent(paper_id)
        return self._exchange(intent, "GET" if intent["state"] != "pending" else "PUT")

    def status(self, paper_id):
        """서버 조회가 실패해도 저장된 의도는 남긴다. 조회만으로 PUT하지 않는다."""
        return self._exchange(self._intent(paper_id), "GET")
