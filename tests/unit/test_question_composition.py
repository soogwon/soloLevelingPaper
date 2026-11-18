"""서버 설정 변경이나 실행 허용 상실을 생성 전 차단한다."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.unit.test_remote_input_dispatch import inputs  # noqa: F401
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.semantic_verifier import SemanticSettings
from solo_leveling.workers.question_composition import QuestionExecutionFactory


@pytest.mark.parametrize('change',['model','profile','prompt','disabled','lost','output'])
def test_invalid_configuration_never_opens_database(inputs,change):  # noqa: F811
    pool=Mock()
    factory=QuestionExecutionFactory(pool,generation=OpenAIGenerationSettings('synthetic',allow_external_api=True),
        semantic=SemanticSettings(),provider_profile_id='test',embedding_runtime=Mock(),window_key='test',token_upper_bound=Mock())
    policy=RemoteExecutionPolicy('test',60,10,1,10000,4096)
    config=factory.processing_config('test')
    field={'model':'generation_model','profile':'provider_profile_id','prompt':'generation_prompt_version'}.get(change)
    if field:config[field]='different'
    if change=='disabled':factory.generation=replace(factory.generation,allow_external_api=False)
    if change=='output':policy=replace(policy,max_output_tokens=1)
    request=RemoteQuestionInput.create(*inputs[:4],config)
    intent=NS(owner_user_id=inputs[0].owner_user_id,input_hash=request.input_hash)
    with pytest.raises(DispatchError):factory(intent,policy,lambda:change!='lost',request)
    pool.connection.assert_not_called()


def test_general_runtime_requires_estimator_revision_and_filters_policy():
    from tests.unit.test_question_contract import contract
    from solo_leveling.workers.question_composition import create_question_runner
    c=contract(); pool=Mock()
    options=dict(generation=OpenAIGenerationSettings('synthetic','synthetic',30,1024,True),
        semantic=SemanticSettings(),provider_profile_id=c.profile,embedding_runtime=Mock(),
        window_key=c.window_key,token_upper_bound=lambda *_:4000,contract=c)
    with pytest.raises(ValueError):create_question_runner(pool,**options)
    runner=create_question_runner(pool,**options,estimator_revision=c.estimator_revision)
    assert runner.store._question_policy_version==c.policy_version
    pool.connection.assert_not_called()
