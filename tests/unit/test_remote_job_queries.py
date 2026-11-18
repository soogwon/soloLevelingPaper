"""잘못된 입력과 장애가 내부 정보나 실행으로 이어지지 않는지 확인한다."""
from unittest.mock import Mock
from uuid import uuid4
import pytest
from solo_leveling.interfaces.http.remote_job_queries import RemoteJobQueries


@pytest.mark.parametrize('after,limit', [('bad',20),(None,True),(None,0),(None,51),(None,'20')])
def test_invalid_paging_never_reads_database(after,limit):
    pool, owner = Mock(), Mock()
    assert RemoteJobQueries(pool,owner).list(after,limit) == {'status':'invalid_argument'}
    owner.assert_not_called()
    pool.connection.assert_not_called()


def test_error_and_missing_identity_are_safe():
    pool=Mock()
    pool.connection.side_effect=RuntimeError('secret url token')
    q=RemoteJobQueries(pool,lambda:'owner')
    assert q.get(str(uuid4())) == {'status':'unavailable'}
    assert q.list() == {'status':'unavailable'}
    q.owner=lambda:None
    assert q.get(str(uuid4())) == {'status':'not_found'}
    assert q.get('bad') == {'status':'invalid_argument'}
