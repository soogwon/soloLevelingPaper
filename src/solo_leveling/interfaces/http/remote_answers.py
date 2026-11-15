"""검증된 원격 답변과 고정 출처 근거를 실행 lease에 원자 저장한다."""

from dataclasses import asdict
import hashlib
import json

from solo_leveling.application.evidence_qa.remote_answer import PreparedRemoteAnswer
from solo_leveling.application.provider_dispatch import DispatchIntent


class RemoteAnswerSaveRejected(ValueError):
    pass


class PostgresRemoteAnswerStore:
    def __init__(self, pool):
        self._pool = pool

    def save(self, intent: DispatchIntent, prepared: PreparedRemoteAnswer) -> dict:
        if not isinstance(intent, DispatchIntent) or not isinstance(prepared, PreparedRemoteAnswer):
            raise ValueError("remote_answer_input_invalid")
        binding = prepared.binding
        response = prepared.response
        if response.context_id != binding.context_id or binding.owner_user_id != intent.owner_user_id:
            raise ValueError("remote_answer_scope_invalid")
        citations = {citation.evidence_id: citation for citation in response.result.citations}
        evidence = {item.evidence_id: item for item in prepared.evidence}
        if len(citations) != len(response.result.citations) or len(evidence) != len(prepared.evidence):
            raise ValueError("remote_answer_evidence_invalid")
        if set(citations) != set(evidence):
            raise ValueError("remote_answer_evidence_invalid")
        for evidence_id, item in evidence.items():
            citation = citations[evidence_id]
            if (
                citation.chunk_id != item.chunk_id
                or citation.quote_original != item.quote_original
                or citation.quote_ko != item.quote_ko
                or citation.version_id != binding.server_version_id
                or citation.parse_revision_id != binding.parse_revision_id
                or citation.translation_revision_id != binding.translation_revision_id
            ):
                raise ValueError("remote_answer_evidence_invalid")
        payload = _payload(prepared)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._pool.connection() as conn:
            locked = conn.execute(
                """SELECT 1 FROM server_remote_executions e
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                   JOIN server_processing_publications pub
                     ON pub.owner_user_id=c.owner_user_id AND pub.publication_id=c.publication_id
                   JOIN server_papers p
                     ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                   WHERE e.owner_user_id=%s AND e.execution_id=%s AND e.state='running'
                     AND e.lease_token=%s AND e.lease_expires_at>clock_timestamp()
                     AND e.input_hash=%s AND e.context_id=%s
                     AND c.paper_id=%s AND c.server_version_id=%s
                     AND c.publication_id=%s AND c.parse_revision_id=%s
                     AND pub.translation_revision_id=%s AND pub.embedding_set_id=%s
                     AND pub.publication_policy=%s AND p.deleted_at IS NULL
                   FOR UPDATE OF e,p""",
                (
                    intent.owner_user_id,
                    intent.execution_id,
                    intent.lease_token,
                    intent.input_hash,
                    binding.context_id,
                    binding.paper_id,
                    binding.server_version_id,
                    binding.publication_id,
                    binding.parse_revision_id,
                    binding.translation_revision_id,
                    binding.embedding_set_id,
                    binding.publication_policy,
                ),
            ).fetchone()
            if locked is None:
                raise RemoteAnswerSaveRejected("remote_answer_lease_or_scope_lost")
            for evidence_id, item in evidence.items():
                citation = citations[evidence_id]
                saved = conn.execute(
                    """INSERT INTO server_processing_evidences
                           (evidence_id,owner_user_id,paper_id,server_version_id,publication_id,
                            parse_revision_id,context_id,chunk_id,quoted_text,execution_id,quote_ko)
                       SELECT %s,c.owner_user_id,c.paper_id,c.server_version_id,%s,
                              c.parse_revision_id,%s,c.chunk_id,%s,%s,%s
                       FROM server_processing_chunks c
                       JOIN server_translation_chunks tc
                         ON tc.owner_user_id=c.owner_user_id
                        AND tc.translation_revision_id=%s AND tc.chunk_id=c.chunk_id
                       WHERE c.owner_user_id=%s AND c.paper_id=%s AND c.server_version_id=%s
                         AND c.parse_revision_id=%s AND c.chunk_id=%s
                         AND position(%s in c.original_text)>0
                         AND (%s::text IS NULL OR position(%s in tc.translated_text)>0)
                       ON CONFLICT DO NOTHING RETURNING evidence_id""",
                    (
                        evidence_id,
                        binding.publication_id,
                        binding.context_id,
                        item.quote_original,
                        intent.execution_id,
                        item.quote_ko,
                        binding.translation_revision_id,
                        intent.owner_user_id,
                        binding.paper_id,
                        binding.server_version_id,
                        binding.parse_revision_id,
                        item.chunk_id,
                        item.quote_original,
                        item.quote_ko,
                        item.quote_ko,
                    ),
                ).fetchone()
                if saved is None:
                    raise RemoteAnswerSaveRejected("remote_answer_source_mismatch")
            completed = conn.execute(
                """UPDATE server_remote_executions
                   SET state='completed',payload_json=%s,payload_sha256=%s,
                       lease_token=NULL,lease_expires_at=NULL,
                       finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND execution_id=%s AND state='running'
                     AND lease_token=%s AND lease_expires_at>clock_timestamp()
                   RETURNING execution_id""",
                (raw, digest, intent.owner_user_id, intent.execution_id, intent.lease_token),
            ).fetchone()
            if completed is None:
                raise RemoteAnswerSaveRejected("remote_answer_lease_lost")
        return payload


def _payload(prepared):
    result = prepared.response.result
    return {
        "context_id": prepared.binding.context_id,
        "verification_level": prepared.response.verification_level.value,
        "status": result.status.value,
        "answer_ko": result.answer_ko,
        "claims": [
            {"claim_id": claim.claim_id, "text": claim.text, "evidence_ids": list(claim.evidence_ids)}
            for claim in result.claims
        ],
        "citations": [asdict(citation) for citation in result.citations],
        "reason_code": None if result.reason_code is None else result.reason_code.value,
        "verification_history": dict(prepared.response.verification_history),
    }
