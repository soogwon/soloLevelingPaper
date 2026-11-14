"""명시적으로 승인된 관리 PDF 한 버전을 중앙 비공개 저장소에 업로드한다."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryFile
from typing import Any, BinaryIO
from urllib.parse import quote, urlsplit
from uuid import UUID

from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.pdf_source import open_pdf_source
from solo_leveling.infrastructure.storage.server_client import (
    ServerConnectionRequired, ServerResponseInvalid,
)


PDF_UPLOAD_NOTICE_VERSION = "paper-pdf-upload-v1"


class PdfUploadStageError(ServerResponseInvalid):
    """업로드 단계와 안전한 서버 오류 정보만 상위 계층에 전달한다."""

    def __init__(self, stage: str, status_code: int | None = None,
                 detail_code: str | None = None):
        super().__init__(stage)
        self.stage = stage
        self.status_code = status_code
        self.detail_code = detail_code


@dataclass(frozen=True)
class PdfUploadPreview:
    account_id: str
    paper_id: str
    server_paper_id: str
    version_id: str
    file_name: str
    file_size: int
    sha256_hex: str
    source: Path


@dataclass(frozen=True)
class PdfUploadResult:
    upload_id: str
    paper_id: str
    version_id: str
    file_name: str
    file_size: int
    content_type: str
    status: str


class PaperPdfUploadService:
    def __init__(self, db_path: str, pdf_root: Path, client: Any, upload_session: Any):
        self._db_path = db_path
        self._pdf_root = pdf_root.resolve()
        self._client = client
        self._upload_session = upload_session

    def preview(self, paper_id: str, version_id: str | None = None) -> PdfUploadPreview:
        account_id = self._client.connection_id()
        conn = get_connection(self._db_path)
        try:
            mapping = conn.execute(
                """SELECT m.server_object_id FROM server_object_mappings m
                   WHERE m.account_id=? AND m.object_type='paper' AND m.local_object_id=?
                     AND m.source_instance_id=(
                       SELECT source_instance_id FROM server_sync_identity WHERE singleton=1)""",
                (account_id, paper_id),
            ).fetchone()
            if mapping is None:
                raise ServerConnectionRequired("paper_sync_required")
            params: list[Any] = [paper_id]
            version_filter = ""
            if version_id is not None:
                version_filter = " AND v.version_id=?"
                params.append(version_id)
            row = conn.execute(
                f"""SELECT v.version_id, v.file_hash, v.stored_path, v.original_filename
                    FROM paper_versions v
                    JOIN learning_contexts c ON c.version_id=v.version_id
                    LEFT JOIN learning_progress lp ON lp.context_id=c.context_id
                    WHERE v.paper_id=? {version_filter}
                    ORDER BY COALESCE(lp.last_studied_at, c.created_at, v.created_at) DESC,
                             v.created_at DESC, v.version_id DESC LIMIT 1""",
                tuple(params),
            ).fetchone()
            if row is None:
                raise KeyError("업로드할 원문 버전을 찾을 수 없습니다.")
        finally:
            conn.close()
        file_name = PurePosixPath(PureWindowsPath(str(row["original_filename"])).name).name.strip()
        if not file_name:
            file_name = "paper.pdf"
        source = Path(row["stored_path"])
        if not source.is_absolute():
            source = self._pdf_root / source
        digest, size = _inspect_pdf(source, self._pdf_root)
        if digest != row["file_hash"]:
            raise OSError("MANAGED_PDF_CHANGED")
        return PdfUploadPreview(
            account_id, paper_id, str(mapping["server_object_id"]), row["version_id"],
            file_name, size, digest, source,
        )

    def upload(self, expected: PdfUploadPreview) -> PdfUploadResult:
        current = self.preview(expected.paper_id, expected.version_id)
        if current != expected:
            raise ValueError("PDF_UPLOAD_PREVIEW_CHANGED")
        response = self._client.request(
            "POST", f"/v1/papers/{quote(current.server_paper_id, safe='')}/uploads",
            expected_connection_id=current.account_id,
            json={
                "server_version_id": current.version_id,
                "local_version_id": current.version_id,
                "file_name": current.file_name,
                "file_size": current.file_size,
                "content_type": "application/pdf",
                "sha256": current.sha256_hex,
                "consent": True,
            },
        )
        try:
            if response.status_code == 409:
                data = _stage_response_json(response, "prepare")
                if _detail_code(data) == "pdf_already_uploaded":
                    raise FileExistsError("PDF_ALREADY_UPLOADED")
            if response.status_code != 201:
                raise PdfUploadStageError(
                    "prepare", response.status_code, _safe_detail_code(response))
            try:
                prepared = _validate_prepare(_response_json(response), current.file_size)
            except ServerResponseInvalid:
                raise PdfUploadStageError("prepare", response.status_code) from None
        finally:
            response.close()

        _validate_upload_url(prepared["upload_url"])
        with TemporaryFile() as snapshot:
            digest, size = _inspect_pdf(current.source, self._pdf_root, snapshot, current.file_size)
            if (digest, size) != (current.sha256_hex, current.file_size):
                raise ValueError("PDF_UPLOAD_PREVIEW_CHANGED")
            snapshot.seek(0)
            uploaded = self._upload_session.request(
                prepared["method"], prepared["upload_url"], data=snapshot,
                headers=prepared["headers"], timeout=120, allow_redirects=False,
            )
        try:
            if not 200 <= uploaded.status_code < 300:
                raise PdfUploadStageError("transfer", uploaded.status_code)
        finally:
            uploaded.close()

        completed = self._client.request(
            "POST", f"/v1/uploads/{prepared['upload_id']}/complete",
            expected_connection_id=current.account_id,
        )
        try:
            if completed.status_code != 200:
                raise PdfUploadStageError(
                    "complete", completed.status_code, _safe_detail_code(completed))
            try:
                data = _validate_complete(
                    _response_json(completed), current, prepared["upload_id"])
            except ServerResponseInvalid:
                raise PdfUploadStageError("complete", completed.status_code) from None
        finally:
            completed.close()
        return PdfUploadResult(
            data["upload_id"], data["paper_id"], data["version_id"], data["file_name"],
            data["file_size"], data["content_type"], data["storage_status"],
        )

    def status(self, expected: PdfUploadPreview) -> dict[str, Any]:
        """승인한 버전의 서버 저장 상태만 조회하며 파일을 다시 전송하지 않는다."""
        current = self.preview(expected.paper_id, expected.version_id)
        if current != expected:
            raise ValueError("PDF_UPLOAD_PREVIEW_CHANGED")
        response = self._client.request(
            "GET",
            f"/v1/papers/{quote(current.server_paper_id, safe='')}/versions/"
            f"{quote(current.version_id, safe='')}/upload-status",
            expected_connection_id=current.account_id,
        )
        try:
            if response.status_code != 200:
                raise PdfUploadStageError(
                    "status", response.status_code, _safe_detail_code(response))
            data = _response_json(response)
            if not isinstance(data, dict) or data.get("status") not in {
                    "not_uploaded", "pending", "failed", "ready"}:
                raise PdfUploadStageError("status", response.status_code)
            return data
        finally:
            response.close()


def _inspect_pdf(source: Path, root: Path, snapshot: BinaryIO | None = None,
                 max_size: int | None = None) -> tuple[str, int]:
    digest, size, header = sha256(), 0, b""
    with open_pdf_source(source, root) as stream:
        while block := stream.read(1024 * 1024):
            header = (header + block[:5])[:5]
            size += len(block)
            if max_size is not None and size > max_size:
                raise ValueError("PDF_UPLOAD_PREVIEW_CHANGED")
            digest.update(block)
            if snapshot is not None:
                snapshot.write(block)
    if header != b"%PDF-" or size < 5:
        raise ValueError("INVALID_MANAGED_PDF")
    return digest.hexdigest(), size


def _response_json(response) -> Any:
    try:
        return response.json()
    except ValueError:
        raise ServerResponseInvalid from None


def _stage_response_json(response, stage: str) -> Any:
    try:
        return _response_json(response)
    except ServerResponseInvalid:
        raise PdfUploadStageError(stage, response.status_code) from None


def _safe_detail_code(response) -> str | None:
    try:
        return _detail_code(response.json())
    except (ValueError, TypeError, AttributeError):
        return None


def _detail_code(data: Any) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("detail"), dict):
        return None
    code = data["detail"].get("code")
    if (not isinstance(code, str) or not 1 <= len(code) <= 64
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code)):
        return None
    return code


def _validate_prepare(data: Any, size: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ServerResponseInvalid
    required = ("upload_id", "upload_url", "method", "headers", "expires_at")
    if any(key not in data for key in required):
        raise ServerResponseInvalid
    try:
        UUID(data["upload_id"])
    except (ValueError, TypeError, AttributeError):
        raise ServerResponseInvalid from None
    if data["method"] != "PUT" or not isinstance(data["headers"], dict):
        raise ServerResponseInvalid
    headers = data["headers"]
    if (headers.get("Content-Length") != str(size) or set(headers) - {"Content-Length", "Content-Type"}
            or headers.get("Content-Type") != "application/pdf"):
        raise ServerResponseInvalid
    return data


def _validate_upload_url(value: Any) -> None:
    if not isinstance(value, str):
        raise ServerResponseInvalid
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment):
        raise ServerResponseInvalid


def _validate_complete(data: Any, preview: PdfUploadPreview, upload_id: str) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("storage_status") != "ready":
        raise ServerResponseInvalid
    expected = {
        "upload_id": upload_id, "paper_id": preview.server_paper_id,
        "version_id": preview.version_id, "file_name": preview.file_name,
        "file_size": preview.file_size, "content_type": "application/pdf",
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise ServerResponseInvalid
    return data
