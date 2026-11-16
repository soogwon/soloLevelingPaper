from dataclasses import replace
from unittest.mock import Mock, call

import pytest

from solo_leveling.domain.deletion_policy import DELETION_NOTICE_KO, DeletionIntent
from solo_leveling.interfaces.mcp.deletion_guidance import (
    DeletionLookup, DeletionSnapshot, deletion_confirmation,
    recover_deletion_guidance, render_deletion_preview,
)

LOOKUP = DeletionLookup('private-owner', 'private-paper', 'private-key')


def snapshot(status='blocking', **kwargs):
    return DeletionSnapshot(LOOKUP, 'private-request', status, True, **kwargs)


@pytest.mark.parametrize('intent', list(DeletionIntent))
def test_confirmation_keeps_actions_distinct_without_executable_controls(intent):
    view = deletion_confirmation(intent)
    text, html = render_deletion_preview(view)
    assert view.state == intent.value
    assert view.title in text and view.title in html
    assert 'disabled' in html and '<script' not in html and 'href=' not in html
    if intent is DeletionIntent.SERVER_PAPER:
        assert DELETION_NOTICE_KO in text
    assert '실제 삭제 기능은 연결되지 않았습니다' in text


@pytest.mark.parametrize('status', ['blocking', 'cleaning', 'failed', 'complete'])
def test_repeated_read_uses_exact_request_without_submission(status):
    reader = Mock(spec=['read'])
    reader.read.return_value = snapshot(status)
    for _ in range(2):
        view = recover_deletion_guidance(reader, LOOKUP)
        assert view.state == status
        text, html = render_deletion_preview(view)
        assert 'private-' not in text + html
    assert reader.mock_calls == [call.read(LOOKUP)] * 2
    if status == 'complete':
        assert '백업 완전 삭제 여부는 아직 확인되지 않았습니다' in view.message


def test_backup_completion_requires_explicit_trusted_confirmation():
    reader = Mock(spec=['read'])
    reader.read.return_value = snapshot('complete', backup_erasure_confirmed=True)
    assert recover_deletion_guidance(reader, LOOKUP).state == 'erased'
    reader.read.return_value = snapshot('cleaning', backup_erasure_confirmed=True)
    assert recover_deletion_guidance(reader, LOOKUP).state == 'unavailable'


@pytest.mark.parametrize('changed', [
    replace(LOOKUP, owner_id='other'), replace(LOOKUP, paper_id='other'),
    replace(LOOKUP, request_key='other'),
])
def test_wrong_scope_returns_same_non_disclosing_view_as_missing(changed):
    reader = Mock(spec=['read'])
    reader.read.return_value = replace(snapshot(), lookup=changed)
    denied = recover_deletion_guidance(reader, LOOKUP)
    reader.read.return_value = None
    assert recover_deletion_guidance(reader, LOOKUP) == denied


@pytest.mark.parametrize('value', [object(), snapshot('unknown-private-error'),
    replace(snapshot(), request_id=''), snapshot(backup_erasure_confirmed='yes')])
def test_malformed_state_never_claims_completion(value):
    reader = Mock(spec=['read'])
    reader.read.return_value = value
    assert recover_deletion_guidance(reader, LOOKUP).state == 'unavailable'


def test_denied_access_and_error_do_not_expose_payload_or_recreate_request():
    reader = Mock(spec=['read'])
    reader.read.return_value = replace(snapshot(), access_allowed=False)
    assert recover_deletion_guidance(reader, LOOKUP).state == 'not_found'
    reader.read.side_effect = RuntimeError('private-token-body')
    view = recover_deletion_guidance(reader, LOOKUP)
    assert view.state == 'unavailable'
    assert 'private-' not in ''.join(render_deletion_preview(view))
    altered = replace(view, message='<script>private-token-body</script>')
    assert render_deletion_preview(altered) == render_deletion_preview(view)
