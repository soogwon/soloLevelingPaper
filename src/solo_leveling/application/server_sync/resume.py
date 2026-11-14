"""인증된 기기로 웹의 이어하기 코드를 조회하고 응답 계약을 검증한다."""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

from solo_leveling.domain.annotation import (
    AnnotationValidationError, SegmentInput, validate_annotation_fields, validate_segments,
)
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.pdf_source import open_pdf_source
from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid


class ResumeRequestUnavailable(Exception):
    pass


class ResumeRequestExpired(Exception):
    pass


class ResumeRequestResolver:
    def __init__(self, client: Any, db_path: str | None = None, pdf_root: Path | None = None,
                 chroma_client: Any | None = None):
        if (db_path is None) != (pdf_root is None):
            raise ValueError("로컬 상태 확인에는 DB와 PDF 저장 경로가 모두 필요합니다.")
        if chroma_client is not None and db_path is None:
            raise ValueError("검색 데이터 확인에는 로컬 DB가 필요합니다.")
        self._pdf_root = pdf_root
        self._chroma_client = chroma_client
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
            _apply_local_status(data, self._db_path, account_id, self._pdf_root,
                                self._chroma_client)
        return data


def _apply_local_status(data: dict[str, Any], db_path: str, account_id: str, pdf_root: Path,
                        chroma_client: Any | None) -> None:
    data["resume_position_status"] = "not_checked"
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
    elif chroma_client is not None and _available_search_data(
            db_path, chroma_client, row["local_object_id"], row["local_subobject_id"],
            row["version_id"]):
        position_status = _resume_position_status(
            db_path, row["local_subobject_id"], data.get("last_position"),
        )
        if position_status == "local_position_unavailable":
            data.update(file_access_status="local_file_ready", status="action_required",
                        reason="local_resume_position_unavailable",
                        resume_position_status=position_status)
        else:
            data.update(file_access_status="local_file_ready", status="prepared",
                        reason="local_search_data_ready",
                        resume_position_status=position_status)
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


def _available_search_data(db_path: str, chroma_client: Any, paper_id: str,
                           context_id: str, version_id: str) -> bool:
    try:
        with closing(get_connection(db_path)) as conn:
            row = conn.execute(
                """SELECT c.embedding_set_id, s.chunk_count
                   FROM learning_contexts c
                   JOIN search_indexes s ON s.embedding_set_id=c.embedding_set_id
                   WHERE c.context_id=? AND c.version_id=? AND s.version_id=?""",
                (context_id, version_id, version_id),
            ).fetchone()
            if row is None or row["chunk_count"] < 1:
                return False
            chunk_ids = [entry["chunk_id"] for entry in conn.execute(
                "SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id=?",
                (row["embedding_set_id"],),
            )]
            if len(chunk_ids) != row["chunk_count"]:
                return False
    except sqlite3.Error:
        return False

    try:
        collection = chroma_client.get_collection(f"chunks-{row['embedding_set_id']}")
        if collection.count() != len(chunk_ids):
            return False
        stored = collection.get(ids=chunk_ids, include=["metadatas"])
        if (set(stored["ids"]) != set(chunk_ids)
                or len(stored["ids"]) != len(chunk_ids)
                or len(stored["metadatas"]) != len(chunk_ids)):
            return False
        expected = {"embedding_set_id": row["embedding_set_id"],
                    "paper_id": paper_id, "version_id": version_id}
        return all(all(metadata.get(key) == value for key, value in expected.items())
                   for metadata in stored["metadatas"])
    except Exception:
        return False


def _resume_position_status(db_path: str, context_id: str,
                            last_position: dict[str, Any] | None) -> str:
    if last_position is None:
        return "no_saved_position"
    chunk_id = last_position.get("chunk_id")
    section_id = last_position.get("section_id")
    page = last_position.get("pdf_page")
    if page is not None and (type(page) is not int or page < 1):
        return "local_position_unavailable"
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in (chunk_id, section_id)):
        return "local_position_unavailable"
    if chunk_id is None and section_id is None and page is None:
        return "local_position_unavailable"
    try:
        with closing(get_connection(db_path)) as conn:
            if chunk_id is None and section_id is not None:
                row = conn.execute(
                    """SELECT 1 FROM learning_contexts c
                       JOIN sections s ON s.parse_revision_id=c.parse_revision_id
                       WHERE c.context_id=? AND s.section_id=?
                         AND (? IS NULL OR ? BETWEEN s.start_pdf_page AND s.end_pdf_page)""",
                    (context_id, section_id, page, page),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT 1 FROM learning_contexts c
                       JOIN search_index_chunks sic ON sic.embedding_set_id=c.embedding_set_id
                       JOIN chunks ch ON ch.chunk_id=sic.chunk_id
                                     AND ch.parse_revision_id=c.parse_revision_id
                       WHERE c.context_id=? AND (? IS NULL OR ch.chunk_id=?)
                         AND (? IS NULL OR ch.pdf_page=?)
                         AND (? IS NULL OR ch.section_id=?) LIMIT 1""",
                    (context_id, chunk_id, chunk_id, page, page, section_id, section_id),
                ).fetchone()
    except sqlite3.Error:
        return "local_position_unavailable"
    return "local_position_ready" if row is not None else "local_position_unavailable"


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
    if not _valid_annotation(data.get("annotation_id"), data.get("annotation")):
        return False
    return True


def _valid_annotation(annotation_id: Any, annotation: Any) -> bool:
    if annotation_id is None:
        return annotation is None
    if not isinstance(annotation_id, str) or not annotation_id:
        return False
    if not isinstance(annotation, dict) or annotation.get("annotation_id") != annotation_id:
        return False
    segments = annotation.get("segments")
    if not isinstance(segments, list):
        return False
    try:
        validate_annotation_fields(annotation.get("kind"), annotation.get("body"), None, len(segments))
        checked = []
        for segment in segments:
            if not isinstance(segment, dict):
                return False
            revision = segment.get("parse_revision_id")
            translation = segment.get("translation_revision_id")
            if not isinstance(revision, str) or not revision.strip():
                return False
            if translation is not None and not isinstance(translation, str):
                return False
            checked.append(SegmentInput(
                chunk_id=segment.get("chunk_id"), target=segment.get("target"),
                start_offset=segment.get("start_offset"), end_offset=segment.get("end_offset"),
                quote_snapshot=segment.get("quote_snapshot"), translation_revision_id=translation,
            ))
        validate_segments(checked)
    except AnnotationValidationError:
        return False
    return True
