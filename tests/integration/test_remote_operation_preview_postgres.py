"""실제 운영 제어 저장소→C 표시 연결. 제품 endpoint/호스트 검증이 아니다."""
# ruff: noqa: F811
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from solo_leveling.interfaces.http.remote_operation_controls import PostgresRemoteOperationControls
from solo_leveling.interfaces.mcp.remote_operation_preview import render_operation_preview

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


@pytest.mark.parametrize('intake,dispatch', [(False, True), (True, False), (False, False), (True, True)])
def test_persisted_controls_render_without_mutation(pool, db, intake, dispatch):
    db('''INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled,intake_enabled)
          VALUES ('provider_dispatch',1,%s,%s) ON CONFLICT(control_id)
          DO UPDATE SET dispatch_enabled=EXCLUDED.dispatch_enabled,intake_enabled=EXCLUDED.intake_enabled''',
       (dispatch, intake))
    before = db('SELECT * FROM server_dispatch_controls')
    result = render_operation_preview(PostgresRemoteOperationControls(pool), mcp_enabled=True)
    assert ('신규 PDF 처리 접수는 운영 제어상 허용' in result.text) == intake
    assert ('진행 작업의 다음 제공자 전송은 운영 제어상 허용' in result.text) == dispatch
    assert db('SELECT * FROM server_dispatch_controls') == before
