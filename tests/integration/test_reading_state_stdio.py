"""실제 MCP 프로세스 재시작·오래된 revision·완료 취소의 기록 보존을 검증한다."""

import asyncio

from tests.integration.test_annotation_stdio import connect, success, rejected
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401


def test_reading_completion_survives_process_restart_and_explicit_reopen(setup_db, tmp_path):
    db, chunks = setup_db
    ctx = publish(db, chunks)

    async def state(session):
        return await success(session, 'get_reading_state', {'paper_id':'paper'})

    async def scenario():
        async with connect(db, tmp_path, 'reading-first') as session:
            initial = await state(session)
            assert initial['status'] == 'not_started'
            # 단순 맥락 조회와 별개로 명시적 start_learning만 reading으로 전이한다.
            await success(session, 'start_learning', {'version_id':'v1'})
            started = await state(session)
            assert started['status'] == 'reading' and started['basis_version_id'] == 'v1'
            saved = await success(session, 'create_annotation', {'request':dict(
                context_id=ctx.context_id, kind='note', body='완료 취소 뒤에도 남는 합성 메모', request_key='keep')})
            complete = dict(paper_id='paper', completed=True, expected_revision=started['revision'], basis_version_id='v1')
            done = await success(session, 'set_reading_completed', complete)
            assert done['status'] == 'completed' and done['action'] == 'complete'
        async with connect(db, tmp_path, 'reading-restart') as session:
            assert await state(session) == done
            await success(session, 'start_learning', {'version_id':'v1'})
            assert await state(session) == done
            # 응답 유실 뒤 같은 완료 요청도 오래된 revision이면 조회를 요구한다.
            await rejected(session, 'set_reading_completed', complete, 'READING_STATE_CONFLICT')
            assert await state(session) == done
            await rejected(session, 'set_reading_completed', dict(
                paper_id='paper', completed=False, expected_revision=started['revision']), 'READING_STATE_CONFLICT')
            reopened = await success(session, 'set_reading_completed', dict(
                paper_id='paper', completed=False, expected_revision=done['revision']))
            assert reopened['status'] == 'reading' and reopened['action'] == 'reopen'
            assert reopened['basis_version_id'] == done['basis_version_id']
        async with connect(db, tmp_path, 'reading-after-reopen') as session:
            assert await state(session) == reopened
            annotation = await success(session, 'get_annotation', {'request':dict(
                context_id=ctx.context_id, annotation_id=saved['annotation']['annotation_id'])})
            assert annotation['annotation'] == saved['annotation']
            await rejected(session, 'get_reading_state', {'paper_id':'missing'}, 'NOT_FOUND')
            assert await state(session) == reopened

    asyncio.run(scenario())
