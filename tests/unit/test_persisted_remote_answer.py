from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.unit.test_remote_answers import prepared, INTENT, BINDING
from solo_leveling.application.evidence_qa.remote_input import CONFIG_KEYS, question_input_hash
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.infrastructure.retrieval.remote_answer_factory import PersistedRemoteAnswerService, RemoteAnswerExecutionError

CONFIG = {key:'synthetic-v1' for key in CONFIG_KEYS}


def setup():
    binding = replace(BINDING, model_fingerprint='{"synthetic":true}')
    value = replace(prepared(), binding=binding)
    intent = replace(INTENT, input_hash=question_input_hash(binding,'질문',RemoteSearchFilters(),5,CONFIG))
    prep, writer = NS(prepare=Mock(return_value=value)), NS(save=Mock(return_value={'saved':True}))
    validate = Mock(return_value=True)
    service = PersistedRemoteAnswerService(lambda _: binding, prep, writer, validate, CONFIG)
    return service, intent, prep, writer, validate, binding


def test_returns_only_committed_payload():
    service,intent,prep,writer,_,_ = setup()
    assert service.execute(intent,'질문','context') == {'saved':True}
    writer.save.assert_called_once_with(intent,prep.prepare.return_value)


@pytest.mark.parametrize('kind', ['hash','owner','lease'])
def test_rejects_before_generation(kind):
    service,intent,prep,writer,validate,_ = setup()
    if kind == 'lease': validate.return_value=False
    else: intent=replace(intent, **({'input_hash':'other'} if kind=='hash' else {'owner_user_id':'other'}))
    with pytest.raises(RemoteAnswerExecutionError): service.execute(intent,'질문','context')
    prep.prepare.assert_not_called(); writer.save.assert_not_called()


def test_save_failure_never_returns_prepared_answer_or_raw_error():
    service,intent,_,writer,_,_ = setup()
    writer.save.side_effect=RuntimeError('private-source-token')
    with pytest.raises(RemoteAnswerExecutionError, match='^REMOTE_ANSWER_SAVE_FAILED$'):
        service.execute(intent,'질문','context')


def test_binding_change_before_save_is_rejected():
    service,intent,prep,writer,_,binding = setup()
    prep.prepare.return_value=replace(prep.prepare.return_value,binding=replace(binding,publication_id='other'))
    with pytest.raises(RemoteAnswerExecutionError): service.execute(intent,'질문','context')
    writer.save.assert_not_called()


@pytest.mark.parametrize('reason', ['time', 'calls_or_tokens'])
def test_provider_budget_limit_is_not_relabelled_as_preparation_failure(reason):
    from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded
    service, intent, prep, writer, _, _ = setup()
    error = DispatchBudgetExceeded(reason)
    prep.prepare.side_effect = error
    with pytest.raises(DispatchBudgetExceeded) as caught:
        service.execute(intent, '질문', 'context')
    assert caught.value is error
    writer.save.assert_not_called()


@pytest.mark.parametrize('code', ['DISPATCH_NOT_AUTHORIZED', 'DISPATCH_USAGE_UNKNOWN',
                                 'DISPATCH_RECONCILIATION_UNAVAILABLE'])
def test_fixed_dispatch_failure_survives_without_saving(code):
    from solo_leveling.application.provider_dispatch import DispatchError
    service, intent, prep, writer, _, _ = setup()
    error = DispatchError(code)
    prep.prepare.side_effect = error
    with pytest.raises(DispatchError) as caught:
        service.execute(intent, '질문', 'context')
    assert caught.value is error and str(caught.value) == code
    writer.save.assert_not_called()


def test_unknown_preparation_error_is_still_sanitized():
    service, intent, prep, writer, _, _ = setup()
    prep.prepare.side_effect = RuntimeError('private-provider-body-and-token')
    with pytest.raises(RemoteAnswerExecutionError, match='^REMOTE_ANSWER_PREPARATION_FAILED$'):
        service.execute(intent, '질문', 'context')
    writer.save.assert_not_called()
