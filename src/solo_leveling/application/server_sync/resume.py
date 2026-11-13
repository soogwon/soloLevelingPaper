"""인증된 기기로 웹의 이어하기 코드를 조회하고 응답 계약을 검증한다."""

from pathlib import Path
from typing import Any

from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.pdf_source import open_pdf_source
from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid


class ResumeRequestUnavailable(Exception):
    pass


class ResumeRequestExpired(Exception):
    pass


class ResumeRequestResolver:
    def __init__(self, client: Any, db_path: str | None = None, pdf_root: Path | None = None):
        if (db_path is None) != (pdf_root is None):
            raise ValueError("로컬 상태 확인에는 DB와 PDF 저장 경로가 모두 필요합니다.")
        self._pdf_root = pdf_root
        self._client = client
        self._db_path = db_path

    def resolve(self, code: str) -> dict[str, Any]:
        account_id = self._client.connection_id() if self._db_path is not None else None
        response = self._client.request(
            "POST", "/v1/mcp/resume-requests/resolve", json={"code": code}, timeout=15,
            expected_connection_id=account_id,
        )
        try:
            if response.status_code == 404:
                raise ResumeRequestUnavailable
            if response.status_code == 410:
                raise ResumeRequestExpired
            if not 200 <= response.status_code < 300:
                response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                raise ServerResponseInvalid from None
        finally:
            response.close()
        if not _valid(data):
            raise ServerResponseInvalid
        if self._db_path is not None and self._pdf_root is not None:
            _apply_local_file_status(data, self._db_path, account_id, self._pdf_root)
        return data


def _apply_local_file_status(data: dict[str, Any], db_path: str, account_id: str, pdf_root: Path) -> None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT m.local_object_id, m.local_subobject_id, c.version_id, v.stored_path
               FROM server_subobject_mappings m
               LEFT JOIN learning_contexts c ON c.context_id=m.local_subobject_id
               LEFT JOIN paper_versions v
                 ON v.version_id=c.version_id AND v.paper_id=m.local_object_id
               WHERE m.account_id=? AND m.object_type='learning_record'
                 AND m.server_object_id=?""",
            (account_id, data["learning_record_id"]),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["version_id"] is None:
        data.update(file_access_status="local_mapping_missing", status="action_required",
                    reason="local_mapping_missing")
    elif row["version_id"] != data["version_id"]:
        data.update(file_access_status="local_version_mismatch", status="action_required",
                    reason="local_version_mismatch")
    elif not _available_pdf(pdf_root, row["stored_path"]):
        data.update(file_access_status="local_file_missing", status="action_required",
                    reason="local_file_missing")
    else:
        data.update(file_access_status="local_file_ready", status="prepared",
                    reason="local_search_data_check_required")


def _available_pdf(root: Path, stored_path: str | None) -> bool:
    if not stored_path:
        return False
    source = root / stored_path
    if source.suffix.lower() != ".pdf":
        return False
    try:
        with open_pdf_source(source, root):
            return True
    except (OSError, ValueError):
        return False


def _valid(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    required = ("resume_request_id", "paper_id", "version_id", "learning_record_id",
                "file_access_status", "status")
    if any(not isinstance(data.get(key), str) or not data[key] for key in required):
        return False
    if data.get("title") is not None and not isinstance(data["title"], str):
        return False
    if data.get("file_name") is not None and not isinstance(data["file_name"], str):
        return False
    if data.get("last_position") is not None and not isinstance(data["last_position"], dict):
        return False
    if data.get("annotation") is not None and not isinstance(data["annotation"], dict):
        return False
    return True
