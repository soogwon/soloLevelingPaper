import json
import pytest
from solo_leveling.interfaces.http.remote_question_settings import load_question_settings
from solo_leveling.application.evidence_qa.remote_input import CONFIG_KEYS


def test_default_is_disabled_and_no_dsn_needed():
    assert load_question_settings({}) is None


@pytest.mark.parametrize('change',['missing','unknown','version','boolean','duplicate'])
def test_enabled_requires_complete_fixed_policy(change):
    data={'policy':dict(version='v1',timeout_seconds=60,stage_timeout_seconds=10,max_calls=1,max_input_tokens=1000,max_output_tokens=100),
        'processing_config':{k:'v1' for k in CONFIG_KEYS},'window_key':'test','review_seconds':60,'web_origin':'https://service.example'}
    if change=='missing':data.pop('policy')
    if change=='unknown':data['api_key']='synthetic'
    if change=='version':data['processing_config']['budget_policy_version']='v2'
    if change=='boolean':data['policy']['max_calls']=True
    raw=json.dumps(data)
    if change=='duplicate':raw=raw[:-1]+',"window_key":"other"}'
    with pytest.raises(ValueError,match='question_settings_invalid'):
        load_question_settings({'REMOTE_QUESTIONS_ENABLED':'true','REMOTE_QUESTION_SETTINGS_JSON':raw})


@pytest.mark.parametrize('notice,profile,semantic,calls,valid',[
    ('question-count-and-generation-v1','general-question-'+'a'*64,'disabled',1,True),
    ('remote-question-v1','general-question-'+'a'*64,'disabled',1,False),
    ('question-count-and-generation-v1','fixed','disabled',1,False),
    ('question-count-and-generation-v1','general-question-'+'a'*64,'enabled',1,False),
    ('question-count-and-generation-v1','general-question-'+'a'*64,'disabled',2,False),
    ('unknown','fixed','disabled',1,False),
])
def test_count_notice_requires_matching_general_configuration(notice,profile,semantic,calls,valid):
    data={'policy':dict(version='v1',timeout_seconds=60,stage_timeout_seconds=10,max_calls=calls,max_input_tokens=1000,max_output_tokens=100),
        'processing_config':{**{k:'v1' for k in CONFIG_KEYS},'provider_profile_id':profile,'semantic_model':semantic},
        'window_key':'test','review_seconds':60,'web_origin':'https://service.example','notice_version':notice}
    env={'REMOTE_QUESTIONS_ENABLED':'true','REMOTE_QUESTION_SETTINGS_JSON':json.dumps(data)}
    if valid:assert load_question_settings(env)['notice_version']==notice
    else:
        with pytest.raises(ValueError):load_question_settings(env)
