"""카드 확인 토큰은 같은 데이터 폴더를 쓰는 MCP 프로세스 사이에서 공유된다.

Claude Desktop은 같은 MCP 서버를 프로세스 두 개로 띄우고, 채팅의 준비 호출과 카드의 승인
호출을 서로 다른 프로세스로 보낼 수 있다(#215 실측: prepare는 pid A, 승인은 pid B).
"""

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.server_sync import SyncResult
from solo_leveling.application.server_sync.paper_metadata import PaperSyncPreview
from solo_leveling.application.server_sync.pdf_upload import PdfUploadPreview, PdfUploadResult
from solo_leveling.interfaces.mcp.confirmations import ConfirmationLimitExceeded, ConfirmationStore
from solo_leveling.interfaces.mcp.pdf_upload_tools import PdfUploadGate
from solo_leveling.interfaces.mcp.registration_consent import RegistrationGate
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.interfaces.mcp.server_sync_tools import ServerSyncGate


def two_processes(tmp_path):
    """같은 파일을 여는 두 저장소 = 같은 DATA_ROOT를 쓰는 두 MCP 프로세스."""
    path = tmp_path / "card-confirmations.sqlite3"
    return ConfirmationStore(path), ConfirmationStore(path)


def pdf_preview():
    return PdfUploadPreview("account", "paper-1", "server-paper-1", "version-1",
                            "paper.pdf", 1234, "ab" * 32, Path("pdfs") / "paper.pdf")


def test_token_issued_in_one_process_is_consumed_once_in_another(tmp_path):
    first, second = two_processes(tmp_path)
    token = first.issue("server_sync", ["paper-1", "op-1"], {"x": 1})
    assert second.take("server_sync", token, ["paper-1", "op-1"]) == (True, {"x": 1})
    assert first.take("server_sync", token, ["paper-1", "op-1"]) == (False, None)
    assert second.take("server_sync", token, ["paper-1", "op-1"]) == (False, None)


def test_token_is_bound_to_kind_and_scope_and_stored_only_as_hash(tmp_path):
    first, second = two_processes(tmp_path)
    token = first.issue("pdf_upload", ["paper-1", "version-1"])
    assert second.take("server_sync", token, ["paper-1", "version-1"]) == (False, None)
    assert second.take("pdf_upload", token, ["paper-1", "version-2"]) == (False, None)
    assert token.encode() not in (tmp_path / "card-confirmations.sqlite3").read_bytes()
    assert second.take("pdf_upload", token, ["paper-1", "version-1"])[0] is True


def test_expiry_cancel_and_limit_use_shared_state(tmp_path):
    now = [1000.0]
    path = tmp_path / "card-confirmations.sqlite3"
    first = ConfirmationStore(path, clock=lambda: now[0])
    second = ConfirmationStore(path, clock=lambda: now[0])
    token = first.issue("registration", ["a.pdf", "r"])
    now[0] += 600
    assert second.take("registration", token, ["a.pdf", "r"]) == (False, None)
    token = first.issue("registration", ["a.pdf", "r"])
    second.cancel("registration", token)
    assert first.take("registration", token, ["a.pdf", "r"]) == (False, None)
    for i in range(3):
        first.issue("registration", ["a.pdf", str(i)], limit=3)
    with pytest.raises(ConfirmationLimitExceeded):
        second.issue("registration", ["a.pdf", "x"], limit=3)
    now[0] += 600
    second.issue("registration", ["a.pdf", "x"], limit=3)
    assert first.pending_count("registration") == 1


def test_token_from_a_real_second_process_is_accepted(tmp_path):
    path = tmp_path / "card-confirmations.sqlite3"
    code = ("import sys; from solo_leveling.interfaces.mcp.confirmations import ConfirmationStore;"
            "print(ConfirmationStore(sys.argv[1]).issue('server_sync', ['paper-1', 'op-1'], {'v': 2}))")
    token = subprocess.run([sys.executable, "-c", code, str(path)], check=True,
                           capture_output=True, text=True).stdout.strip()
    assert ConfirmationStore(path).take("server_sync", token, ["paper-1", "op-1"]) == (True, {"v": 2})


def test_sync_gate_restores_preview_in_other_process(tmp_path):
    first, second = two_processes(tmp_path)
    preview = PaperSyncPreview("account", "paper-1", None, "created", 2, "t", "h", 1, "s", 0, None)
    token = ServerSyncGate(store=first).prepare(preview, "op-1")
    assert ServerSyncGate(store=second).consume(token, "paper-1", "op-1") == preview
    with pytest.raises(ToolError, match="SERVER_SYNC_CONFIRMATION_REQUIRED"):
        ServerSyncGate(store=first).consume(token, "paper-1", "op-1")


def test_pdf_gate_keeps_confirmed_token_for_status_in_any_process(tmp_path):
    first, second = two_processes(tmp_path)
    token = PdfUploadGate(store=first).prepare(pdf_preview())
    assert PdfUploadGate(store=second).consume(token, "paper-1", "version-1") == pdf_preview()
    assert PdfUploadGate(store=first).lookup_confirmed(token, "paper-1", "version-1") == pdf_preview()
    with pytest.raises(ToolError, match="PDF_UPLOAD_CONFIRMATION_REQUIRED"):
        PdfUploadGate(store=first).consume(token, "paper-1", "version-1")


def test_registration_gate_is_shared_but_kinds_stay_separate(tmp_path):
    first, second = two_processes(tmp_path)
    token = RegistrationGate(store=first).prepare("paper.pdf", "r")
    assert RegistrationGate(store=second).is_prepared("paper.pdf", "r")
    with pytest.raises(ToolError):
        RegistrationGate(store=second, kind="paper_reparse").consume(token, "paper.pdf", "r")
    RegistrationGate(store=second).consume(token, "paper.pdf", "r")
    assert not RegistrationGate(store=first).is_prepared("paper.pdf", "r")


def test_sync_card_approval_reaches_a_different_server_process(tmp_path):
    first, second = two_processes(tmp_path)
    service = Mock()
    service.preview.return_value = PaperSyncPreview("account", "paper-1", "Paper", "created")
    service.grant_and_queue.return_value = SyncResult("op", "pending")
    service.sync.side_effect = lambda operation_id: SyncResult(operation_id, "synced", "server-1")
    chat = create_server(MCPServices(Mock(), Mock(), server_sync=service, confirmations=first))
    card = create_server(MCPServices(Mock(), Mock(), server_sync=service, confirmations=second))

    async def check():
        prepared = await chat.call_tool("prepare_paper_server_sync", {"paper_id": "paper-1"})
        arguments = {"paper_id": "paper-1",
                     "operation_id": prepared.structuredContent["operation_id"],
                     "confirmation_token": prepared.meta["confirmation_token"]}
        result = await card.call_tool("sync_paper_to_server", arguments)
        assert result[1]["status"] == "synced"
        with pytest.raises(ToolError):
            await chat.call_tool("sync_paper_to_server", arguments)

    asyncio.run(check())
    service.grant_and_queue.assert_called_once()
    assert service.grant_and_queue.call_args.args[3] == service.preview.return_value


def test_pdf_card_approval_and_status_reach_a_different_server_process(tmp_path):
    first, second = two_processes(tmp_path)
    service = Mock()
    service.preview.return_value = pdf_preview()
    service.upload.return_value = PdfUploadResult(
        "upload-1", "server-paper-1", "version-1", "paper.pdf", 1234, "application/pdf", "ready")
    service.status.return_value = {"status": "ready", "file_name": "paper.pdf", "file_size": 1234,
                                   "content_type": "application/pdf", "reason": None}
    chat = create_server(MCPServices(Mock(), Mock(), pdf_upload=service, confirmations=first))
    card = create_server(MCPServices(Mock(), Mock(), pdf_upload=service, confirmations=second))

    async def check():
        prepared = await chat.call_tool("prepare_paper_pdf_upload", {"paper_id": "paper-1"})
        arguments = {"paper_id": "paper-1", "version_id": "version-1",
                     "confirmation_token": prepared.meta["confirmation_token"]}
        uploaded = await card.call_tool("upload_paper_pdf_to_server", arguments)
        assert uploaded.structuredContent["status"] == "ready"
        status = await chat.call_tool("check_paper_pdf_upload_status", arguments)
        assert status.structuredContent["status"] == "ready"

    asyncio.run(check())
    service.upload.assert_called_once_with(pdf_preview())


def test_runtime_store_lives_in_data_root(tmp_path):
    store = ConfirmationStore(tmp_path / "nested" / "card-confirmations.sqlite3")
    store.issue("server_sync", ["p", "o"])
    with sqlite3.connect(tmp_path / "nested" / "card-confirmations.sqlite3") as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM card_confirmations").fetchone()
    assert count == 1


@pytest.mark.parametrize('operation', ['take', 'lookup_confirmed', 'has_pending', 'pending_count'])
def test_expiry_is_checked_after_acquiring_transaction(tmp_path, operation):
    from contextlib import contextmanager
    now = [1000.0]
    store = ConfirmationStore(tmp_path / 'confirm.sqlite', clock=lambda: now[0])
    token = store.issue('pdf_upload', ['p', 'v'], ttl=1)
    if operation == 'lookup_confirmed':
        assert store.take('pdf_upload', token, ['p', 'v'], keep_confirmed=True)[0]
    transaction = store._transaction
    @contextmanager
    def delayed_transaction():
        with transaction() as conn:
            # 잠금 획득까지 대기하는 동안 만료 경계를 넘은 상황이다.
            now[0] = 1002.0
            yield conn
    store._transaction = delayed_transaction
    if operation in ('take', 'lookup_confirmed'):
        assert getattr(store, operation)('pdf_upload', token, ['p', 'v']) == (False, None)
    elif operation == 'has_pending':
        assert not store.has_pending('pdf_upload', ['p', 'v'])
    else:
        assert store.pending_count('pdf_upload') == 0


def test_storage_failure_is_safe_and_does_not_start_sync(tmp_path):
    path = tmp_path / 'private-storage-marker.sqlite'
    store = ConfirmationStore(path)
    service = Mock()
    service.preview.return_value = PaperSyncPreview('account', 'paper-1', 'Paper', 'created')
    server = create_server(MCPServices(Mock(), Mock(), server_sync=service, confirmations=store))
    async def scenario():
        prepared = await server.call_tool('prepare_paper_server_sync', {'paper_id':'paper-1'})
        path.unlink()
        path.mkdir()
        with pytest.raises(ToolError) as error:
            await server.call_tool('sync_paper_to_server', {
                'paper_id':'paper-1', 'operation_id':prepared.structuredContent['operation_id'],
                'confirmation_token':prepared.meta['confirmation_token']})
        assert 'CARD_CONFIRMATION_STORE_UNAVAILABLE' in str(error.value)
        assert 'private-storage-marker' not in str(error.value)
        service.grant_and_queue.assert_not_called()
        service.sync.assert_not_called()
    asyncio.run(scenario())


def test_two_real_processes_cannot_consume_same_token_twice(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import json
    path = tmp_path / 'confirm.sqlite'
    token = ConfirmationStore(path).issue('registration', ['p', 'r'])
    code = ("import json,sys; from solo_leveling.interfaces.mcp.confirmations import ConfirmationStore;"
            "print(json.dumps(ConfirmationStore(sys.argv[1]).take('registration',sys.argv[2],['p','r'])[0]))")
    def consume(_):
        result = subprocess.run([sys.executable, '-c', code, str(path), token],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [False, True]
