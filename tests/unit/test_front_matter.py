"""PDF 첫 페이지 논문 제목 인식은 보수적으로 하고, 못 찾으면 파일 이름 표시로 남긴다."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from solo_leveling.domain.remote_processing import PaperFrontMatter, front_matter_valid
from solo_leveling.infrastructure.parsing.front_matter import (
    detect_authors, detect_front_matter, detect_publication_year, detect_title,
)
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from solo_leveling.infrastructure.parsing.sections import LineStyle
from solo_leveling.workers.processing_staging import PostgresProcessingResultStaging, ProcessingStagingError
from tests.fixtures.pdf_builder import build_minimal_pdf
from tests.fixtures.processing_result import result, LIMITS, SETTINGS, SPEC

ATTENTION_PDF = Path(__file__).resolve().parents[1] / 'test_data' / 'tmp_pdfs' / 'c30732f1-f6af-45b0-b8f7-38e5c0869588.pdf'


def line(text, size, top, bold=False, segments=None):
    return LineStyle(text=text, size=size, bold=bold, top_ratio=top, segments=segments)


def test_largest_top_lines_become_title_and_footnote_marks_are_removed():
    lines = [
        line('Provided proper attribution is provided, Google hereby grants', 12, 0.09),
        line('Deep Residual Learning', 17.2, 0.15),
        line('for Image Recognition∗', 17.2, 0.17),
        line('Kaiming He Xiangyu Zhang', 10, 0.25),
        line('Abstract', 12, 0.4),
    ]
    assert detect_title(lines, 10.0) == 'Deep Residual Learning for Image Recognition'
    # 간격 정보 없이 한 칸 띄어쓰기로 붙은 저자 줄은 나누지 않는다.
    assert detect_front_matter(lines, 10.0) == PaperFrontMatter(title='Deep Residual Learning for Image Recognition')


@pytest.mark.parametrize('lines, body', [
    ([line('Same size heading', 10.5, 0.1)], 10.0),                 # 본문과 크기 차이가 작다.
    ([line('Large Text Low On Page', 18, 0.7)], 10.0),              # 위쪽 절반이 아니다.
    ([line('arXiv:1706.03762v7 [cs.CL]', 20, 0.1)], 10.0),          # arXiv 표기
    ([line('https://example.org/paper', 20, 0.1)], 10.0),           # URL
    ([line('A1', 20, 0.1)], 10.0),                                  # 글자가 거의 없다.
    ([line(f'Line {n} of a very long block', 20, 0.05 + n / 100) for n in range(6)], 10.0),  # 문단 크기
    ([line('Some Title', 20, 0.1)], None),                          # 본문 크기를 모른다.
    ([], 10.0),
])
def test_uncertain_candidates_are_not_guessed(lines, body):
    assert detect_title(lines, body) is None
    found = detect_front_matter(lines, body)
    assert found is None or found.title is None


def test_repeated_header_is_excluded():
    lines = [line('Journal of Synthetic Studies', 20, 0.05), line('Real Paper Title', 16, 0.12)]
    assert detect_title(lines, 10.0, excluded_texts=['journal of synthetic studies']) == 'Real Paper Title'


def test_parser_reads_attention_title_from_first_page():
    document = ProcessingPdfParser().parse(ATTENTION_PDF.read_bytes(), '00000000-0000-0000-0000-000000000001')
    assert document.front_matter == PaperFrontMatter(
        title='Attention Is All You Need',
        authors=('Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar', 'Jakob Uszkoreit',
                 'Llion Jones', 'Aidan N. Gomez', 'Łukasz Kaiser', 'Illia Polosukhin'),
        publication_year=2017)


def test_single_font_pdf_has_no_front_matter():
    document = ProcessingPdfParser().parse(build_minimal_pdf(['Plain text only.']), '00000000-0000-0000-0000-000000000001')
    assert document.front_matter is None


@pytest.mark.parametrize('value, ok', [
    (None, True),
    (PaperFrontMatter(title='Title'), True),
    (PaperFrontMatter(authors=('A', 'B'), publication_year=2017), True),
    (PaperFrontMatter(), False),
    (PaperFrontMatter(title=' padded '), False),
    (PaperFrontMatter(title='x' * 301), False),
    (PaperFrontMatter(title='line\nbreak'), False),
    (PaperFrontMatter(authors=['A']), False),
    (PaperFrontMatter(authors=()), False),
    (PaperFrontMatter(publication_year=999), False),
    (PaperFrontMatter(publication_year=True), False),
    ({'title': 'dict'}, False),
])
def test_front_matter_validation(value, ok):
    assert front_matter_valid(value) is ok


def staging():
    return PostgresProcessingResultStaging(Mock(), limits=LIMITS, translation_settings=SETTINGS,
                                           embedding_spec=SPEC, max_result_bytes=100_000)


def test_payload_without_front_matter_keeps_previous_fingerprint():
    payload, _, _ = staging()._payload(result())
    assert 'front_matter' not in payload['result']['document']


def test_front_matter_roundtrips_through_staging_payload():
    value = result()
    value = replace(value, document=replace(value.document, front_matter=PaperFrontMatter(
        title='Synthetic Title', authors=('A. Author',), publication_year=2020)))
    store = staging()
    payload, _, _ = store._payload(value)
    assert payload['result']['document']['front_matter'] == {
        'title': 'Synthetic Title', 'authors': ('A. Author',), 'publication_year': 2020}
    import json
    stored = json.loads(json.dumps({**payload, 'authorization': {}}))
    assert store.restore(stored).document.front_matter == value.document.front_matter
    # 이전 payload(필드 없음)도 그대로 복원한다.
    plain, _, _ = store._payload(result())
    assert store.restore(json.loads(json.dumps({**plain, 'authorization': {}}))).document.front_matter is None


def test_invalid_front_matter_is_rejected_before_database():
    value = result()
    value = replace(value, document=replace(value.document, front_matter=PaperFrontMatter(title='')))
    pool = Mock()
    store = PostgresProcessingResultStaging(pool, limits=LIMITS, translation_settings=SETTINGS,
                                            embedding_spec=SPEC, max_result_bytes=100_000)
    with pytest.raises(ProcessingStagingError, match='^processing_staging_invalid$'):
        store.save(value)
    pool.connection.assert_not_called()


def test_authors_from_gap_segments_skip_affiliations_and_emails():
    lines = [
        line('Attention Is All You Need', 17.2, 0.19),
        line('x', 10, 0.30, segments=('Ashish Vaswani∗', 'Noam Shazeer∗')),
        line('x', 10, 0.31, segments=('Google Brain', 'Google Research')),
        line('x', 10, 0.32, segments=('avaswani@google.com', 'noam@google.com')),
        line('x', 10, 0.36, segments=('Aidan N. Gomez∗ †', 'Łukasz Kaiser∗')),
        line('x', 10, 0.37, segments=('University of Toronto',)),
        line('Abstract', 12, 0.49),
        line('Jane Doe', 10, 0.52),
    ]
    assert detect_authors(lines, 'Attention Is All You Need') == (
        'Ashish Vaswani', 'Noam Shazeer', 'Aidan N. Gomez', 'Łukasz Kaiser')


def test_comma_separated_authors_with_superscripts():
    lines = [
        line('An Image is Worth 16x16 Words', 17, 0.1),
        line('Alexey Dosovitskiy∗,†, Lucas Beyer∗, Alexander Kolesnikov∗, Dirk Weissenborn∗,', 10, 0.18),
        line('Xiaohua Zhai∗, and Neil Houlsby∗,†', 10, 0.2),
        line('Google Research, Brain Team', 10, 0.22),
        line('BERT: Pre-training of Deep Bidirectional Transformers', 17, 0.3),
    ]
    assert detect_authors(lines, 'An Image is Worth 16x16 Words') == (
        'Alexey Dosovitskiy', 'Lucas Beyer', 'Alexander Kolesnikov', 'Dirk Weissenborn', 'Xiaohua Zhai',
        'Neil Houlsby')


def test_numbered_affiliation_marks_and_particles():
    lines = [line('Title Here', 17, 0.1),
             line('Jacob Devlin1, Ming-Wei Chang2, Ludwig van Beethoven1,2, 홍길동', 10, 0.15)]
    assert detect_authors(lines, 'Title Here') == (
        'Jacob Devlin', 'Ming-Wei Chang', 'Ludwig van Beethoven', '홍길동')


@pytest.mark.parametrize('text', [
    'Department of Computer Science', 'GOOGLE BRAIN', 'Equal contribution', 'Kaiming He Xiangyu Zhang',
    'Single', 'deep learning is fun', 'Work done while at Google',
])
def test_non_author_text_is_rejected(text):
    lines = [line('Title Here', 17, 0.1), line(text, 10, 0.15)]
    assert detect_authors(lines, 'Title Here') is None


def test_authors_need_a_title():
    assert detect_authors([line('Jane Doe', 10, 0.2)], None) is None


@pytest.mark.parametrize('lines, margin, year', [
    ([line('31st Conference on Neural Information Processing Systems (NIPS 2017), Long Beach, CA, USA.', 9, 0.92)],
     'arXiv:1706.03762v7 [cs.CL] 2 Aug 2023', 2017),
    ([line('Published as a conference paper at ICLR 2021', 10, 0.05)], 'arXiv:2010.11929v2 [cs.CV] 3 Jun 2021', 2021),
    ([], 'arXiv:1810.04805v2 [cs.CL] 24 May 2019', 2018),
    ([line('Proceedings of NAACL-HLT 2019, pages 4171–4186', 9, 0.95)], '', 2019),
    # 본문 가운데의 인용 문장은 학회 표기로 보지 않는다.
    ([line('as shown in Proceedings of ACL 2015', 10, 0.5)], '', None),
    # arXiv 첫 제출보다 이른 연도는 버리고 arXiv 연도를 쓴다.
    ([line('Workshop on Something 2012', 9, 0.95)], 'arXiv:1706.03762v7', 2017),
    ([], 'arXiv:1713.00001', None),
    ([], '', None),
])
def test_publication_year(lines, margin, year):
    assert detect_publication_year(lines, margin) == year
