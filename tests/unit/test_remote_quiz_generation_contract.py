"""외부 호출 없이 고정 입력·동의 목적·문항 구조를 검증한다."""
from dataclasses import replace,asdict
from datetime import datetime,timedelta,timezone
import json
import pytest
from tests.unit.summary.test_remote_contract import BINDING,SOURCE
from solo_leveling.application.quiz.remote_contract import (
    RemoteQuizGenerationInput,QuizGenerationConsent,NOTICE,require_generation_consents,validate_generated_questions,
)
NOW=datetime(2026,1,1,tzinfo=timezone.utc)


def make(**changes):
    return RemoteQuizGenerationInput.create(**(dict(binding=BINDING,sources=(SOURCE,),step='preview',
        question_count=1,question_type='multiple_choice',provider='synthetic',model='fixed',prompt_version='v1',
        language='ko',budget_policy_version='v1',max_input_tokens=4000,max_output_tokens=1024)|changes))


def items():
    return [dict(question_type='multiple_choice',prompt='합성 문항',evidence_ids=['e1'],choices=['가','나'],
                 correct_index=0,answer='가',grading_criteria='')]


def consents(request):
    return tuple(QuizGenerationConsent(BINDING.owner_user_id,BINDING.context_id,request.input_hash,p,NOTICE,
        NOW+timedelta(seconds=1)) for p in ('quiz_generation_storage','quiz_generation_ai'))


def test_roundtrip_notice_and_no_raw_text_in_repr():
    request=make();assert RemoteQuizGenerationInput(json.dumps(json.loads(request.encoded),indent=2)).input_hash==request.input_hash
    notice=request.transfer_notice()
    assert notice['automatic_generation'] is False and notice['max_calls']==1
    assert notice['storage_consent_required'] and notice['ai_consent_required']
    assert SOURCE.original_text not in repr(request)+json.dumps(notice)
    require_generation_consents(request,consents(request),now=NOW)
    result=validate_generated_questions(request,items())
    assert result[0].correct_index==0


@pytest.mark.parametrize('field,value',[('step','review'),('question_count',2),('question_type','short_answer'),
    ('model','other'),('provider','other'),('prompt_version','other'),('language','en'),
    ('budget_policy_version','other'),('max_input_tokens',4001),('max_output_tokens',1025)])
def test_generation_change_changes_consent_hash(field,value):
    assert make(**{field:value}).input_hash!=make().input_hash


@pytest.mark.parametrize('field',list(asdict(BINDING))[:-1])
def test_binding_changes_hash(field):
    assert make(binding=replace(BINDING,**{field:'other'})).input_hash!=make().input_hash


@pytest.mark.parametrize('changes',[{'sources':()},{'sources':(SOURCE,SOURCE)},{'question_count':True},
    {'question_count':0},{'question_count':21},{'max_input_tokens':True},{'max_output_tokens':0},
    {'binding':replace(BINDING,publication_policy='partial')}])
def test_invalid_preparation_rejected(changes):
    with pytest.raises(ValueError):make(**changes)


@pytest.mark.parametrize('field,value',[('purpose','storage'),('purpose','quiz_attempt_storage'),('revoked',True),
    ('owner_user_id','other'),('context_id','other'),('notice_version','remote-summary-v1'),('input_hash','other'),
    ('expires_at',NOW),('expires_at',NOW.replace(tzinfo=None))])
def test_wrong_expired_revoked_consent_rejected(field,value):
    request=make();pair=consents(request)
    with pytest.raises(ValueError):require_generation_consents(request,(replace(pair[0],**{field:value}),pair[1]),now=NOW)


@pytest.mark.parametrize('change',[{'evidence_ids':['foreign']},{'evidence_ids':['e1','e1']},
    {'correct_index':True},{'correct_index':2},{'answer':'나'},{'prompt':''},{'choices':['가','가']},
    {'question_type':'short_answer'},{'extra':'비밀'}, {'prompt':'x'*8193}])
def test_no_partial_acceptance_of_bad_question(change):
    with pytest.raises(ValueError):validate_generated_questions(make(),[items()[0]|change])


def test_count_duplicate_json_and_short_answer_rules():
    request=make()
    with pytest.raises(ValueError):RemoteQuizGenerationInput(request.encoded[:-1]+',"max_calls":1}')
    with pytest.raises(ValueError):validate_generated_questions(request,[])
    with pytest.raises(ValueError):validate_generated_questions(make(question_count=2),items()*2)
    short=items()[0]|dict(question_type='short_answer',choices=[],correct_index=None,answer='합성',grading_criteria='정규화 일치')
    assert validate_generated_questions(make(question_type='short_answer'),[short])[0].answer=='합성'
