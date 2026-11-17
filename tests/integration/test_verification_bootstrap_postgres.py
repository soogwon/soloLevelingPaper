"""실제 PostgreSQL pool을 bootstrap에서 검사하고 종료 순서를 확인한다."""
# ruff: noqa: F811
from dataclasses import replace
import os
from unittest.mock import Mock

import pytest

from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from tests.unit.test_processing_verification import config
from tests.unit.test_verification_bootstrap import raw_config, secrets
from solo_leveling.workers import verification_bootstrap as bootstrap
import json

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.mark.parametrize('mismatch', [False, True])
def test_bootstrap_checks_real_schema_before_object_client_and_closes(tmp_path, pool, db, mismatch):
    row = db('SELECT current_database() AS db, current_schema() AS schema')[0]
    cfg = replace(config(), database_name=row['db'], schema_name='wrong_schema' if mismatch else row['schema'])
    raw = raw_config()
    raw['scope_sha256'] = bootstrap.scope_digest(cfg)
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(raw))
    values = secrets()
    values['SLP_VERIFY_DATABASE_URL'] = os.environ['SOLO_TEST_POSTGRES_DSN']
    proxy = Mock()
    proxy.connection = pool.connection
    client = Mock()
    client_factory = Mock(return_value=client)
    def run():
        with bootstrap.build(cfg, bootstrap.load_plan(cfg, path), values,
                pool_factory=lambda *_: proxy, client_factory=client_factory, check=lambda _: None) as resources:
            assert resources.pool is proxy
            assert resources.stage_options['token_upper_bound'].calls == 0
    if mismatch:
        with pytest.raises(RuntimeError, match='database_mismatch'): run()
        client_factory.assert_not_called()
    else:
        run()
        client.close.assert_called_once()
    proxy.close.assert_called_once()
