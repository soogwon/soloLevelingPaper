"""명시적으로 활성화한 질문 검토 설정만 읽는다. 정책 기본값이나 API 호출은 없다."""
import json
import os
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.evidence_qa.remote_input import CONFIG_KEYS,_unique,_reject_constant


def load_question_settings(env=None):
    env=os.environ if env is None else env
    enabled=env.get('REMOTE_QUESTIONS_ENABLED','false')
    if enabled not in ('true','false'):raise ValueError('question_settings_invalid')
    if enabled=='false':return None
    try:
        raw=env['REMOTE_QUESTION_SETTINGS_JSON']
        if not isinstance(raw,str) or len(raw)>16384:raise ValueError()
        data=json.loads(raw,object_pairs_hook=_unique,parse_constant=_reject_constant)
        if set(data)-{'notice_version'}!={'policy','processing_config','window_key','review_seconds','web_origin'}:raise ValueError()
        data['policy']=RemoteExecutionPolicy(**data['policy'])
        config=data['processing_config']
        if (not isinstance(config,dict) or set(config)!=CONFIG_KEYS
                or any(not isinstance(v,str) or not v.strip() for v in config.values())
                or config['budget_policy_version']!=data['policy'].version):raise ValueError()
        from .remote_question_reviews import NOTICE, validate_notice
        validate_notice(data.get('notice_version', NOTICE), data['policy'], config)
        return data
    except (KeyError,TypeError,ValueError):
        raise ValueError('question_settings_invalid') from None
