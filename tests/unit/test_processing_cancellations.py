"""취소 입력은 저장소 접근 전에 엄격하게 검사한다."""
from unittest.mock import Mock
from uuid import uuid4
import pytest
from solo_leveling.interfaces.http.processing_cancellations import PostgresProcessingCancellationStore


@pytest.mark.parametrize('revision',[True,0,-1,'1',1.0,2**63])
def test_revision_is_strict_positive_bigint(revision):
    pool=Mock()
    with pytest.raises(ValueError,match='argument_invalid'):
        PostgresProcessingCancellationStore(pool).cancel('owner',str(uuid4()),str(uuid4()),revision)
    pool.connection.assert_not_called()


@pytest.mark.parametrize('job,key',[('bad',str(uuid4())),(str(uuid4()),'bad')])
def test_ids_are_validated_before_database(job,key):
    pool=Mock()
    with pytest.raises(ValueError,match='argument_invalid'):
        PostgresProcessingCancellationStore(pool).cancel('owner',job,key,1)
    pool.connection.assert_not_called()
