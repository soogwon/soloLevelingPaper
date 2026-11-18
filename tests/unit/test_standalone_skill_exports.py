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


def _quiz():
    return json.loads((ROOT / 'skills/samples/quiz-attention.json').read_text(encoding='utf-8'))


def test_quiz_html_is_default_and_markdown_is_optional(tmp_path):
    module = load_script('solo-leveling-quiz', 'quiz_export')
    source = tmp_path / 'quiz.json'
    source.write_text(json.dumps(_quiz(), ensure_ascii=False), encoding='utf-8')
    assert module.main([str(source), '--out', str(tmp_path / 'a')]) == 0
    assert [p.suffix for p in (tmp_path / 'a').iterdir()] == ['.html']
    assert module.main([str(source), '--out', str(tmp_path / 'b'), '--md']) == 0
    assert sorted(p.suffix for p in (tmp_path / 'b').iterdir()) == ['.html', '.md', '.md']


def test_quiz_html_keeps_text_inside_data_block(tmp_path):
    module = load_script('solo-leveling-quiz', 'quiz_export')
    quiz = _quiz()
    quiz['title'] = '</script><script>alert(1)</script> & <b>퀴즈</b>'
    quiz['questions'][0]['prompt'] = '<!-- </script> 닫기 시도'
    page = module.build_html(quiz)
    assert page.count('</script>') == 2  # 데이터 블록과 실행 스크립트를 닫는 것만
    assert '<b>' not in page and '<!--' not in page
    start = page.index('id="quiz-data">') + len('id="quiz-data">')
    data = json.loads(page[start:page.index('</script>', start)])
    assert data['title'] == quiz['title']
    assert data['questions'][0]['prompt'] == quiz['questions'][0]['prompt']
    assert '__QUIZ_DATA__' not in page and '__TITLE__' not in page


def test_sample_quiz_html_matches_generator():
    module = load_script('solo-leveling-quiz', 'quiz_export')
    sample = (ROOT / 'skills/samples/quiz-attention.html').read_text(encoding='utf-8')
    assert sample == module.build_html(_quiz())


def test_blank_optional_text_is_treated_as_absent(tmp_path):
    module = load_script('solo-leveling-textbook', 'build_textbook')
    book = json.loads((ROOT / 'skills/samples/textbook-attention.json').read_text(encoding='utf-8'))
    evidence = next(sec for ch in book['chapters'] for sec in ch['sections'] if sec['type'] == 'evidence')
    evidence['note'] = ''
    evidence['section'] = '   '
    source, output = tmp_path / 'input.json', tmp_path / 'book.html'
    source.write_text(json.dumps(book, ensure_ascii=False), encoding='utf-8')
    assert module.main([str(source), '--out', str(output)]) == 0
    assert output.exists()


def test_missing_pdf_library_is_not_reported_as_broken_file(tmp_path, monkeypatch, capsys):
    module = load_script('solo-leveling-guide', 'pdf_text')
    source, output = tmp_path / 'paper.pdf', tmp_path / 'pages'
    source.write_bytes(b'%PDF-test')
    monkeypatch.setattr(module, 'pdf_backend', lambda: None)
    monkeypatch.setattr(module, 'extract_pages', lambda _: pytest.fail('도구가 없으면 추출을 시도하지 않아야 합니다.'))
    assert module.main([str(source), '--out', str(output)]) == 3
    err = capsys.readouterr().err
    assert 'pip install pdfplumber' in err and '손상' not in err
    assert not output.exists()


def test_pypdf_fallback_is_recorded(tmp_path, monkeypatch, capsys):
    module = load_script('solo-leveling-guide', 'pdf_text')
    source, output = tmp_path / 'paper.pdf', tmp_path / 'pages'
    source.write_bytes(b'%PDF-test')
    monkeypatch.setattr(module, 'pdf_backend', lambda: 'pypdf')
    monkeypatch.setattr(module, 'extract_pages', lambda _: (['본문'], None))
    assert module.main([str(source), '--out', str(output)]) == 0
    assert json.loads((output / 'paper.json').read_text(encoding='utf-8'))['extractor'] == 'pypdf'
    assert '추출 도구: pypdf' in capsys.readouterr().out


@pytest.mark.parametrize('skill,script,args', [
    ('solo-leveling-quiz', 'quiz_export', ['{quiz}', '--out', '{tmp}/quiz']),
    ('solo-leveling-textbook', 'build_textbook', ['{book}', '--out', '{tmp}/book.html']),
    ('solo-leveling-guide', 'concept_path', ['--paper-json', '{paper}', '--goal', 'understand']),
])
def test_scripts_print_korean_on_windows_console(tmp_path, skill, script, args):
    """Windows 기본 콘솔(cp1252)에서도 한글 출력 때문에 실패하지 않는다."""
    import os
    import subprocess
    import sys
    paper = tmp_path / 'paper.json'
    paper.write_text(json.dumps({'sha256': 'unknown', 'arxiv_id': '1706.03762'}), encoding='utf-8')
    values = {'quiz': str(ROOT / 'skills/samples/quiz-attention.json'),
              'book': str(ROOT / 'skills/samples/textbook-attention.json'),
              'paper': str(paper), 'tmp': str(tmp_path)}
    command = [sys.executable, str(ROOT / 'skills' / skill / 'scripts' / f'{script}.py')]
    command += [a.format(**values) for a in args]
    env = dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
    result = subprocess.run(command, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert 'UnicodeEncodeError' not in result.stderr.decode('utf-8', 'replace')
