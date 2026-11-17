"""원격 처리 revision 집합을 사용자 범위 안에서 원자적으로 게시한다."""

from dataclasses import dataclass
import json
from typing import Any, Literal, Protocol
from uuid import uuid4


PublicationPolicy = Literal["complete", "partial"]


class ProcessingPublicationNotFound(Exception):
    """인증 사용자 범위에서 처리 revision 집합을 찾지 못했다."""


class ProcessingPublicationNotReady(Exception):
    """처리 결과가 게시 가능한 확정 집합이 아니다."""


@dataclass(frozen=True)
class ProcessingPublication:
    publication_id: str
    paper_id: str
    server_version_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    policy: PublicationPolicy
    manifest: dict[str, Any]


class ProcessingPublicationStore(Protocol):
    def publish(
        self,
        owner_user_id: str,
        paper_id: str,
        server_version_id: str,
        parse_revision_id: str,
        translation_revision_id: str,
        embedding_set_id: str,
        source_job_id: str,
        source_lease_token: str,
        *,
        policy: PublicationPolicy,
    ) -> ProcessingPublication: ...

    def get_active(
        self, owner_user_id: str, paper_id: str, server_version_id: str
    ) -> ProcessingPublication: ...


class PostgresProcessingPublicationStore:
    """owner가 포함된 복합 참조와 active pointer 교체를 한 transaction으로 처리한다."""

    def __init__(self, connection_pool):
        self._pool = connection_pool

    def publish(
        self,
        owner_user_id: str,
        paper_id: str,
        server_version_id: str,
        parse_revision_id: str,
        translation_revision_id: str,
        embedding_set_id: str,
        source_job_id: str,
        source_lease_token: str,
        *,
        policy: PublicationPolicy,
    ) -> ProcessingPublication:
        if policy not in ("complete", "partial"):
            raise ValueError("publication_policy_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT pr.status AS parse_status, pr.chunk_count,
                          tr.status AS translation_status, tr.source_chunk_count,
                          tr.translated_chunk_count, tr.omissions,
                          es.status AS embedding_status, es.indexed_chunk_count,
                          (SELECT count(*) FROM server_processing_chunks c
                            WHERE c.owner_user_id=pr.owner_user_id
                              AND c.paper_id=pr.paper_id
                              AND c.server_version_id=pr.server_version_id
                              AND c.parse_revision_id=pr.parse_revision_id) AS actual_chunk_count,
                          (SELECT count(*) FROM server_translation_chunks tc
                            WHERE tc.owner_user_id=tr.owner_user_id
                              AND tc.paper_id=tr.paper_id
                              AND tc.server_version_id=tr.server_version_id
                              AND tc.parse_revision_id=tr.parse_revision_id
                              AND tc.translation_revision_id=tr.translation_revision_id)
                              AS actual_translation_count,
                          (SELECT count(*) FROM server_processing_chunks c
                            WHERE c.owner_user_id=pr.owner_user_id
                              AND c.paper_id=pr.paper_id
                              AND c.server_version_id=pr.server_version_id
                              AND c.parse_revision_id=pr.parse_revision_id
                              AND NOT EXISTS (
                                  SELECT 1 FROM server_translation_chunks tc
                                   WHERE tc.owner_user_id=tr.owner_user_id
                                     AND tc.translation_revision_id=tr.translation_revision_id
                                     AND tc.chunk_id=c.chunk_id)
                              AND c.chunk_id::text IN (
                                  SELECT item->>'chunk_id'
                                    FROM jsonb_array_elements(tr.omissions) AS item
                                   WHERE jsonb_typeof(item)='object'))
                              AS matched_omission_count
                   FROM server_processing_versions v
                   JOIN server_parse_revisions pr
                     ON pr.owner_user_id=v.owner_user_id
                    AND pr.paper_id=v.paper_id
                    AND pr.server_version_id=v.server_version_id
                   JOIN server_translation_revisions tr
                     ON tr.owner_user_id=pr.owner_user_id
                    AND tr.paper_id=pr.paper_id
                    AND tr.server_version_id=pr.server_version_id
                    AND tr.parse_revision_id=pr.parse_revision_id
                   JOIN server_embedding_sets es
                     ON es.owner_user_id=tr.owner_user_id
                    AND es.paper_id=tr.paper_id
                    AND es.server_version_id=tr.server_version_id
                    AND es.parse_revision_id=tr.parse_revision_id
                    AND es.translation_revision_id=tr.translation_revision_id
                   JOIN server_processing_jobs j
                     ON j.owner_user_id=v.owner_user_id
                    AND j.paper_id=v.paper_id
                    AND j.server_version_id=v.server_version_id
                   WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
                     AND pr.parse_revision_id=%s
                     AND tr.translation_revision_id=%s
                     AND es.embedding_set_id=%s
                     AND j.job_id=%s AND j.status='running' AND j.lease_token=%s
                     AND j.lease_expires_at>clock_timestamp()
                   FOR UPDATE OF v, pr, tr, es, j""",
                (
                    owner_user_id,
                    paper_id,
                    server_version_id,
                    parse_revision_id,
                    translation_revision_id,
                    embedding_set_id,
                    source_job_id,
                    source_lease_token,
                ),
            ).fetchone()
            if row is None:
                # 같은 실행 세대가 이미 게시한 뒤 응답을 잃고 다시 요청한 경우 그 결과를 재생한다.
                replayed = self._replay(
                    conn,
                    owner_user_id,
                    paper_id,
                    server_version_id,
                    parse_revision_id,
                    translation_revision_id,
                    embedding_set_id,
                    source_job_id,
                    source_lease_token,
                    policy,
                )
                if replayed is not None:
                    return replayed
                raise ProcessingPublicationNotFound("processing_revision_set_not_found")
            manifest = _validated_manifest(row, policy)
            publication_id = str(uuid4())
            conn.execute(
                """INSERT INTO server_processing_publications
                       (publication_id, owner_user_id, paper_id, server_version_id,
                        parse_revision_id, translation_revision_id, embedding_set_id,
                        publication_policy, manifest, source_job_id, source_lease_token)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                (
                    publication_id,
                    owner_user_id,
                    paper_id,
                    server_version_id,
                    parse_revision_id,
                    translation_revision_id,
                    embedding_set_id,
                    policy,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                    source_job_id,
                    source_lease_token,
                ),
            )
            updated = conn.execute(
                """UPDATE server_processing_versions
                   SET active_publication_id=%s
                   WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                   RETURNING server_version_id""",
                (publication_id, owner_user_id, paper_id, server_version_id),
            ).fetchone()
            if updated is None:
                raise ProcessingPublicationNotFound("processing_version_not_found")
            # 게시와 작업 완료를 같은 transaction에서 처리한다. 게시만 되고 작업이 running으로
            # 남으면 lease 만료 뒤 다시 claim되어 같은 처리를 반복하고 비용이 중복된다.
            finished = conn.execute(
                """UPDATE server_processing_jobs
                   SET status='succeeded', error_code=NULL, lease_owner=NULL, lease_token=NULL,
                       lease_expires_at=NULL, heartbeat_at=NULL,
                       finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND job_id=%s AND status='running'
                     AND lease_token=%s AND lease_expires_at>clock_timestamp()
                   RETURNING job_id""",
                (owner_user_id, source_job_id, source_lease_token),
            ).fetchone()
            if finished is None:
                raise ProcessingPublicationNotFound("processing_revision_set_not_found")
        return ProcessingPublication(
            publication_id,
            paper_id,
            server_version_id,
            parse_revision_id,
            translation_revision_id,
            embedding_set_id,
            policy,
            manifest,
        )

    @staticmethod
    def _replay(
        conn,
        owner_user_id: str,
        paper_id: str,
        server_version_id: str,
        parse_revision_id: str,
        translation_revision_id: str,
        embedding_set_id: str,
        source_job_id: str,
        source_lease_token: str,
        policy: PublicationPolicy,
    ) -> ProcessingPublication | None:
        """같은 job·같은 실행 세대·같은 revision 집합의 기존 게시만 돌려준다."""
        row = conn.execute(
            """SELECT publication_id, publication_policy, manifest
               FROM server_processing_publications
               WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                 AND parse_revision_id=%s AND translation_revision_id=%s
                 AND embedding_set_id=%s
                 AND source_job_id=%s AND source_lease_token=%s
                 AND publication_policy=%s
                 AND EXISTS (SELECT 1 FROM server_papers paper
                              WHERE paper.owner_user_id=server_processing_publications.owner_user_id
                                AND paper.paper_id=server_processing_publications.paper_id
                                AND paper.deleted_at IS NULL)""",
            (
                owner_user_id,
                paper_id,
                server_version_id,
                parse_revision_id,
                translation_revision_id,
                embedding_set_id,
                source_job_id,
                source_lease_token,
                policy,
            ),
        ).fetchone()
        if row is None:
            return None
        return ProcessingPublication(
            str(row["publication_id"]),
            paper_id,
            server_version_id,
            parse_revision_id,
            translation_revision_id,
            embedding_set_id,
            row["publication_policy"],
            dict(row["manifest"]),
        )

    def get_active(
        self, owner_user_id: str, paper_id: str, server_version_id: str
    ) -> ProcessingPublication:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT p.publication_id, p.paper_id, p.server_version_id,
                          p.parse_revision_id, p.translation_revision_id,
                          p.embedding_set_id, p.publication_policy, p.manifest
                   FROM server_processing_versions v
                   JOIN server_processing_publications p
                     ON p.owner_user_id=v.owner_user_id
                    AND p.paper_id=v.paper_id
                    AND p.server_version_id=v.server_version_id
                    AND p.publication_id=v.active_publication_id
                   WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
                     AND EXISTS (SELECT 1 FROM server_papers paper WHERE paper.owner_user_id=v.owner_user_id
                                 AND paper.paper_id=v.paper_id AND paper.deleted_at IS NULL)""",
                (owner_user_id, paper_id, server_version_id),
            ).fetchone()
        if row is None:
            raise ProcessingPublicationNotFound("active_publication_not_found")
        return ProcessingPublication(
            str(row["publication_id"]),
            str(row["paper_id"]),
            str(row["server_version_id"]),
            str(row["parse_revision_id"]),
            str(row["translation_revision_id"]),
            str(row["embedding_set_id"]),
            row["publication_policy"],
            dict(row["manifest"]),
        )


def _validated_manifest(row, policy: PublicationPolicy) -> dict[str, Any]:
    if any(
        row[name] != "ready"
        for name in ("parse_status", "translation_status", "embedding_status")
    ):
        raise ProcessingPublicationNotReady("processing_revision_not_ready")
    chunk_count = int(row["chunk_count"])
    source_count = int(row["source_chunk_count"])
    translated_count = int(row["translated_chunk_count"])
    indexed_count = int(row["indexed_chunk_count"])
    actual_chunk_count = int(row["actual_chunk_count"])
    actual_translation_count = int(row["actual_translation_count"])
    omissions = list(row["omissions"])
    if actual_chunk_count != chunk_count:
        raise ProcessingPublicationNotReady("parse_chunk_count_mismatch")
    if actual_translation_count != translated_count:
        raise ProcessingPublicationNotReady("translation_chunk_count_mismatch")
    if source_count != chunk_count:
        raise ProcessingPublicationNotReady("translation_source_count_mismatch")
    if translated_count + len(omissions) != chunk_count:
        raise ProcessingPublicationNotReady("translation_manifest_incomplete")
    if indexed_count != translated_count:
        raise ProcessingPublicationNotReady("embedding_count_mismatch")
    if policy == "complete" and omissions:
        raise ProcessingPublicationNotReady("complete_publication_has_omissions")
    if policy == "partial" and not omissions:
        raise ProcessingPublicationNotReady("partial_publication_requires_omissions")
    omission_ids = []
    for omission in omissions:
        if not isinstance(omission, dict) or not omission.get("chunk_id") or not omission.get("reason"):
            raise ProcessingPublicationNotReady("translation_omission_invalid")
        omission_ids.append(str(omission["chunk_id"]))
    if len(omission_ids) != len(set(omission_ids)):
        raise ProcessingPublicationNotReady("translation_omission_duplicate")
    # 개수만 맞추면 없는 chunk나 이미 번역된 chunk를 누락으로 적어도 통과한다.
    # 누락 항목은 이 파싱 revision의 chunk이면서 번역 행이 없는 것과 정확히 일치해야 한다.
    if int(row["matched_omission_count"]) != len(omission_ids):
        raise ProcessingPublicationNotReady("translation_omission_mismatch")
    # 번역이 하나도 없는 집합은 partial로도 게시하지 않는다.
    if policy == "partial" and translated_count == 0:
        raise ProcessingPublicationNotReady("partial_publication_has_no_translation")
    return {
        "chunk_count": chunk_count,
        "translated_chunk_count": translated_count,
        "indexed_chunk_count": indexed_count,
        "omissions": omissions,
        "policy": policy,
    }
