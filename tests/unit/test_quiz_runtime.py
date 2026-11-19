"""빈칸 검증과 실행 opt-in·오류 출력의 필수 경계."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from threading import Event
import pytest
from solo_leveling.workers.quiz_validation import validate_cloze,PREFIX,MARKER,CRITERIA,PROFILE
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.interfaces.cli import quiz_worker
from solo_leveling.workers.quiz_runtime import build


def request():
    source=NS(evidence_id='e1',chunk_id='c1',original_text='Attention preserves the notes.',translated_text='어텐션은 메모를 보존합니다.')
    return NS(unpack=lambda:(None,(source,),{'prompt_version':PROFILE}))


def test_cli_requires_execute_and_never_imports_runtime(monkeypatch,capsys):
    factory=Mock();monkeypatch.setattr(quiz_worker,'factory',factory)
    assert quiz_worker.main([])==2
    factory.assert_not_called()
    assert 'execute_required' in capsys.readouterr().out


def test_cli_stops_after_failure_and_hides_raw_error(monkeypatch,capsys):
    @contextmanager
    def fail():
        print('synthetic-secret')
        raise ValueError('synthetic-secret')
        yield
    monkeypatch.setattr(quiz_worker,'factory',fail)
    assert quiz_worker.main(['--execute'])==1
    text=capsys.readouterr().out
    assert 'quiz_worker_failed' in text and 'synthetic-secret' not in text


def test_queue_cancel_is_terminal_and_failure_does_not_retry():
    runner=NS(run_once=Mock(side_effect=['cancelled','finished']))
    result=quiz_worker.run_queue(runner,Event(),max_jobs=2)
    assert result.status=='limit_reached' and result.processed==2
    runner.run_once=Mock(return_value='failed')
    assert quiz_worker.run_queue(runner,Event(),max_jobs=2).status=='failed'
    assert runner.run_once.call_count==1


def test_disabled_build_never_connects():
    pool=Mock()
    with pytest.raises(ValueError):
        with build(None,{},pool_factory=pool):pass
    pool.assert_not_called()


def test_cloze_validator_does_not_claim_free_form_meaning(monkeypatch):
    # 이 단위 검사는 구조 검사를 별도로 두고 근거 복원 규칙에 집중한다.
    monkeypatch.setattr('solo_leveling.workers.quiz_validation.validate_generated_questions',lambda *_:None)
    question=QuizQuestion(question_type='multiple_choice',prompt=PREFIX+'Attention preserves the '+MARKER+'.',
        evidence_ids=('e1',),choices=('notes','models'),correct_index=0,answer='notes',grading_criteria=CRITERIA)
    assert validate_cloze(request(),(question,))
    assert not validate_cloze(request(),(replace(question,prompt=PREFIX+'Attention deletes the '+MARKER+'.'),))
    assert not validate_cloze(request(),(replace(question,choices=('notes',' Notes ')),))
    assert not validate_cloze(request(),(replace(question,grading_criteria='무조건 정답'),))
    assert not validate_cloze(request(),(replace(question,evidence_ids=('foreign',)),))
    short=QuizQuestion(question_type='short_answer',prompt=PREFIX+'Attention preserves the '+MARKER+'.',
        evidence_ids=('e1',),choices=(),correct_index=None,answer='notes',grading_criteria=CRITERIA)
    assert validate_cloze(request(),(short,))


def test_bootstrap_closes_pool_on_wrong_identity():
    plan=NS(raw={'database_role':'quiz_worker','database_name':'test_db','schema_name':'quiz_test'})
    pool=Mock();connection=Mock()
    pool.connection.return_value.__enter__=Mock(return_value=connection)
    pool.connection.return_value.__exit__=Mock(return_value=False)
    connection.execute.return_value.fetchone.return_value={'database':'wrong','schema':'quiz_test'}
    source={'QUIZ_WORKER_EXECUTION_ENABLED':'true','QUIZ_WORKER_DATABASE_URL':'postgresql://quiz_worker:synthetic@127.0.0.1/test_db','QUIZ_WORKER_OPENAI_API_KEY':'synthetic'}
    with pytest.raises(ValueError):
        with build(plan,source,pool_factory=lambda *_:pool):pass
    pool.close.assert_called_once()


def test_bootstrap_rejects_admin_role_flags():
    plan=NS(raw={'database_role':'quiz_worker','database_name':'test_db','schema_name':'quiz_test'})
    pool=Mock();connection=Mock()
    pool.connection.return_value.__enter__=Mock(return_value=connection)
    pool.connection.return_value.__exit__=Mock(return_value=False)
    connection.execute.return_value.fetchone.side_effect=[{'database':'test_db','schema':'quiz_test'},
        dict(rolname='quiz_worker',rolsuper=True,rolcreatedb=False,rolcreaterole=False,rolbypassrls=False,rolinherit=False)]
    source={'QUIZ_WORKER_EXECUTION_ENABLED':'true','QUIZ_WORKER_DATABASE_URL':'postgresql://quiz_worker:synthetic@127.0.0.1/test_db','QUIZ_WORKER_OPENAI_API_KEY':'synthetic'}
    with pytest.raises(ValueError):
        with build(plan,source,pool_factory=lambda *_:pool):pass
    pool.close.assert_called_once()
