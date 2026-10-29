"""시작 전 취소가 확정된 작업만 interrupted + SERVER_SHUTTING_DOWN으로 기록한다."""
import pytest

from solo_leveling.domain.models import JobStage, JobStatus, Paper, PaperVersion
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db


def _job(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    registration = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v1', 'p', 'hash', 'a', 'a'), 'j1')
    return db, registration.job_id


def test_cancelled_before_start_marks_interrupted_with_code(tmp_path):
    db, job_id = _job(tmp_path)
    assert repo.mark_job_cancelled_before_start(db, job_id) is True
    job = repo.get_job(db, job_id)
    assert job.status == JobStatus.INTERRUPTED
    assert job.limitations == ['SERVER_SHUTTING_DOWN']


def test_second_call_is_noop(tmp_path):
    db, job_id = _job(tmp_path)
    assert repo.mark_job_cancelled_before_start(db, job_id) is True
    assert repo.mark_job_cancelled_before_start(db, job_id) is False
    assert repo.get_job(db, job_id).status == JobStatus.INTERRUPTED


@pytest.mark.parametrize('status', [JobStatus.READY, JobStatus.FAILED, JobStatus.INTERRUPTED])
def test_finished_jobs_are_never_overwritten(tmp_path, status):
    db, job_id = _job(tmp_path)
    repo.update_job_status(db, job_id, status, limitations=['keep'])
    assert repo.mark_job_cancelled_before_start(db, job_id) is False
    job = repo.get_job(db, job_id)
    assert job.status == status and job.limitations == ['keep']


@pytest.mark.parametrize('stage', [JobStage.TRANSLATE, JobStage.INDEX])
def test_job_already_past_parse_is_not_touched(tmp_path, stage):
    db, job_id = _job(tmp_path)
    repo.update_job_status(db, job_id, JobStatus.PROCESSING, stage)
    assert repo.mark_job_cancelled_before_start(db, job_id) is False
    assert repo.get_job(db, job_id).status == JobStatus.PROCESSING


def test_unknown_job_returns_false(tmp_path):
    db, _ = _job(tmp_path)
    assert repo.mark_job_cancelled_before_start(db, 'missing') is False


def test_interrupted_job_lets_registration_start_a_new_job(tmp_path):
    db, job_id = _job(tmp_path)
    repo.mark_job_cancelled_before_start(db, job_id)
    retry = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v2', 'p', 'hash', 'a', 'a'), 'j2')
    assert retry.job_id == 'j2'
