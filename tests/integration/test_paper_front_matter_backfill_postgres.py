"""이미 처리된 원격 논문의 빈 서지 정보를 저장된 PDF로 채우는 관리자 명령을 실제 PostgreSQL에서 검증한다."""
# ruff: noqa: F811
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from solo_leveling.interfaces.cli.paper_front_matter_backfill import run_backfill
from solo_leveling.interfaces.http.papers import PostgresPaperStore
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore
from solo_leveling.interfaces.http.remote_upload_consents import PostgresRemoteUploadConsentStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')

PDF = (Path(__file__).resolve().parents[1] / 'test_data' / 'tmp_pdfs'
       / 'c30732f1-f6af-45b0-b8f7-38e5c0869588.pdf').read_bytes()


class Storage:
    def __init__(self, objects):
        self.objects, self.reads = objects, []

    def read_object(self, key, *, expected_size):
        self.reads.append(key)
        body = self.objects[key]
        if len(body) != expected_size:
            raise RuntimeError('object_size_mismatch')
        return SimpleNamespace(body=body, sha256_hex=sha256(body).hexdigest(), pdf_header_valid=body.startswith(b'%PDF-'))


def remote_paper(pool, db, owner, body, key_suffix):
    uploads = PostgresRemoteUploadIntentStore(pool)
    intent = uploads.create(owner, f'key-{key_suffix}', f'{key_suffix}.pdf', len(body), 'application/pdf',
                            sha256(body).hexdigest())
    PostgresRemoteUploadConsentStore(pool).grant(owner, intent.intent_id, 'storage', f'consent-{key_suffix}')
    uploads.reserve_upload(owner, intent.intent_id)
    key = f'private/files/{key_suffix}.pdf'
    uploads.mark_verified(owner, intent.intent_id, key)
    return intent.paper_id, key


def test_preview_then_apply_fills_only_missing_fields(pool, db):
    owner = 'backfill-' + str(uuid4())
    db('INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,%s)', (owner, '합성'))
    attention, attention_key = remote_paper(pool, db, owner, PDF, 'attention')
    titled, titled_key = remote_paper(pool, db, owner, PDF + b'\n', 'titled')
    broken, broken_key = remote_paper(pool, db, owner, b'%PDF-broken', 'broken')
    db("UPDATE server_papers SET title='사용자 제목' WHERE paper_id=%s", (titled,))
    storage = Storage({attention_key: PDF, titled_key: PDF + b'\n', broken_key: b'%PDF-broken'})
    seen = []

    preview = run_backfill(pool, storage, limit=10, apply=False, emit=seen.append)
    assert preview == dict(checked=3, updated=0, would_update=2, not_found=0, unchanged=0, failed=1)
    assert db('SELECT count(*) AS n FROM server_papers WHERE owner_user_id=%s AND authors IS NOT NULL',
              (owner,))[0]['n'] == 0
    assert {item['paper_id']: item['status'] for item in seen}[broken] == 'failed'

    result = run_backfill(pool, storage, limit=10, apply=True)
    assert result == dict(checked=3, updated=2, would_update=0, not_found=0, unchanged=0, failed=1)
    papers = PostgresPaperStore(pool)
    item = papers.get_paper(owner, attention, learning_record_id=None, version_id=None).paper
    assert item.title == 'Attention Is All You Need'
    assert item.authors[0] == 'Ashish Vaswani' and len(item.authors) == 8
    assert item.publication_year == 2017
    assert item.file_name == 'attention.pdf'
    kept = papers.get_paper(owner, titled, learning_record_id=None, version_id=None).paper
    assert kept.title == '사용자 제목' and kept.publication_year == 2017

    # 채운 논문은 다시 후보가 되지 않는다.
    again = run_backfill(pool, storage, limit=10, apply=True)
    assert again == dict(checked=1, updated=0, would_update=0, not_found=0, unchanged=0, failed=1)
