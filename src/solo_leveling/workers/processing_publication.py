"""검증된 staging을 정규 테이블·벡터·게시·성공으로 원자 전환한다."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import re
import sys

from solo_leveling.interfaces.http.processing_publications import PostgresProcessingPublicationStore
from solo_leveling.interfaces.http.remote_vectors import EmbeddingWriteScope, PostgresEmbeddingVectorWriter
from solo_leveling.interfaces.http.remote_fragment_vectors import PostgresEmbeddingFragmentWriter
from solo_leveling.workers.processing_authorization import _AUTH_SOURCE, _IDENTITY, _USAGE_BLOCKED, _params
from solo_leveling.workers.processing_staging import _source, _uuid, _encode


class ProcessingPublishError(RuntimeError):
    """본문/SQL/자격 증명을 포함하지 않는 고정 내부 오류."""


class _TransactionPool:
    def __init__(self, conn): self.conn = conn

    @contextmanager
    def connection(self):
        # 기존 저장 포트를 재사용하되 nested commit/새 연결은 만들지 않는다.
        yield self.conn


class PostgresStagedProcessingPublisher:
    def __init__(self, staging):
        self.staging, self.pool = staging, staging.pool

    def current(self, source):
        """처리 시작 시 읽은 pointer를 publish의 비교 값으로 고정한다."""
        try:
            _source(source)
            with self.pool.connection() as conn:
                row = conn.execute('''SELECT v.active_publication_id FROM server_processing_versions v
                    JOIN server_papers p ON p.owner_user_id=v.owner_user_id AND p.paper_id=v.paper_id
                    WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
                      AND p.deleted_at IS NULL''',
                    (source.owner_user_id, source.paper_id, source.version_id)).fetchone()
                if row is None: raise ProcessingPublishError('processing_publication_not_allowed')
                return None if row['active_publication_id'] is None else str(row['active_publication_id'])
        except ProcessingPublishError:
            raise
        except Exception:
            raise ProcessingPublishError('processing_publication_unavailable') from None

    def publish(self, source, payload_sha256, *, expected_active_publication_id):
        try:
            _source(source)
            if (type(payload_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', payload_sha256)
                    or expected_active_publication_id is not None and not _uuid(expected_active_publication_id)):
                raise ProcessingPublishError('processing_publication_invalid')
            with self.pool.connection() as conn:
                # 삭제와 같은 논문 우선 순서. version 잠금으로 pointer 비교부터 전환까지 직렬화한다.
                paper = conn.execute('''SELECT paper_id FROM server_papers
                    WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL FOR SHARE''',
                    (source.owner_user_id, source.paper_id)).fetchone()
                version = conn.execute('''SELECT active_publication_id FROM server_processing_versions
                    WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s FOR UPDATE''',
                    (source.owner_user_id, source.paper_id, source.version_id)).fetchone()
                job = conn.execute('''SELECT status,lease_expires_at FROM server_processing_jobs
                    WHERE owner_user_id=%s AND job_id=%s AND paper_id=%s AND server_version_id=%s FOR UPDATE''',
                    (source.owner_user_id, source.job_id, source.paper_id, source.version_id)).fetchone()
                if paper is None or version is None or job is None:
                    raise ProcessingPublishError('processing_publication_not_allowed')
                tx = _TransactionPool(conn)
                publications = PostgresProcessingPublicationStore(tx)
                previous = conn.execute('''SELECT staging_payload_sha256 FROM server_processing_publications
                    WHERE owner_user_id=%s AND source_job_id=%s''', (source.owner_user_id, source.job_id)).fetchone()
                if previous is not None:
                    if job['status'] != 'succeeded' or previous['staging_payload_sha256'] != payload_sha256:
                        raise ProcessingPublishError('processing_publication_conflict')
                    auth = self._completed_authorization(conn, source)
                else:
                    actual = version['active_publication_id']
                    if (None if actual is None else str(actual)) != expected_active_publication_id:
                        raise ProcessingPublishError('processing_publication_conflict')
                    auth = self.staging._lock(conn, source)
                data = self.staging.read_locked(conn, source, auth)
                if data is None or hashlib.sha256(_encode(data)).hexdigest() != payload_sha256:
                    raise ProcessingPublishError('processing_publication_conflict')
                result = self.staging.restore(data)
                args = (source.owner_user_id, source.paper_id, source.version_id, source.parse_revision_id,
                        source.translation_revision_id, source.embedding_set_id, source.job_id, source.lease_token)
                policy = 'partial' if len(result.embeddings.chunk_ids) < len(result.document.chunks) else 'complete'
                if previous is not None:
                    replay = publications._replay(conn, *args, policy)
                    if replay is None: raise ProcessingPublishError('processing_publication_conflict')
                    return replay
                self._materialize(conn, tx, result)
                self._apply_front_matter(conn, source, result.document.front_matter)
                self.staging._live(conn, source)
                publication = publications.publish(*args, policy=policy)
                self._record_provenance(conn, source, publication.publication_id, payload_sha256)
                valid = conn.execute('SELECT %s>clock_timestamp() AS valid', (job['lease_expires_at'],)).fetchone()
                if valid['valid'] is not True:
                    raise ProcessingPublishError('processing_publication_not_allowed')
                return publication
        except ProcessingPublishError:
            raise
        except Exception:
            raise ProcessingPublishError('processing_publication_unavailable') from None

    @staticmethod
    def _completed_authorization(conn, source):
        # 완료 job의 lease는 지워진다. 원래 세대는 publication과 staging의 일치로 별도 확인한다.
        sql = (_AUTH_SOURCE + _IDENTITY).replace("j.status='running'", "j.status='succeeded'").replace(
            'AND j.lease_token=%s AND j.lease_expires_at>CURRENT_TIMESTAMP', 'AND %s::uuid IS NOT NULL')
        row = conn.execute(sql + ' FOR SHARE OF a,i,u,sb,ab,sc,ac', _params(source)).fetchone()
        if row is None or conn.execute(_USAGE_BLOCKED, (source.owner_user_id, source.job_id)).fetchone() is not None:
            raise ProcessingPublishError('processing_publication_not_allowed')
        return dict(row)

    @staticmethod
    def _apply_front_matter(conn, source, front_matter):
        """PDF 첫 페이지에서 찾은 제목(이후 저자·연도)을 비어 있는 논문 열에만 채운다.

        사용자·로컬 기기가 넣은 값은 덮어쓰지 않는다. 서지 정보는 보조 정보이므로
        권한 부족 등으로 실패해도 savepoint만 되돌리고 게시는 계속한다.
        """
        if front_matter is None:
            return
        authors = (None if front_matter.authors is None
                   else json.dumps(list(front_matter.authors), ensure_ascii=False))
        values = (front_matter.title, authors, front_matter.publication_year)
        try:
            with conn.transaction():
                conn.execute('''UPDATE server_papers SET
                        title=COALESCE(title,%s), authors=COALESCE(authors,%s::jsonb),
                        publication_year=COALESCE(publication_year,%s::integer),
                        revision=revision+1, updated_at=CURRENT_TIMESTAMP
                    WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL
                      AND ((title IS NULL AND %s::text IS NOT NULL)
                        OR (authors IS NULL AND %s::jsonb IS NOT NULL)
                        OR (publication_year IS NULL AND %s::integer IS NOT NULL))''',
                    (*values, source.owner_user_id, source.paper_id, *values))
        except Exception:
            # 본문·SQL·자격 증명은 남기지 않는다. 게시는 파일 이름 표시로 계속한다.
            print(json.dumps({'event': 'processing_front_matter_not_applied'}), file=sys.stderr, flush=True)

    @staticmethod
    def _record_provenance(conn, source, publication_id, digest):
        conn.execute('''UPDATE server_processing_publications SET staging_payload_sha256=%s
            WHERE owner_user_id=%s AND publication_id=%s''', (digest, source.owner_user_id, publication_id))

    @staticmethod
    def _materialize(conn, tx, result, *, reuse_parse=False):
        source = result.source
        prefix = (source.owner_user_id, source.paper_id, source.version_id)
        generation = (source.job_id, source.lease_token)
        size = len(result.document.chunks)
        indexed = set(result.embeddings.chunk_ids)
        omissions = [{'chunk_id': item.chunk_id, 'reason': item.failure_code.value}
                     for item in result.translations if item.chunk_id not in indexed]
        fingerprint = asdict(result.embeddings.spec)
        if result.embeddings.split_policy is not None:
            fingerprint['split_policy'] = result.embeddings.split_policy
        metadata = dict(page_count=result.document.page_count, empty_pages=result.empty_pages,
                        sections=[asdict(s) for s in result.document.sections])
        if result.document.front_matter is not None:
            # 판본별 추출 근거. 논문 열이 이미 채워져 있어도 이 판본에서 찾은 값을 남긴다.
            metadata['front_matter'] = asdict(result.document.front_matter)
        metadata['translation_status_counts'] = {
            status:sum(t.status.value == status for t in result.translations)
            for status in sorted({t.status.value for t in result.translations})}
        if not reuse_parse:
            conn.execute('''INSERT INTO server_parse_revisions
                (owner_user_id,paper_id,server_version_id,parse_revision_id,status,chunk_count,
                 source_job_id,source_lease_token,document_metadata)
                VALUES (%s,%s,%s,%s,'building',%s,%s,%s,%s::jsonb)''',
                (*prefix, source.parse_revision_id, size, *generation, json.dumps(metadata)))
        conn.execute('''INSERT INTO server_translation_revisions
            (owner_user_id,paper_id,server_version_id,parse_revision_id,translation_revision_id,status,
             source_chunk_count,translated_chunk_count,source_job_id,source_lease_token,translation_settings,omissions)
            VALUES (%s,%s,%s,%s,%s,'building',%s,%s,%s,%s,%s::jsonb,%s::jsonb)''',
            (*prefix, source.parse_revision_id, source.translation_revision_id, size, len(indexed), *generation,
             json.dumps(asdict(result.translation_settings)),json.dumps(omissions)))
        conn.execute('''INSERT INTO server_embedding_sets
            (owner_user_id,paper_id,server_version_id,parse_revision_id,translation_revision_id,embedding_set_id,
             status,indexed_chunk_count,model_fingerprint,source_job_id,source_lease_token)
            VALUES (%s,%s,%s,%s,%s,%s,'building',%s,%s::jsonb,%s,%s)''',
            (*prefix, source.parse_revision_id, source.translation_revision_id, source.embedding_set_id,
             len(indexed), json.dumps(fingerprint), *generation))
        writer = PostgresEmbeddingVectorWriter(tx)
        scope = EmbeddingWriteScope(*prefix, source.parse_revision_id, source.translation_revision_id,
                                    source.embedding_set_id, result.embeddings.spec.dimension)
        vectors = {} if result.embeddings.fragments else dict(zip(result.embeddings.chunk_ids, result.embeddings.vectors))
        for chunk, translation in zip(result.document.chunks, result.translations):
            if not reuse_parse:
                conn.execute('''INSERT INTO server_processing_chunks
                    (owner_user_id,paper_id,server_version_id,parse_revision_id,chunk_id,ordinal,pdf_page,
                     original_text,section_id,printed_page_label) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (*prefix, source.parse_revision_id, chunk.chunk_id, chunk.chunk_index, chunk.pdf_page,
                     chunk.original_text, chunk.section_id, chunk.printed_page_label))
            if chunk.chunk_id not in indexed:
                continue
            conn.execute('''INSERT INTO server_translation_chunks
                (owner_user_id,paper_id,server_version_id,parse_revision_id,translation_revision_id,chunk_id,
                 translated_text,translation_status,translation_failure_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (*prefix, source.parse_revision_id, source.translation_revision_id, chunk.chunk_id,
                 translation.text, translation.status.value,
                 None if translation.failure_code is None else translation.failure_code.value))
            if vectors:
                writer.put(scope, chunk.chunk_id, vectors[chunk.chunk_id])
        table = 'server_embedding_vectors'
        expected_count = len(indexed)
        if result.embeddings.fragments:
            fragments = PostgresEmbeddingFragmentWriter(tx)
            for fragment, vector in zip(result.embeddings.fragments, result.embeddings.vectors):
                fragments.put(scope, fragment, fragment.parent_token_count, vector)
            table = 'server_embedding_fragments'
            expected_count = len(result.embeddings.fragments)
        actual = conn.execute(f'''SELECT count(*) AS n FROM {table}
            WHERE owner_user_id=%s AND embedding_set_id=%s AND dimension=%s''',
            (source.owner_user_id, source.embedding_set_id, scope.dimension)).fetchone()
        if actual['n'] != expected_count: raise ProcessingPublishError('processing_publication_vectors_incomplete')
        for table, key, value in (
            ('server_parse_revisions', 'parse_revision_id', source.parse_revision_id),
            ('server_translation_revisions', 'translation_revision_id', source.translation_revision_id),
            ('server_embedding_sets', 'embedding_set_id', source.embedding_set_id),
        ):
            if reuse_parse and table=='server_parse_revisions':continue
            conn.execute(f'''UPDATE {table} SET status='ready',completed_at=CURRENT_TIMESTAMP
                WHERE owner_user_id=%s AND {key}=%s''', (source.owner_user_id, value))
