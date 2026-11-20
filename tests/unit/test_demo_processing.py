from datetime import datetime, timedelta, timezone

import pytest

from solo_leveling.workers.demo_processing import DemoBudget


def active_settings():
    now = datetime.now(timezone.utc)
    return {
        'starts_at': (now - timedelta(minutes=1)).isoformat(),
        'expires_at': (now + timedelta(minutes=1)).isoformat(),
        'max_jobs': 4, 'input_token_limit': 1000, 'input_overhead_tokens': 100,
        'input_microusd_per_million_tokens': 1_000_000,
        'output_microusd_per_million_tokens': 2_000_000,
        'owner_budget_microusd': 500, 'service_budget_microusd': 1000,
    }


def test_cost_bounds_apply_to_both_budget_rows_and_pin_payload():
    budget = DemoBudget(active_settings(), 'fixed-model', 50)
    owner = {'scope_type':'owner','max_input_tokens':400,'max_output_tokens':50}
    service = {'scope_type':'service','max_input_tokens':800,'max_output_tokens':100}
    assert budget.allows_limits([owner, service])
    assert not budget.allows_limits([{**owner,'max_input_tokens':401}, service])
    assert not budget.allows_limits([owner, {**service,'max_output_tokens':101}])
    payload = {'model':'fixed-model','max_output_tokens':50,'input':'합성'}
    assert budget.upper_bound('fixed-model', payload) > 100
    for invalid in ({**payload,'model':'other'}, {**payload,'input':'x' * 1000},
                    {**payload,'max_output_tokens':51}):
        with pytest.raises(ValueError, match='demo_payload_out_of_scope'):
            budget.upper_bound('fixed-model', invalid)


def test_expired_window_denies_dispatch():
    raw = active_settings()
    now = datetime.now(timezone.utc)
    raw['starts_at'] = (now - timedelta(minutes=2)).isoformat()
    raw['expires_at'] = (now - timedelta(minutes=1)).isoformat()
    budget = DemoBudget(raw, 'fixed-model', 50)
    assert not budget.active()
    assert not budget.allows_limits([])
