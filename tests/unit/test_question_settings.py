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
