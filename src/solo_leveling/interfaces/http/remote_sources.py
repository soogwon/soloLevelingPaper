"""고정 원격 publication에서 검색·인접 출처를 읽는 PostgreSQL adapter."""

import json

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteRetrievalContractError,
    RemoteSearchBinding,
)
from solo_leveling.application.evidence_qa.remote_sources import RemoteSourceChunk


class PostgresRemoteSourceReader:
    def __init__(self, pool):
        self._pool = pool

    def read_chunks(self, binding: RemoteSearchBinding, chunk_ids: tuple[str, ...]) -> tuple[RemoteSourceChunk, ...]:
        _validate(binding, chunk_ids)
        if not chunk_ids:
            return ()
        return self._read(binding, "c.chunk_id = ANY(%s::uuid[])", (list(chunk_ids),))

    def following_chunks(
        self, binding: RemoteSearchBinding, ordinals: tuple[int, ...]
    ) -> tuple[RemoteSourceChunk, ...]:
        if (
            not isinstance(binding, RemoteSearchBinding)
            or not isinstance(ordinals, tuple)
            or any(type(value) is not int or value < 0 for value in ordinals)
            or len(set(ordinals)) != len(ordinals)
        ):
            raise RemoteRetrievalContractError("REMOTE_SOURCE_INPUT_INVALID")
        if not ordinals:
            return ()
        return self._read(binding, "c.ordinal = ANY(%s::integer[])", (list(ordinals),))

    def _read(self, binding, predicate, tail):
        try:
            vector_table = ('server_embedding_fragments' if 'split_policy' in json.loads(binding.model_fingerprint)
                            else 'server_embedding_vectors')
            with self._pool.connection() as conn:
                rows = conn.execute(
                    f"""SELECT c.chunk_id,c.ordinal,c.pdf_page,c.section_id,c.original_text,
                               tc.translated_text,tc.translation_status
                        FROM server_execution_contexts ec
                        JOIN server_papers p
                          ON p.owner_user_id=ec.owner_user_id AND p.paper_id=ec.paper_id
                        JOIN server_processing_publications pub
                          ON pub.owner_user_id=ec.owner_user_id
                         AND pub.paper_id=ec.paper_id
                         AND pub.server_version_id=ec.server_version_id
                         AND pub.publication_id=ec.publication_id
                         AND pub.parse_revision_id=ec.parse_revision_id
                        JOIN server_parse_revisions pr
                          ON pr.owner_user_id=pub.owner_user_id
                         AND pr.parse_revision_id=pub.parse_revision_id AND pr.status='ready'
                        JOIN server_translation_revisions tr
                          ON tr.owner_user_id=pub.owner_user_id
                         AND tr.translation_revision_id=pub.translation_revision_id AND tr.status='ready'
                        JOIN server_embedding_sets es
                          ON es.owner_user_id=pub.owner_user_id
                         AND es.embedding_set_id=pub.embedding_set_id AND es.status='ready'
                        JOIN server_processing_chunks c
                          ON c.owner_user_id=pub.owner_user_id
                         AND c.paper_id=pub.paper_id
                         AND c.server_version_id=pub.server_version_id
                         AND c.parse_revision_id=pub.parse_revision_id
                        JOIN server_translation_chunks tc
                          ON tc.owner_user_id=pub.owner_user_id
                         AND tc.translation_revision_id=pub.translation_revision_id
                         AND tc.chunk_id=c.chunk_id
                        WHERE ec.owner_user_id=%s AND ec.context_id=%s
                          AND ec.paper_id=%s AND ec.server_version_id=%s
                          AND ec.publication_id=%s AND ec.parse_revision_id=%s
                          AND pub.translation_revision_id=%s AND pub.embedding_set_id=%s
                          AND pub.publication_policy=%s AND p.deleted_at IS NULL
                          AND EXISTS (SELECT 1 FROM {vector_table} ev
                               WHERE ev.owner_user_id=pub.owner_user_id
                                 AND ev.embedding_set_id=pub.embedding_set_id
                                 AND ev.chunk_id=c.chunk_id)
                          AND {predicate}
                        ORDER BY c.ordinal,c.chunk_id
                        FOR SHARE OF p,pub,pr,tr,es""",
                    (
                        binding.owner_user_id,
                        binding.context_id,
                        binding.paper_id,
                        binding.server_version_id,
                        binding.publication_id,
                        binding.parse_revision_id,
                        binding.translation_revision_id,
                        binding.embedding_set_id,
                        binding.publication_policy,
                        *tail,
                    ),
                ).fetchall()
            return tuple(_source(binding, row) for row in rows)
        except RemoteRetrievalContractError:
            raise
        except Exception:
            raise RemoteRetrievalContractError("REMOTE_SOURCE_UNAVAILABLE") from None


def _validate(binding, values):
    if (
        not isinstance(binding, RemoteSearchBinding)
        or not isinstance(values, tuple)
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or len(set(values)) != len(values)
    ):
        raise RemoteRetrievalContractError("REMOTE_SOURCE_INPUT_INVALID")


def _source(binding, row):
    try:
        return RemoteSourceChunk(
            binding,
            str(row["chunk_id"]),
            int(row["ordinal"]),
            int(row["pdf_page"]),
            None if row["section_id"] is None else str(row["section_id"]),
            row["original_text"],
            row["translated_text"],
            translation_status=row.get('translation_status','translated'),
        )
    except Exception:
        raise RemoteRetrievalContractError("REMOTE_SOURCE_INVALID") from None
