"""명시적으로 승인한 복구 작업만 실행하고 새 결과를 게시 전 원장에 저장한다."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from solo_leveling.interfaces.http.remote_reprocessing import PostgresReprocessingReviews, encoded
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from solo_leveling.domain.translation import TranslationRequest, translation_quality_failure


def require(value):
    if not value:
        raise ValueError('reprocessing_execution_blocked')


@dataclass(frozen=True, repr=False)
class ReprocessingApproval:
    owner: str
    request_key: str
    input_hash: str
    lease_seconds: int = 120
    max_pdf_bytes: int = 20_000_000
    max_pages: int = 500
    max_chunks: int = 10000

    def __post_init__(self):
        import re
        require(isinstance(self.owner, str) and bool(self.owner))
        UUID(self.request_key)
        require(isinstance(self.input_hash, str) and re.fullmatch('[0-9a-f]{64}', self.input_hash))
        require(type(self.lease_seconds) is int and 10 <= self.lease_seconds <= 3600)
        require(all(type(x) is int and x > 0 for x in (self.max_pdf_bytes,self.max_pages,self.max_chunks)))


class ReprocessingWorker:
    """큐 전체를 자동 소비하지 않고 승인된 owner/key/hash 한 건만 소비한다."""
    def __init__(self, reviews, approval, stages, *, execution_enabled=False):
        self.reviews, self.pool, self.approval, self.stages = reviews, reviews.pool, approval, stages
        self.enabled = execution_enabled is True
        self.token = None

    def _row(self, conn):
        return conn.execute('''SELECT *,clock_timestamp()<expires_at AS active,
            lease_until>clock_timestamp() AS lease_active FROM server_reprocessing_reviews
            WHERE owner_user_id=%s AND request_key=%s FOR UPDATE''',
            (self.approval.owner,self.approval.request_key)).fetchone()

    def _source_allowed(self, conn, row):
        require(row['input_hash']==self.approval.input_hash and row['contract']==self.reviews.contract and row['active'])
        require(sha256(encoded(dict(snapshot=row['snapshot'],contract=row['contract'])).encode()).hexdigest()==row['input_hash'])
        require(self.reviews._states(conn,row)=={'storage':'current','ai_provider':'current'})
        require(self.reviews._snapshot(conn,row['owner_user_id'],str(row['context_id']),row['operation'])==row['snapshot'])

    def _finish(self, conn, row, state, result=None, code=None):
        if row['budget_reserved']:
            require(len(self.reviews._budget(conn,row['owner_user_id'],row['contract']))==2)
            self.reviews._adjust(conn,row['owner_user_id'],row['contract'],-1)
        if state=='staged' and row['charged']:
            require(row['attempts'] and all('input_tokens' in a and 'output_tokens' in a for a in row['attempts']))
            contract=row['contract']
            used_input=sum(a['input_tokens'] for a in row['attempts'])
            used_output=sum(a['output_tokens'] for a in row['attempts'])
            require(used_input<=contract['max_input_tokens'] and used_output<=contract['max_output_tokens'])
            require(len(self.reviews._budget(conn,row['owner_user_id'],contract))==2)
            conn.execute('''UPDATE server_dispatch_budget_limits SET consumed_input_tokens=consumed_input_tokens-%s,
                consumed_output_tokens=consumed_output_tokens-%s WHERE policy_version=%s AND window_key=%s
                AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))''',
                (contract['max_input_tokens']-used_input,contract['max_output_tokens']-used_output,
                 contract['policy_version'],contract['window_key'],row['owner_user_id']))
        conn.execute('''UPDATE server_reprocessing_reviews SET state=%s,budget_reserved=false,
            lease_token=NULL,lease_until=NULL,staged_result=%s::jsonb,failure_code=%s
            WHERE owner_user_id=%s AND request_key=%s''',
            (state,encoded(result) if result is not None else None,code,self.approval.owner,self.approval.request_key))

    def claim(self):
        require(self.enabled)
        with self.pool.connection() as conn:
            row=self._row(conn)
            if row is None or row['state'] not in ('queued','running'):return None
            require(row['input_hash']==self.approval.input_hash and row['contract']==self.reviews.contract)
            if row['state']=='running' and row['lease_active']:return None
            if row['attempts']:
                self._finish(conn,row,'failed',code='provider_result_uncertain')
                return None
            try:
                self._source_allowed(conn,row)
            except Exception:
                self._finish(conn,row,'failed',code='source_or_consent_changed')
                return None
            self.token=str(uuid4())
            conn.execute('''UPDATE server_reprocessing_reviews SET state='running',lease_token=%s,
                lease_until=clock_timestamp()+make_interval(secs=>%s) WHERE owner_user_id=%s AND request_key=%s''',
                (self.token,self.approval.lease_seconds,self.approval.owner,self.approval.request_key))
            return row

    def _owned(self, conn):
        row=self._row(conn)
        require(row and row['state']=='running' and str(row['lease_token'])==self.token and row['lease_active'])
        self._source_allowed(conn,row)
        return row

    def checkpoint(self):
        with self.pool.connection() as conn:
            self._owned(conn)
            conn.execute('''UPDATE server_reprocessing_reviews SET lease_until=clock_timestamp()+make_interval(secs=>%s)
                WHERE owner_user_id=%s AND request_key=%s''',
                (self.approval.lease_seconds,self.approval.owner,self.approval.request_key))

    def dispatch(self, payload_hash, input_upper, output_upper, transmit):
        """물리 전송 전에 전체 승인 상한을 보수적으로 소비 처리한다. 불명확한 전송은 재시도하지 않는다."""
        require(type(input_upper) is int and input_upper>0 and type(output_upper) is int and output_upper>0)
        with self.pool.connection() as conn:
            row=self._owned(conn); contract=row['contract']; attempts=row['attempts']
            control=conn.execute("SELECT dispatch_enabled FROM server_dispatch_controls WHERE control_id='provider_dispatch' FOR SHARE").fetchone()
            require(control and control['dispatch_enabled'])
            require(len(attempts)<contract['max_calls']
                and sum(a['input_upper'] for a in attempts)+input_upper<=contract['max_input_tokens']
                and sum(a['output_upper'] for a in attempts)+output_upper<=contract['max_output_tokens'])
            require(not any(a['payload_hash']==payload_hash for a in attempts))
            if not row['charged']:
                require(row['budget_reserved'] and len(self.reviews._budget(conn,row['owner_user_id'],contract))==2)
                self.reviews._adjust(conn,row['owner_user_id'],contract,-1)
                conn.execute('''UPDATE server_dispatch_budget_limits SET consumed_input_tokens=consumed_input_tokens+%s,
                    consumed_output_tokens=consumed_output_tokens+%s WHERE policy_version=%s AND window_key=%s
                    AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))''',
                    (contract['max_input_tokens'],contract['max_output_tokens'],contract['policy_version'],contract['window_key'],row['owner_user_id']))
            attempts=attempts+[dict(payload_hash=payload_hash,input_upper=input_upper,output_upper=output_upper)]
            conn.execute('''UPDATE server_reprocessing_reviews SET charged=true,budget_reserved=false,attempts=%s::jsonb
                WHERE owner_user_id=%s AND request_key=%s''',(encoded(attempts),self.approval.owner,self.approval.request_key))
        # 전송 직전 취소·철회를 재검사한다. 이미 송신된 요청은 취소할 수 없고 상한은 반환하지 않는다.
        self.checkpoint()
        response=transmit()
        from solo_leveling.application.provider_dispatch import ProviderResponse
        require(isinstance(response,ProviderResponse))
        usage=(response.input_tokens,response.output_tokens)
        require(all(type(x) is int and x>=0 for x in usage))
        excess=(max(0,usage[0]-input_upper),max(0,usage[1]-output_upper))
        with self.pool.connection() as conn:
            current=self._row(conn)
            if current is not None:
                observed=current['attempts']
                for attempt in observed:
                    if attempt['payload_hash']==payload_hash:
                        attempt.update(input_tokens=usage[0],output_tokens=usage[1])
                conn.execute('UPDATE server_reprocessing_reviews SET attempts=%s::jsonb WHERE owner_user_id=%s AND request_key=%s',
                             (encoded(observed),self.approval.owner,self.approval.request_key))
            if any(excess):
                require(len(self.reviews._budget(conn,self.approval.owner,contract))==2)
                conn.execute('''UPDATE server_dispatch_budget_limits SET consumed_input_tokens=consumed_input_tokens+%s,
                    consumed_output_tokens=consumed_output_tokens+%s WHERE policy_version=%s AND window_key=%s
                    AND ((scope_type='owner' AND scope_id=%s) OR (scope_type='service' AND scope_id='service'))''',
                    (*excess,contract['policy_version'],contract['window_key'],self.approval.owner))
        require(not any(excess))
        self.checkpoint()
        return response

    def run_once(self):
        row=self.claim()
        if row is None:return 'idle'
        try:
            result=self.stages(row,self)
            require(type(result) is dict and len(encoded(result).encode())<=32_000_000)
            with self.pool.connection() as conn:
                current=self._owned(conn)
                self._finish(conn,current,'staged',result=result)
            return 'staged'
        except Exception:
            with self.pool.connection() as conn:
                current=self._row(conn)
                if current and current['state']=='running' and str(current['lease_token'])==self.token:
                    self._finish(conn,current,'failed',code='execution_failed')
            return 'blocked'


class ReprocessingStages:
    """재파싱은 로컬 추출만, 번역 복구는 실패/누락 원문만 처리한다. 새 색인은 아직 게시하지 않는다."""
    def __init__(self, pool, objects, translator_factory=None, parser=None):
        self.pool,self.objects,self.translator_factory=pool,objects,translator_factory
        self.parser=parser or ProcessingPdfParser()

    def __call__(self,row,worker):
        worker.checkpoint()
        p=row['snapshot']['preview']; a=worker.approval
        if row['operation']=='reparse':
            with self.pool.connection() as conn:
                upload=conn.execute('''SELECT object_key,file_size,sha256_hex FROM server_pdf_uploads
                    WHERE owner_user_id=%s AND upload_id=%s AND status='ready' AND source_kind='remote' ''',
                    (row['owner_user_id'],row['snapshot']['pdf']['upload_id'])).fetchone()
            require(upload and 0<upload['file_size']<=a.max_pdf_bytes)
            prefix=f"private/files/{sha256(row['owner_user_id'].encode()).hexdigest()[:24]}/{p['paper_id']}/{p['version_id']}/"
            require(upload['object_key'].startswith(prefix))
            pdf=self.objects.read_object(upload['object_key'],expected_size=upload['file_size'])
            require(type(pdf.body) is bytes and len(pdf.body)==int(row['snapshot']['pdf']['file_size'])
                    and pdf.body.startswith(b'%PDF-') and sha256(pdf.body).hexdigest()==row['snapshot']['pdf']['sha256_hex'])
            worker.checkpoint()
            revision=str(uuid4()); parsed=self.parser.parse(pdf.body,revision)
            require(0<parsed.page_count<=a.max_pages and 0<len(parsed.chunks)<=a.max_chunks)
            return dict(schema_version=1,operation='reparse',parse_revision_id=revision,
                source_publication_id=p['publication_id'],chunks=[asdict(c) for c in parsed.chunks],
                sections=[asdict(s) for s in parsed.sections],page_count=parsed.page_count,
                publication_ready=False)
        require(row['operation']=='translation_recovery' and self.translator_factory is not None)
        with self.pool.connection() as conn:
            chunks=conn.execute('''SELECT c.chunk_id,c.original_text,t.translated_text,t.translation_status
                FROM server_processing_chunks c LEFT JOIN server_translation_chunks t
                ON t.owner_user_id=c.owner_user_id AND t.paper_id=c.paper_id AND t.server_version_id=c.server_version_id
                  AND t.parse_revision_id=c.parse_revision_id AND t.chunk_id=c.chunk_id AND t.translation_revision_id=%s
                WHERE c.owner_user_id=%s AND c.paper_id=%s AND c.server_version_id=%s AND c.parse_revision_id=%s
                ORDER BY c.ordinal''',(p['translation_revision_id'],row['owner_user_id'],p['paper_id'],p['version_id'],p['parse_revision_id'])).fetchall()
        require(0<len(chunks)<=a.max_chunks)
        translate=self.translator_factory(worker,row['contract'])
        results=[]; translated={}
        for chunk in chunks:
            worker.checkpoint()
            status=chunk['translation_status']
            text=chunk['translated_text']
            if status is None or status in ('original_only','translation_failed'):
                request=TranslationRequest(str(chunk['chunk_id']),p['parse_revision_id'],chunk['original_text'],recovery_policy='layout-aware-v1')
                if request.original_text not in translated:
                    translated[request.original_text]=translate(request)
                text=translated[request.original_text]
                failure=translation_quality_failure(request.original_text,text) if isinstance(text,str) and text.strip() else 'empty'
                if failure:
                    text=request.original_text;status='original_only'
                else:status='translated'
            results.append(dict(chunk_id=str(chunk['chunk_id']),text=text,status=status))
        return dict(schema_version=1,operation='translation_recovery',parse_revision_id=p['parse_revision_id'],
            translation_revision_id=str(uuid4()),source_publication_id=p['publication_id'],translations=results,
            publication_ready=False)


def create_worker(pool, *, config, approval, objects, translator_factory=None, execution_enabled=False):
    reviews=PostgresReprocessingReviews(pool,config=config)
    return ReprocessingWorker(reviews,approval,ReprocessingStages(pool,objects,translator_factory),
                              execution_enabled=execution_enabled)
