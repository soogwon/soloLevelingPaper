"""일반 질문 계약은 실제 모델·API 없이 합성 본문과 단가로 검증한다."""
from dataclasses import replace
import json
import pytest
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.infrastructure.generation.openai_generator import _INSTRUCTIONS, _claim_schema
from solo_leveling.workers.question_contract import QuestionContract


def contract():
    return QuestionContract('synthetic', 'test', 'window', 'counter-v1', 'price-v1',
        4000, 1024, 50000, 1, 2, 6048, 60480, 604800)


def payload(question='합성 질문'):
    row = dict(evidence_id='e1', text_ko='근거', original_text='Evidence.',
               starts_mid_sentence=False, ends_mid_sentence=False, follows_evidence_id=None)
    return dict(model='synthetic', instructions=_INSTRUCTIONS,
        input=[dict(role='user', content=json.dumps(dict(question=question, evidence=[row])))],
        text={'format': {'type':'json_schema','name':'evidence_claims','strict':True,'schema':_claim_schema(['e1'])}},
        max_output_tokens=1024, store=False)


@pytest.mark.parametrize('question', ['한국어 질문', 'English mixed 질문', '새로운 질문 👋'])
def test_general_questions_do_not_require_payload_hash(question):
    c = contract()
    c.validate_payload('generation', payload(question))
    c.validate_upper(4000)
    c.validate_policy(RemoteExecutionPolicy('test',120,60,1,4000,1024), 'synthetic','window',c.profile,False)


@pytest.mark.parametrize('change', ['model','store','tool','instructions','schema','metadata','duplicate','repair'])
def test_extra_transmission_and_unapproved_stage_rejected(change):
    p = payload(); stage = 'generation'
    if change == 'model':p['model']='other'
    if change == 'store':p['store']=True
    if change == 'tool':p['tools']=[]
    if change == 'instructions':p['instructions']='changed'
    if change == 'schema':p['text']['format']['schema']={}
    if change == 'metadata':
        data=json.loads(p['input'][0]['content']);data['owner']='private';p['input'][0]['content']=json.dumps(data)
    if change == 'duplicate':p['input'][0]['content']='{"question":"a","question":"b","evidence":[]}'
    if change == 'repair':stage='repair'
    with pytest.raises(DispatchError):contract().validate_payload(stage,p)


@pytest.mark.parametrize('upper', [True, 0, 4001, float('nan')])
def test_invalid_token_upper_rejected(upper):
    with pytest.raises(DispatchError):contract().validate_upper(upper)


def test_pinned_prices_budget_and_profile():
    c=contract()
    assert replace(c,price_revision='new').profile!=c.profile
    assert replace(c,estimator_revision='new').profile!=c.profile
    with pytest.raises(DispatchError):replace(c,question_usd_nanos=6047)
    with pytest.raises(DispatchError):replace(c,input_token_usd_nanos=True)
    rows=[dict(scope_type='owner',max_input_tokens=40000,max_output_tokens=10240),
          dict(scope_type='service',max_input_tokens=400000,max_output_tokens=102400)]
    assert c.validate_limits(rows)
    rows[0]['max_input_tokens']+=1
    with pytest.raises(DispatchError):c.validate_limits(rows)


def test_payload_size_and_policy_cannot_silently_expand():
    c=contract()
    with pytest.raises(DispatchError):replace(c,max_payload_bytes=1).validate_payload('generation',payload())
    with pytest.raises(DispatchError):c.validate_policy(RemoteExecutionPolicy('test',120,60,2,4000,1024),'synthetic','window',c.profile,False)


def test_prompt_changes_require_new_profile(monkeypatch):
    from solo_leveling.workers import question_contract
    c=contract(); previous=c.profile
    monkeypatch.setattr(question_contract, '_INSTRUCTIONS', '변경된 합성 프롬프트')
    assert c.profile != previous
