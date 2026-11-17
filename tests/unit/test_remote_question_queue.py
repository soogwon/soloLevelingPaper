from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from solo_leveling.workers.remote_question import run_question_queue


def test_limit_stops_before_next_claim():
    worker=Mock();worker.run_once.return_value='finished'
    result=run_question_queue(worker,Event(),max_jobs=2)
    assert result.status=='limit_reached' and result.processed==2
    assert worker.run_once.call_count==2


def test_stop_prevents_claim_and_idle_wait_is_interruptible():
    stop=Event();stop.set();worker=Mock()
    assert run_question_queue(worker,stop,max_jobs=1).status=='stopped'
    worker.run_once.assert_not_called()
    stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
    worker.run_once.return_value=None
    assert run_question_queue(worker,stop,max_jobs=1).processed==0
    assert worker.run_once.call_count==1
    stop.wait.assert_called_once_with(5.0)


@pytest.mark.parametrize('failure',[RuntimeError('비공개 내용'),'failed'])
def test_uncertain_failure_never_retries(failure):
    worker=Mock()
    if isinstance(failure,Exception):worker.run_once.side_effect=failure
    else:worker.run_once.return_value=failure
    result=run_question_queue(worker,Event(),max_jobs=9)
    assert result.status in ('failed','not_published')
    assert worker.run_once.call_count==1 and '비공개' not in repr(result)


def test_stop_during_job_allows_current_publication_but_no_next_claim():
    stop=Event();worker=Mock()
    def run():stop.set();return 'finished'
    worker.run_once.side_effect=run
    result=run_question_queue(worker,stop,max_jobs=9)
    assert result.status=='stopped' and result.processed==1
    assert worker.run_once.call_count==1


@pytest.mark.parametrize('jobs,delay',[(0,5),(True,5),(1,0),(1,float('nan')),(1,True)])
def test_invalid_config_never_claims(jobs,delay):
    worker=Mock()
    with pytest.raises(ValueError):run_question_queue(worker,Event(),max_jobs=jobs,idle_seconds=delay)
    worker.run_once.assert_not_called()


@pytest.mark.parametrize('max_jobs', [None, 9])
@pytest.mark.parametrize('failure', ['failed', 'limited', 'unknown', RuntimeError('비공개')])
def test_continuous_and_bounded_fail_closed(max_jobs, failure):
    worker = Mock()
    if isinstance(failure, Exception):
        worker.run_once.side_effect = failure
    else:
        worker.run_once.return_value = failure
    result = run_question_queue(worker, Event(), max_jobs=max_jobs)
    assert result.status == 'failed'
    assert worker.run_once.call_count == 1


def test_continuous_consumes_multiple_jobs_and_drains_on_stop():
    stop = Event()
    statuses = iter(['finished', 'expired', 'blocked', None, 'finished'])
    worker = Mock()
    def run():
        status = next(statuses)
        if worker.run_once.call_count == 5:
            stop.set()
        return status
    worker.run_once.side_effect = run
    result = run_question_queue(worker, stop, max_jobs=None, idle_seconds=0.1)
    assert result.status == 'stopped' and result.processed == 4
    assert worker.run_once.call_count == 5
