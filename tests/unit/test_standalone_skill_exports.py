"""단독판 생성기의 잘못된 입력과 기존 근거 파일 보호를 검증한다."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(skill, name):
    path = ROOT / 'skills' / skill / 'scripts' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('field,value', [
    ('check', 7), ('check', '질문'), ('glossary', 1), ('glossary', {}),
    ('path', 42), ('path', ['개념']), ('objectives', '목표'),
    ('key_points', [None]), ('next_steps', {}),
])
def test_invalid_optional_fields_preserve_existing_output(tmp_path, field, value):
    module = load_script('solo-leveling-textbook', 'build_textbook')
    book = json.loads((ROOT / 'skills/samples/textbook-attention.json').read_text(encoding='utf-8'))
    target = book['chapters'][0] if field == 'check' else book
    target[field] = value
    source, output = tmp_path / 'input.json', tmp_path / 'book.html'
    source.write_text(json.dumps(book), encoding='utf-8')
    output.write_text('기존 교재', encoding='utf-8')
    assert module.main([str(source), '--out', str(output)]) == 1
    assert output.read_text(encoding='utf-8') == '기존 교재'


def test_sample_textbook_builds_and_escapes_html(tmp_path):
    module = load_script('solo-leveling-textbook', 'build_textbook')
    book = json.loads((ROOT / 'skills/samples/textbook-attention.json').read_text(encoding='utf-8'))
    book['chapters'][0]['sections'].append({'type': 'text', 'body': '<script>alert(1)</script>'})
    source, output = tmp_path / 'input.json', tmp_path / 'book.html'
    source.write_text(json.dumps(book), encoding='utf-8')
    assert module.main([str(source), '--out', str(output)]) == 0
    html = output.read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


@pytest.mark.parametrize('existing', ['paper.json', 'page_003.txt'])
def test_pdf_extraction_does_not_mix_previous_paper(tmp_path, monkeypatch, existing):
    module = load_script('solo-leveling-guide', 'pdf_text')
    source, output = tmp_path / 'new.pdf', tmp_path / 'pages'
    source.write_bytes(b'%PDF-test')
    output.mkdir()
    (output / existing).write_text('기존 근거', encoding='utf-8')
    monkeypatch.setattr(module, 'extract_pages', lambda _: pytest.fail('기존 출력 폴더는 추출 전 거부해야 합니다.'))
    assert module.main([str(source), '--out', str(output)]) == 1
    assert (output / existing).read_text(encoding='utf-8') == '기존 근거'
    assert len(list(output.iterdir())) == 1


def test_pdf_extraction_new_directory(tmp_path, monkeypatch):
    module = load_script('solo-leveling-guide', 'pdf_text')
    source, output = tmp_path / 'new.pdf', tmp_path / 'pages'
    source.write_bytes(b'%PDF-test')
    monkeypatch.setattr(module, 'extract_pages', lambda _: (['새 논문 본문'], None))
    assert module.main([str(source), '--out', str(output)]) == 0
    assert (output / 'page_001.txt').read_text(encoding='utf-8') == '새 논문 본문'
    assert json.loads((output / 'paper.json').read_text(encoding='utf-8'))['page_count'] == 1
