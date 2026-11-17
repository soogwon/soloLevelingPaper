"""본문 변조·설정 공유·로그 노출 없이 입력을 복원하는 계약."""
import json
from unittest.mock import Mock
import pytest
from tests.unit.test_remote_input_dispatch import inputs  # noqa: F401
from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
from solo_leveling.application.evidence_qa.remote_input import question_input_hash
from solo_leveling.application.evidence_qa.remote_contract import RemoteRetrievalContractError
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.workers.remote_question import PersistedQuestionExecutor


def test_serialized_copy_keeps_original_question_and_configuration(inputs):  # noqa: F811
    request=RemoteQuestionInput.create(*inputs)
    assert request.input_hash==question_input_hash(*inputs)
    inputs[-1]['generation_model']='changed'
    assert request.unpack()[-1]['generation_model']=='v1'
    assert request.unpack()[1]=='원문 질문\r\n'
    assert '원문 질문' not in repr(request)


@pytest.mark.parametrize('value',['null','[]','{"schema_version":1,"schema_version":1}','{"value":NaN}','x'*262145])
def test_bad_snapshot_uses_fixed_error(value):
    with pytest.raises(RemoteRetrievalContractError,match='^REMOTE_INPUT_INVALID$'):
        RemoteQuestionInput(value)


def test_unknown_fields_and_boolean_schema_rejected(inputs):  # noqa: F811
    request=RemoteQuestionInput.create(*inputs)
    for extra in ({'api_key':'secret'},{'schema_version':True}):
        data=json.loads(request.encoded);data.update(extra)
        with pytest.raises(RemoteRetrievalContractError):RemoteQuestionInput(json.dumps(data))


@pytest.mark.parametrize('available,allowed',[(False,True),(True,False)])
def test_executor_never_calls_generation_without_current_input(available,allowed):
    store,execute=Mock(),Mock()
    store.question_input.return_value=object() if available else None
    with pytest.raises(DispatchError):PersistedQuestionExecutor(store,execute)(object(),object(),lambda:allowed)
    execute.assert_not_called()


def test_executor_uses_restored_input(inputs):  # noqa: F811
    request=RemoteQuestionInput.create(*inputs)
    store,execute=Mock(),Mock()
    store.question_input.return_value=request
    intent,policy,allowed=object(),object(),lambda:True
    PersistedQuestionExecutor(store,execute)(intent,policy,allowed)
    execute.assert_called_once_with(intent,policy,allowed,request)
