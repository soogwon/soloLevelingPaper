"""복구 저장 결과를 로컬 임베딩·정규 테이블·후보 게시본으로 원자 저장한다."""
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid4

from solo_leveling.domain.remote_processing import (
    ProcessingSource,ProcessingResult,ProcessingLimits,ProcessingChunk,ParsedDocument,ProcessedTranslation,
)
from solo_leveling.domain.models import PaperSection
from solo_leveling.domain.translation import TranslationSettings,TranslationStatus,TranslationFailureCode
from solo_leveling.interfaces.http.processing_publications import PostgresProcessingPublicationStore
from .processing_publication import PostgresStagedProcessingPublisher,_TransactionPool
from .processing_staging import PostgresProcessingResultStaging
from .remote_reprocessing import ReprocessingWorker,require
from solo_leveling.interfaces.http.remote_reprocessing import encoded


class ReprocessingPublisher:
    def __init__(self,reviews,approval,spec,embedder_factory,*,execution_enabled=False):
        self.reviews,self.pool,self.approval,self.spec=reviews,reviews.pool,approval,spec
        self.embedder_factory,self.enabled=embedder_factory,execution_enabled is True
        self.guard=ReprocessingWorker(reviews,approval,None)

    def _check(self,conn):
        row=self.guard._row(conn)
        require(row and row['input_hash']==self.approval.input_hash)
        require(conn.execute('SELECT paper_id FROM server_papers WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL FOR SHARE',
            (self.approval.owner,row['snapshot']['preview']['paper_id'])).fetchone() is not None)
        old=json.loads(row['snapshot']['preview']['model_fingerprint'])
        require({k:v for k,v in old.items() if k!='split_policy'}==asdict(self.spec))
        if row['state']=='published':return row
        require(row['state']=='staged' and type(row['staged_result']) is dict)
        self.guard._source_allowed(conn,row)
        return row

    def allowed(self):
        with self.pool.connection() as conn:
            row=self._check(conn)
            require(row['state']=='staged')
        return True

    def _document(self,conn,row):
        staged=row['staged_result'];p=row['snapshot']['preview']
        require(staged['schema_version']==1 and staged['operation']==row['operation']
                and staged['source_publication_id']==p['publication_id'] and staged['publication_ready'] is False)
        if row['operation']=='reparse':
            document=ParsedDocument(staged['page_count'],tuple(ProcessingChunk(**c) for c in staged['chunks']),
                                    tuple(PaperSection(**s) for s in staged['sections']))
            require(all(c.parse_revision_id==staged['parse_revision_id'] for c in document.chunks))
            translations=tuple(ProcessedTranslation(c.chunk_id,c.original_text,TranslationFailureCode.EMPTY_TRANSLATION,
                               TranslationStatus.ORIGINAL_ONLY) for c in document.chunks)
            return document,translations,staged['parse_revision_id'],str(uuid4())
        require(staged['parse_revision_id']==p['parse_revision_id'])
        rows=conn.execute('''SELECT chunk_id,ordinal,original_text,pdf_page,section_id,printed_page_label
            FROM server_processing_chunks WHERE owner_user_id=%s AND parse_revision_id=%s ORDER BY ordinal''',
            (row['owner_user_id'],p['parse_revision_id'])).fetchall()
        meta=conn.execute('SELECT document_metadata FROM server_parse_revisions WHERE owner_user_id=%s AND parse_revision_id=%s',
                          (row['owner_user_id'],p['parse_revision_id'])).fetchone()['document_metadata']
        chunks=tuple(ProcessingChunk(str(c['chunk_id']),p['parse_revision_id'],c['ordinal'],c['original_text'],
                     c['pdf_page'],c['section_id'],c['printed_page_label']) for c in rows)
        document=ParsedDocument(meta.get('page_count',max(c.pdf_page for c in chunks)),chunks,
                                tuple(PaperSection(**s) for s in meta.get('sections',[])))
        values=staged['translations']
        require(len(values)==len(chunks) and [v['chunk_id'] for v in values]==[c.chunk_id for c in chunks])
        translations=tuple(ProcessedTranslation(v['chunk_id'],v['text'],
            TranslationFailureCode.EMPTY_TRANSLATION if v['status']=='original_only' else None,
            TranslationStatus(v['status'])) for v in values)
        return document,translations,p['parse_revision_id'],staged['translation_revision_id']

    def run_once(self):
        require(self.enabled)
        with self.pool.connection() as conn:
            row=self._check(conn)
            if row['state']=='published':return str(row['publication_id'])
            document,translations,parse,translation=self._document(conn,row)
        digest=sha256(encoded(row['staged_result']).encode()).hexdigest()
        p=row['snapshot']['preview'];pdf=row['snapshot']['pdf'];a=self.approval
        token=str(uuid4());embedding=str(uuid4())
        source=ProcessingSource(a.owner,str(row['job_id']),p['paper_id'],p['version_id'],parse,translation,embedding,
                                pdf['sha256_hex'],int(pdf['file_size']),token)
        settings=TranslationSettings(row['contract']['provider'],row['contract']['model'],'ko-translation-v1')
        limits=ProcessingLimits(a.max_pdf_bytes,a.max_pages,a.max_chunks)
        validator=PostgresProcessingResultStaging(self.pool,limits=limits,translation_settings=settings,
            embedding_spec=self.spec,max_result_bytes=64_000_000)
        # 모델 파일/토크나이저/런타임과 조각 대응 검증은 기존 어댑터·staging 검증기를 사용한다.
        batch=self.embedder_factory(embedding,self.allowed).embed(translations,self.spec)
        require(batch.split_policy is not None)
        present={c.pdf_page for c in document.chunks}
        result=ProcessingResult(source,document,settings,translations,batch,
            tuple(n for n in range(1,document.page_count+1) if n not in present),'prepared')
        _,_,result_digest=validator._payload(result)
        with self.pool.connection() as conn:
            current=self._check(conn)
            if current['state']=='published':return str(current['publication_id'])
            require(sha256(encoded(current['staged_result']).encode()).hexdigest()==digest)
            version=conn.execute('''SELECT active_publication_id FROM server_processing_versions
                WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s FOR UPDATE''',
                (a.owner,p['paper_id'],p['version_id'])).fetchone()
            require(version and str(version['active_publication_id'])==pdf['active_publication_id'])
            # 정규 저장의 기존 lease 제약은 유지한다. 이 embed 작업은 트랜잭션 밖에서 queued로 노출되지 않는다.
            conn.execute('''INSERT INTO server_processing_jobs(job_id,owner_user_id,paper_id,server_version_id,
                job_kind,request_key,payload_hash,status,attempt,max_attempts,retryable,lease_owner,lease_token,lease_expires_at,heartbeat_at)
                VALUES (%s,%s,%s,%s,'embed',%s,%s,'running',1,1,false,'reprocessing-publication',%s,
                    clock_timestamp()+make_interval(secs=>%s),clock_timestamp())''',
                (source.job_id,a.owner,p['paper_id'],p['version_id'],'reprocessing-publication:'+a.request_key,result_digest,token,a.lease_seconds))
            tx=_TransactionPool(conn)
            PostgresStagedProcessingPublisher._materialize(conn,tx,result,reuse_parse=row['operation']=='translation_recovery')
            publication=PostgresProcessingPublicationStore(tx).publish(a.owner,p['paper_id'],p['version_id'],parse,
                translation,embedding,source.job_id,token,policy='complete',activate=False)
            PostgresStagedProcessingPublisher._record_provenance(conn,source,publication.publication_id,result_digest)
            conn.execute('''UPDATE server_reprocessing_reviews SET state='published',publication_id=%s,
                publication_payload_sha256=%s WHERE owner_user_id=%s AND request_key=%s''',
                (publication.publication_id,result_digest,a.owner,a.request_key))
            return publication.publication_id
