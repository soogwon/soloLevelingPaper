"""취소 설정은 명시적 별도 자격만 허용한다."""
import pytest
from solo_leveling.interfaces.http.processing_cancel_settings import load_processing_cancel_dsn


def test_disabled_and_separate_role():
    assert load_processing_cancel_dsn({}) is None
    env={'REMOTE_JOB_CANCELLATIONS_ENABLED':'true','REMOTE_MCP_DATABASE_URL':'postgresql://reader:p@localhost/db','REMOTE_JOB_CANCELLATION_DATABASE_URL':'postgresql://cancel:p@localhost/db'}
    assert load_processing_cancel_dsn(env).startswith('postgresql://cancel:')
    for change in ({'REMOTE_JOB_CANCELLATIONS_ENABLED':'yes'}, {'REMOTE_JOB_CANCELLATION_DATABASE_URL':'postgresql://reader:p@localhost/db'}, {'REMOTE_JOB_CANCELLATION_DATABASE_URL':'postgresql://cancel:p@other/db'}, {'DATABASE_URL':'postgresql://cancel:p@localhost/db'}):
        with pytest.raises(ValueError,match='processing_cancel_settings_invalid'):
            load_processing_cancel_dsn({**env,**change})
