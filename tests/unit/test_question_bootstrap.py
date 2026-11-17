"""질문 생성 설정의 오프라인 검사와 외부 연결 차단·수명 계약."""
from contextlib import contextmanager
from dataclasses import asdict
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from solo_leveling.workers import question_bootstrap as bootstrap
from tests.fixtures.processing_result import SPEC


def raw_config():
    return dict(schema_version=1,database_name='verification',schema_name='isolated',database_role='question',
        policy=dict(version='approved',timeout_seconds=120,stage_timeout_seconds=60,max_calls=1,max_input_tokens=4000,max_output_tokens=1024),
        generation=dict(model='synthetic',timeout_seconds=30,max_output_tokens=1024),
        provider_profile_id='approved-profile',window_key='approved-window',
        runtime_image='synthetic@sha256:'+'a'*64,runtime_lock_path='/synthetic/runtime.json',model_lock_path='/synthetic/model.json',
        model_dir='/synthetic/revision',embedding_spec=asdict(SPEC),
        requests=dict(max_requests=1,max_input_tokens=4000,max_output_tokens=1024,payloads={'a'*64:4000}))


def plan(tmp_path,raw=None):
    path=tmp_path/'question.json';path.write_text(json.dumps(raw or raw_config()))
    return bootstrap.load_plan(path)


def env():
    return dict(QUESTION_WORKER_EXECUTION_ENABLED='true',QUESTION_WORKER_DATABASE_URL='postgresql://question:synthetic@localhost/verification',QUESTION_WORKER_OPENAI_API_KEY='synthetic')


@pytest.mark.parametrize('change',['extra','bool','timeout','calls','output','schema','role','path','image','spec','normalization'])
def test_invalid_config_rejected(tmp_path,change):
    raw=raw_config()
    if change=='extra':raw['api_key']='forbidden'
    elif change=='bool':raw['schema_version']=True
    elif change=='timeout':raw['generation']['timeout_seconds']=61
    elif change=='calls':raw['requests']['max_requests']=2
    elif change=='output':raw['generation']['max_output_tokens']=2048
    elif change=='schema':raw['schema_name']='public,unsafe'
    elif change=='role':raw['database_role']=''
    elif change=='path':raw['model_dir']='relative'
    elif change=='image':raw['runtime_image']='image:latest'
    elif change=='spec':raw['embedding_spec']['dimension']=True
    else:raw['embedding_spec']['normalization']='none'
    with pytest.raises((ValueError,RuntimeError,TypeError)):plan(tmp_path,raw)


def test_preflight_cli_does_not_read_credentials_or_connect(tmp_path,monkeypatch,capsys):
    path=tmp_path/'config.json';path.write_text(json.dumps(raw_config()))
    monkeypatch.setattr(bootstrap,'build',Mock(side_effect=AssertionError('연결 금지')))
    assert bootstrap.main(['--config',str(path)])==0
    assert 'question_config_valid' in capsys.readouterr().out


@pytest.mark.parametrize('change',['disabled','missing','wrong_db','wrong_role','insecure','options'])
def test_invalid_environment_never_opens_pool(tmp_path,change):
    source=env(); create=Mock()
    if change=='disabled':source.pop('QUESTION_WORKER_EXECUTION_ENABLED')
    elif change=='missing':source.pop('QUESTION_WORKER_OPENAI_API_KEY')
    elif change=='wrong_db':source['QUESTION_WORKER_DATABASE_URL']+='other'
    elif change=='wrong_role':source['QUESTION_WORKER_DATABASE_URL']=source['QUESTION_WORKER_DATABASE_URL'].replace('question:','postgres:')
    elif change=='insecure':source['QUESTION_WORKER_DATABASE_URL']=source['QUESTION_WORKER_DATABASE_URL'].replace('localhost','remote.example')
    else:source['QUESTION_WORKER_DATABASE_URL']+='?options=unsafe'
    with pytest.raises(ValueError):
        with bootstrap.build(plan(tmp_path),source,pool_factory=create,check=lambda _:None):pass
    create.assert_not_called()


class Pool:
    def __init__(self,failure=None):self.failure,self.closed=failure,False
    def open(self,**kw):
        if self.failure=='open':raise RuntimeError('비공개 DSN')
    def close(self):self.closed=True
    @contextmanager
    def connection(self):
        def execute(query):
            if 'pg_roles' in query:
                return NS(fetchone=lambda:dict(rolname='question',rolsuper=self.failure=='privilege',rolcreatedb=False,rolcreaterole=False,rolbypassrls=False,rolinherit=False))
            return NS(fetchone=lambda:dict(database='wrong' if self.failure=='identity' else 'verification',schema='isolated'))
        yield NS(execute=execute)


@pytest.mark.parametrize('failure',[None,'open','identity','privilege','embedding','body'])
def test_build_closes_resources_and_never_sends_during_startup(tmp_path,failure):
    pool=Pool(failure);post=Mock()
    def embedding(plan):
        if failure=='embedding':raise RuntimeError('합성 모델 오류')
        return Mock()
    def run():
        with bootstrap.build(plan(tmp_path),env(),pool_factory=lambda *args:pool,check=lambda _:None,embedding_factory=embedding,post=post) as runner:
            assert runner.store._question_policy_version=='approved'
            if failure=='body':raise RuntimeError('합성 오류')
    if failure:
        with pytest.raises((ValueError,RuntimeError)):run()
    else:run()
    assert pool.closed
    post.assert_not_called()


def test_exact_policy_mismatch_blocks_provider_before_request(tmp_path):
    from dataclasses import replace
    pool=Pool();post=Mock()
    p=plan(tmp_path)
    with bootstrap.build(p,env(),pool_factory=lambda *args:pool,check=lambda _:None,embedding_factory=lambda _:Mock(),post=post) as runner:
        with pytest.raises(ValueError):
            runner.execute.execute(Mock(),replace(p.policy,max_calls=2),lambda:True,Mock())
    post.assert_not_called()


def test_duplicate_json_and_nonfinite_values_rejected(tmp_path):
    path=tmp_path/'bad.json'
    path.write_text(json.dumps(raw_config())[:-1]+',"schema_version":1}')
    with pytest.raises(ValueError):bootstrap.load_plan(path)
    raw=raw_config();raw['generation']['timeout_seconds']=float('nan')
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):bootstrap.load_plan(path)


def test_evidence_ids_repeat_only_within_same_execution():
    from uuid import uuid4
    from solo_leveling.workers.question_composition import execution_ids
    execution=str(uuid4())
    first,second,other=execution_ids(execution),execution_ids(execution),execution_ids(str(uuid4()))
    ids=[first() for _ in range(3)]
    assert ids==[second() for _ in range(3)]
    assert len(set(ids))==3 and not set(ids).intersection(other() for _ in range(3))
