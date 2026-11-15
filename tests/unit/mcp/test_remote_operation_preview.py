"""운영 제어 안내의 조합·조회 실패·원문 비노출을 검증한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from solo_leveling.interfaces.http.remote_operation_controls import OperationControls
from solo_leveling.interfaces.mcp.remote_operation_preview import render_operation_preview
from tests.unit.mcp.test_remote_consent_preview import Parsed


@pytest.mark.parametrize('intake', [False, True])
@pytest.mark.parametrize('dispatch', [False, True])
def test_independent_controls_have_equal_text_and_card(intake, dispatch):
    reader = NS(get=Mock(return_value=OperationControls(2, intake, dispatch)))
    result = render_operation_preview(reader, mcp_enabled=True)
    reader.get.assert_called_once_with()
    assert ('신규 PDF 처리 접수는 운영 제어상 허용' in result.text) == intake
    assert ('진행 작업의 다음 제공자 전송은 운영 제어상 허용' in result.text) == dispatch
    assert '접근·삭제 권한' in result.text and '동의·예산·권한을 다시 검사' in result.text
    parsed = Parsed(result.html)
    assert result.text.splitlines()[1] in parsed.text
    assert not {tag for tag, _ in parsed.tags} & {'button','form','script','a'}


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_endpoint_off_or_unknown_never_queries_or_promises_web_availability(enabled):
    reader = NS(get=Mock(side_effect=AssertionError('조회하지 않아야 함')))
    result = render_operation_preview(reader, mcp_enabled=enabled)
    reader.get.assert_not_called()
    assert ('연결과 도구 조회가 중단' in result.text) == (enabled is False)
    assert '허용 상태' not in result.text
    if enabled is False:
        assert '웹의 이용 가능 여부는 별도로 확인' in result.text


@pytest.mark.parametrize('value', [None, {}, OperationControls(True, True, True),
    OperationControls(0, True, True), OperationControls(1, 1, True), OperationControls(1, True, 'true')])
def test_invalid_control_is_not_treated_as_permission(value):
    result = render_operation_preview(NS(get=lambda: value), mcp_enabled=True)
    assert '확인하지 못했습니다' in result.text and '허용 상태' not in result.text


def test_store_error_never_leaks_exception_or_retries():
    reader = NS(get=Mock(side_effect=RuntimeError('private-token-question')))
    result = render_operation_preview(reader, mcp_enabled=True)
    assert 'private-' not in result.text + result.html
    assert '확인하지 못했습니다' in result.text
    reader.get.assert_called_once_with()
