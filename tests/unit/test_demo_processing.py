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


def unbounded_settings():
    raw = active_settings()
    for key in ('starts_at', 'expires_at', 'max_jobs'):
        raw.pop(key)
    return raw


def test_window_and_job_count_are_optional():
    budget = DemoBudget(unbounded_settings(), 'fixed-model', 50)
    assert budget.starts_at is None and budget.expires_at is None and budget.max_jobs is None
    assert budget.active()
    assert budget.allows_limits([{'scope_type': 'owner', 'max_input_tokens': 400, 'max_output_tokens': 50}])


@pytest.mark.parametrize('change', [
    {'starts_at': '2026-11-21T00:00:00+09:00'},
    {'expires_at': '2026-11-30T00:00:00+09:00'},
    {'max_jobs': 0}, {'max_jobs': 10001}, {'max_jobs': '5'}, {'unknown': 1},
])
def test_partial_or_invalid_optional_fields_rejected(change):
    with pytest.raises(ValueError, match='demo_budget_invalid'):
        DemoBudget({**unbounded_settings(), **change}, 'fixed-model', 50)


def test_continuous_run_skips_failed_job_and_keeps_consuming():
    from solo_leveling.workers.demo_processing import run_continuously
    from solo_leveling.workers.processing_queue import QueueRunResult

    outcomes = [QueueRunResult('failed', 1), QueueRunResult('limit_reached', 3), QueueRunResult('stopped', 2)]
    waits = []

    class Stop:
        def is_set(self): return False
        def wait(self, seconds):
            waits.append(seconds)
            return False

    def queue(worker, stop, *, max_jobs, settle_failed):
        assert max_jobs == 10000 and settle_failed == 'settle'
        return outcomes.pop(0)

    result = run_continuously(object(), Stop(), settle_failed='settle', queue=queue)
    assert result == QueueRunResult('stopped', 6)
    assert waits == [30.0] and not outcomes


def test_continuous_run_stops_during_failure_pause():
    from solo_leveling.workers.demo_processing import run_continuously
    from solo_leveling.workers.processing_queue import QueueRunResult

    class Stop:
        def is_set(self): return False
        def wait(self, seconds): return True

    calls = []
    def queue(*args, **kwargs):
        calls.append(1)
        return QueueRunResult('failed', 0)

    assert run_continuously(object(), Stop(), settle_failed=None, queue=queue).status == 'stopped'
    assert len(calls) == 1


@pytest.mark.parametrize('error, code', [
    (ValueError('demo_budget_invalid'), 3), (ValueError('demo_config_invalid'), 3),
    (ValueError('automatic_pdf_policy_mismatch'), 3), (KeyError('SLP_DEMO_DATABASE_URL'), 3),
    (ValueError('demo_processing_disabled'), 4), (ValueError('demo_credentials_invalid'), 5),
    (ValueError('SECRET postgres://user:pw@host'), 1), (RuntimeError('boom'), 1),
])
def test_exit_code_names_reason_without_details(error, code):
    from solo_leveling.workers.demo_processing import exit_code
    assert exit_code(error) == code

