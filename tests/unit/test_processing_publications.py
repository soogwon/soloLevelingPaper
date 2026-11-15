from contextlib import nullcontext

import pytest

from solo_leveling.interfaces.http.processing_publications import (
    PostgresProcessingPublicationStore,
    ProcessingPublicationNotFound,
    ProcessingPublicationNotReady,
)


class Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class Connection:
    def __init__(self, revision_row=None, active_row=None):
        self.revision_row = revision_row
        self.active_row = active_row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FOR UPDATE OF v, pr, tr, es, j" in sql:
            return Result(self.revision_row)
        if sql.startswith("UPDATE server_processing_versions"):
            return Result({"server_version_id": params[3]})
        if "JOIN server_processing_publications p" in sql:
            return Result(self.active_row)
        return Result()


class Pool:
    def __init__(self, connection):
        self.value = connection

    def connection(self):
        return nullcontext(self.value)


def ready_row(*, omissions=(), translated=2, indexed=2):
    return {
        "parse_status": "ready",
        "chunk_count": 2,
        "translation_status": "ready",
        "source_chunk_count": 2,
        "translated_chunk_count": translated,
        "omissions": list(omissions),
        "embedding_status": "ready",
        "indexed_chunk_count": indexed,
        "actual_chunk_count": 2,
        "actual_translation_count": translated,
        "matched_omission_count": len(omissions),
    }


def test_publishes_complete_revision_set_and_scopes_every_statement_to_owner():
    conn = Connection(ready_row())
    store = PostgresProcessingPublicationStore(Pool(conn))

    result = store.publish("user-a", "paper", "v1", "parse", "translation", "embedding",
                           "job", "lease",
                           policy="complete")

    assert result.manifest == {
        "chunk_count": 2,
        "translated_chunk_count": 2,
        "indexed_chunk_count": 2,
        "omissions": [],
        "policy": "complete",
    }
    select_params = conn.calls[0][1]
    assert select_params == (
        "user-a", "paper", "v1", "parse", "translation", "embedding", "job", "lease"
    )
    insert = next(call for call in conn.calls if call[0].startswith("INSERT INTO server_processing_publications"))
    update = next(call for call in conn.calls if call[0].startswith("UPDATE server_processing_versions"))
    assert insert[1][1:4] == ("user-a", "paper", "v1")
    assert update[1][1:] == ("user-a", "paper", "v1")


def test_partial_publication_requires_explicit_unique_omissions():
    omission = {"chunk_id": "chunk-2", "reason": "translation_failed"}
    store = PostgresProcessingPublicationStore(Pool(Connection(
        ready_row(omissions=(omission,), translated=1, indexed=1)
    )))

    result = store.publish("user-a", "paper", "v1", "parse", "translation", "embedding",
                           "job", "lease",
                           policy="partial")

    assert result.manifest["omissions"] == [omission]

    duplicate = (omission, omission)
    with pytest.raises(ProcessingPublicationNotReady, match="incomplete|duplicate"):
        PostgresProcessingPublicationStore(Pool(Connection(
            ready_row(omissions=duplicate, translated=0, indexed=0)
        ))).publish("user-a", "paper", "v1", "parse", "translation", "embedding",
                    "job", "lease",
                    policy="partial")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"parse_status": "failed"}, "not_ready"),
        ({"source_chunk_count": 1}, "source_count_mismatch"),
        ({"actual_chunk_count": 1}, "parse_chunk_count_mismatch"),
        ({"actual_translation_count": 1}, "translation_chunk_count_mismatch"),
        ({"translated_chunk_count": 1, "actual_translation_count": 1}, "manifest_incomplete"),
        ({"indexed_chunk_count": 1}, "embedding_count_mismatch"),
    ],
)
def test_rejects_incomplete_or_failed_revision_sets(changes, reason):
    row = ready_row()
    row.update(changes)
    store = PostgresProcessingPublicationStore(Pool(Connection(row)))

    with pytest.raises(ProcessingPublicationNotReady, match=reason):
        store.publish("user-a", "paper", "v1", "parse", "translation", "embedding",
                      "job", "lease",
                      policy="complete")


def test_unknown_or_other_owner_revision_set_is_not_found_without_detail():
    store = PostgresProcessingPublicationStore(Pool(Connection(None)))

    with pytest.raises(ProcessingPublicationNotFound, match="not_found"):
        store.publish("user-b", "paper", "v1", "parse", "translation", "embedding",
                      "job", "lease",
                      policy="complete")


def test_get_active_requires_owner_and_returns_immutable_revision_ids():
    row = {
        "publication_id": "publication",
        "paper_id": "paper",
        "server_version_id": "v1",
        "parse_revision_id": "parse",
        "translation_revision_id": "translation",
        "embedding_set_id": "embedding",
        "publication_policy": "complete",
        "manifest": {"policy": "complete"},
    }
    conn = Connection(active_row=row)

    result = PostgresProcessingPublicationStore(Pool(conn)).get_active("user-a", "paper", "v1")

    assert result.publication_id == "publication"
    assert conn.calls[0][1] == ("user-a", "paper", "v1")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # 없는 chunk나 이미 번역된 chunk를 누락으로 적으면 개수는 맞아도 거부한다.
        ({"matched_omission_count": 0}, "translation_omission_mismatch"),
        ({"omissions": ["chunk-2"]}, "translation_omission_invalid"),
    ],
)
def test_partial_publication_requires_omissions_to_match_untranslated_chunks(changes, reason):
    row = ready_row(
        omissions=({"chunk_id": "chunk-2", "reason": "translation_failed"},),
        translated=1,
        indexed=1,
    )
    row.update(changes)

    with pytest.raises(ProcessingPublicationNotReady, match=reason):
        PostgresProcessingPublicationStore(Pool(Connection(row))).publish(
            "user-a", "paper", "v1", "parse", "translation", "embedding", "job", "lease",
            policy="partial",
        )


def test_partial_publication_without_any_translation_is_rejected():
    omissions = tuple({"chunk_id": f"chunk-{n}", "reason": "translation_failed"} for n in (1, 2))

    with pytest.raises(ProcessingPublicationNotReady, match="has_no_translation"):
        PostgresProcessingPublicationStore(Pool(Connection(
            ready_row(omissions=omissions, translated=0, indexed=0)
        ))).publish(
            "user-a", "paper", "v1", "parse", "translation", "embedding", "job", "lease",
            policy="partial",
        )


def test_omission_match_is_counted_inside_the_owner_scoped_revision():
    conn = Connection(ready_row())

    PostgresProcessingPublicationStore(Pool(conn)).publish(
        "user-a", "paper", "v1", "parse", "translation", "embedding", "job", "lease",
        policy="complete",
    )

    sql = conn.calls[0][0]
    assert "matched_omission_count" in sql
    assert "jsonb_array_elements(tr.omissions)" in sql
