"""설정 유효성과 제한 오류 의미를 외부 전송 없이 확인한다."""
from unittest.mock import Mock
import pytest
from solo_leveling.infrastructure.generation.execution_budget import ExecutionBudget
from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded, ProviderDispatch
from solo_leveling.application.provider_dispatch import DispatchIntent
INTENT=DispatchIntent('alice','execution','lease','input','storage','ai','notice')


@pytest.mark.parametrize('value', [0,-1,True,1.5,None])
def test_budget_rejects_invalid_limits(value):
    with pytest.raises(ValueError):
        ExecutionBudget(value,100,100,60)


def test_budget_error_does_not_become_insufficient_or_provider_failure():
    ledger=Mock(); ledger.authorize.side_effect=DispatchBudgetExceeded('time')
    send=Mock(); gate=ProviderDispatch(ledger,INTENT)
    with pytest.raises(DispatchBudgetExceeded,match='DISPATCH_TIME_BUDGET_EXCEEDED'):
        gate.send('generation',10,10,send,dispatch_key='one')
    send.assert_not_called()
