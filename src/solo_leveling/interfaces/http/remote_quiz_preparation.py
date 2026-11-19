"""이미 저장된 답변의 고정 인용만으로 퀴즈 입력을 준비한다. 생성·저장은 하지 않는다."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID

from solo_leveling.application.quiz.remote_contract import RemoteQuizGenerationInput
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.summary.remote_contract import SummarySource
from .remote_evidence_read import PostgresRemoteEvidenceReader
from .remote_retrieval import PostgresRemoteContextResolver


class PostgresQuizPreparation:
    def __init__(self,pool,*,settings,policy):
        keys={'provider','model','prompt_version','language','budget_policy_version'}
        if (type(settings) is not dict or set(settings)!=keys
                or any(type(v) is not str or not v.strip() for v in settings.values())
                or type(policy) is not RemoteExecutionPolicy or policy.max_calls!=1
                or policy.version!=settings['budget_policy_version']):
            raise ValueError('remote_quiz_settings_invalid')
        self.pool,self.settings,self.policy=pool,dict(settings),policy

    def prepare(self,owner,context,evidence_ids,*,step,question_count,question_type):
        context=str(UUID(context))
        if type(evidence_ids) is not tuple or not 1<=len(evidence_ids)<=20:
            raise ValueError('remote_quiz_sources_invalid')
        ids=tuple(str(UUID(e)) for e in evidence_ids)
        if len(set(ids))!=len(ids):
            raise ValueError('remote_quiz_sources_invalid')
        with self.pool.connection() as conn:
            parent=conn.execute('''SELECT p.paper_id FROM server_execution_contexts c JOIN server_papers p
                ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                FOR SHARE OF p''',(owner,context)).fetchone()
            if parent is None:
                return None
            scoped=SimpleNamespace(connection=lambda:nullcontext(conn))
            binding=PostgresRemoteContextResolver(scoped).resolve(owner,context)
            if binding is None:
                return None
            reader=PostgresRemoteEvidenceReader(scoped);sources=[]
            for evidence_id in ids:
                value=reader.get(owner,context,evidence_id)
                if value is None:
                    return None
                expected=dict(paper_id=binding.paper_id,version_id=binding.server_version_id,
                    publication_id=binding.publication_id,parse_revision_id=binding.parse_revision_id,
                    translation_revision_id=binding.translation_revision_id,embedding_set_id=binding.embedding_set_id)
                if any(value[k]!=v for k,v in expected.items()):
                    raise ValueError('remote_quiz_source_binding_changed')
                sources.append(SummarySource(evidence_id,value['chunk_id'],value['pdf_page'],value['section_id'],
                                             value['quote_original'],value['quote_ko']))
            return RemoteQuizGenerationInput.create(binding,tuple(sources),step=step,question_count=question_count,
                question_type=question_type,**self.settings,max_input_tokens=self.policy.max_input_tokens,
                max_output_tokens=self.policy.max_output_tokens)
