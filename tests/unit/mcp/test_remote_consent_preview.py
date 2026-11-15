"""서버 조회 대역→Guidance→HTML/텍스트의 표시와 미연결 경계를 확인한다."""

from dataclasses import replace
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from solo_leveling.interfaces.mcp.remote_consent_contract import (
    Intent, Snapshot, Guidance, ConsentState as C, BudgetState as B, ExecutionState as E, recover_guidance,
)
from solo_leveling.interfaces.mcp.remote_consent_preview import render_consent_preview

BASE = Snapshot(Intent('private-owner', 'private-target', 'question', 'private-hash', 'private-key'),
                C.CURRENT, C.CURRENT, B.AVAILABLE, E.ABSENT, access_allowed=True)


class Parsed(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []; self.text = []
        self.feed(html)
    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))
    def handle_data(self, data): self.text.append(data)


@pytest.mark.parametrize('changes', [
    *[{p+'_consent':s} for p in ('storage','ai') for s in (C.MISSING,C.OUTDATED,C.REVOKED)],
    {'budget':B.LIMITED}, {'budget':B.USAGE_UNKNOWN}, {},
    *[{'execution':s,'execution_id':'private-job'} for s in (E.RUNNING,E.COMPLETED,E.FAILED,E.UNKNOWN)],
    {'execution':E.COMPLETED,'execution_id':'private-job','saved_result_available':True,'budget':B.USAGE_UNKNOWN},
    {'access_allowed':False},
])
def test_card_and_text_use_same_server_guidance_without_side_effects(changes):
    calls = []
    snapshot = replace(BASE, **changes)
    def read(intent):
        calls.append(intent)
        return snapshot
    guidance = recover_guidance(SimpleNamespace(read=read), BASE.intent)
    preview = render_consent_preview(guidance)
    parsed = Parsed(preview.html)
    assert guidance.message in parsed.text and guidance.message in preview.text
    buttons = [attrs for tag, attrs in parsed.tags if tag == 'button']
    assert len(buttons) == 1 and 'disabled' in buttons[0] and buttons[0]['type'] == 'button'
    assert not {tag for tag, _ in parsed.tags} & {'script','form','a','iframe','img'}
    assert len(calls) == 1
    assert 'private-' not in preview.html and 'private-' not in preview.text


def test_message_is_escaped_in_html():
    attack = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    preview = render_consent_preview(Guidance('unavailable', attack, 'refresh_status'))
    parsed = Parsed(preview.html)
    assert not any(tag in ('script','img') for tag, _ in parsed.tags)
    assert attack in ''.join(parsed.text)


@pytest.mark.parametrize('view', [None,
    Guidance('future','secret-response','refresh_status'),
    Guidance('completed','secret-response','read_result'),
    Guidance('usage_unknown','secret-response','review_submission'),
    Guidance('unavailable','secret-response','refresh_status',automatic_paid_retry=True),
])
def test_unknown_or_inconsistent_response_has_fixed_fallback(view):
    preview = render_consent_preview(view)
    assert 'secret-response' not in preview.text
    assert '상태를 확인하지 못했습니다' in preview.text
