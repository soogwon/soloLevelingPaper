"""실제 합성 PDF와 안전 로그로 회전 필터 집계·본문 비노출을 검증한다."""

from contextlib import redirect_stderr
from io import StringIO
import json

import pytest

from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages, ExtractedPage, _is_upright
from solo_leveling.infrastructure.parsing.chunker import chunk_pages
from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
from solo_leveling.parsing_diagnostics import RotationMetrics, emit_rotation_metrics, valid_rotation_record
from tests.fixtures.pdf_builder import build_minimal_pdf


@pytest.mark.parametrize('detailed', [False, True])
def test_real_pdf_mixed_rotated_empty_and_normal_pages(tmp_path, capsys, detailed):
    path = tmp_path / 'PRIVATE-filename.pdf'
    path.write_bytes(build_minimal_pdf(['BODY', '', '', 'NORMAL'],
        rotated_texts=['UNIQUE_HEADER', 'ROTATED_ONLY', None, None]))
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output, detailed=detailed)):
        pages = extract_pages(str(path))
    assert [p.text for p in pages] == ['BODY', '', '', 'NORMAL']
    assert [p.rotation_metrics for p in pages] == [
        RotationMetrics(17, 13), RotationMetrics(12, 12), RotationMetrics(0, 0), RotationMetrics(6, 0)]
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == 4 and all(valid_rotation_record(row) for row in rows)
    assert len({row['parse_id'] for row in rows}) == 1
    assert [row['pdf_page'] for row in rows] == [1, 2, 3, 4]
    assert [row['excluded_ratio'] for row in rows] == [round(13/17, 6), 1.0, 0.0, 0.0]
    assert all(value not in output.getvalue() for value in ['PRIVATE', 'BODY', 'UNIQUE_HEADER', str(path)])
    assert capsys.readouterr().out == ''
    assert [(c.pdf_page, c.original_text) for c in chunk_pages('revision', pages)] == [(1, 'BODY'), (4, 'NORMAL')]
    # 별도 추출은 다른 식별자이며 파일명/내용에서 계산한 식별자가 아니다.
    second = StringIO()
    with redirect_stderr(second):
        extract_pages(str(path))
    assert json.loads(second.getvalue().splitlines()[0])['parse_id'] != rows[0]['parse_id']


def test_counts_precede_footer_removal_and_keep_spaces(tmp_path):
    path = tmp_path / 'footer.pdf'
    path.write_bytes(build_minimal_pdf(['A B'], footers=['6'], rotated_texts=['XY']))
    page, = extract_pages(str(path))
    assert page.text == 'A B' and page.page_number_candidate == '6'
    assert page.printed_page_label is None
    assert page.rotation_metrics == RotationMetrics(6, 2)


def test_original_filter_and_unmeasured_pages_remain_compatible():
    assert _is_upright({'object_type': 'rect', 'upright': False})
    assert _is_upright({'object_type': 'char'})
    assert not _is_upright({'object_type': 'char', 'upright': False})
    assert ExtractedPage(1, 'Body').rotation_metrics is None


@pytest.mark.parametrize('update', [
    {'text': 'PRIVATE'}, {'path': 'PRIVATE'}, {'parse_id': 'PRIVATE'},
    {'pdf_page': True}, {'pdf_page': 0}, {'total_chars': -1}, {'total_chars': True},
    {'total_chars': 2**64}, {'total_chars': 'PRIVATE'}, {'excluded_chars': 11},
    {'excluded_chars': -1}, {'excluded_ratio': float('nan')},
    {'excluded_ratio': float('inf')}, {'excluded_ratio': True}, {'excluded_ratio': 0.9},
])
def test_malformed_or_sensitive_metrics_are_dropped_and_stream_recovers(update):
    row = dict(event='pdf_rotation', parse_id='a'*32, pdf_page=1,
               total_chars=10, excluded_chars=2, excluded_ratio=0.2)
    output = StringIO()
    stream = SafeDiagnosticStream(output)
    stream.write(json.dumps({**row, **update}) + '\n')
    assert output.getvalue() == ''
    line = json.dumps(row) + '\n'
    stream.write(line[:20])
    stream.write(line[20:])
    assert json.loads(output.getvalue()) == row


def test_logging_failure_preserves_extracted_text(tmp_path):
    class Broken:
        def write(self, text):
            raise OSError('PRIVATE')
    path = tmp_path / 'sample.pdf'
    path.write_bytes(build_minimal_pdf(['Body.'], rotated_texts=['SIDE']))
    with redirect_stderr(SafeDiagnosticStream(Broken())):
        page, = extract_pages(str(path))
    assert page.text == 'Body.' and page.rotation_metrics == RotationMetrics(9, 4)


def test_unknown_log_fields_are_rejected():
    assert not valid_rotation_record({'event': 'pdf_rotation'})
    output = StringIO()
    with redirect_stderr(output):
        emit_rotation_metrics('unsafe/path', 1, RotationMetrics(1, 0))
    assert output.getvalue() == ''
