"""교재 JSON을 검사하고 assets/template.html 형식의 HTML 교재 한 파일로 만든다.

사용법:
    python build_textbook.py textbook.json --out 교재.html

입력 형식은 references/textbook-format.md를 따른다. 형식 오류가 있으면 파일을 만들지 않고
문제를 모두 출력한 뒤 종료 코드 1을 돌려준다. 본문 문자열은 HTML로 해석하지 않고 이스케이프하며,
**굵게**, `코드`, 줄머리 "- " 목록, 빈 줄 문단만 지원한다.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / 'assets' / 'template.html'
SECTION_TYPES = ('text', 'formula', 'evidence', 'example', 'tip')


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def inline(text: str) -> str:
    out = esc(text)
    out = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    return out


def rich(text: str) -> str:
    """빈 줄로 문단, '- '로 시작하는 줄 묶음은 목록."""
    blocks = []
    for chunk in re.split(r'\n\s*\n', text.strip()):
        lines = [l.rstrip() for l in chunk.splitlines() if l.strip()]
        if lines and all(l.lstrip().startswith('- ') for l in lines):
            items = ''.join(f'<li>{inline(l.lstrip()[2:])}</li>' for l in lines)
            blocks.append(f'<ul class="plain">{items}</ul>')
        elif lines:
            blocks.append('<p>' + '<br>'.join(inline(l) for l in lines) + '</p>')
    return '\n'.join(blocks)


def text_field(obj: dict, key: str, where: str, problems: list[str], required=True):
    value = obj.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        problems.append(f'{where}: {key}는 비어 있지 않은 문자열이어야 합니다.')
        return None
    return value


def validate(book: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(book, dict):
        return ['최상위는 객체여야 합니다.']
    text_field(book, 'title', '교재', problems)
    paper = book.get('paper')
    if not isinstance(paper, dict):
        problems.append('paper 객체(title 필수)가 필요합니다.')
    else:
        text_field(paper, 'title', 'paper', problems)
    chapters = book.get('chapters')
    if not isinstance(chapters, list) or not chapters:
        return problems + ['chapters에 장이 하나 이상 있어야 합니다.']
    for n, ch in enumerate(chapters, start=1):
        where = f'{n}장'
        if not isinstance(ch, dict):
            problems.append(f'{where}: 객체여야 합니다.')
            continue
        text_field(ch, 'title', where, problems)
        sections = ch.get('sections')
        if not isinstance(sections, list) or not sections:
            problems.append(f'{where}: sections가 하나 이상 필요합니다.')
            continue
        for m, sec in enumerate(sections, start=1):
            w = f'{where} {m}번째 블록'
            kind = sec.get('type') if isinstance(sec, dict) else None
            if kind not in SECTION_TYPES:
                problems.append(f'{w}: type은 {"/".join(SECTION_TYPES)} 중 하나입니다.')
                continue
            if kind == 'formula':
                text_field(sec, 'tex', w, problems)
            elif kind == 'evidence':
                page = sec.get('page')
                if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                    problems.append(f'{w}: 근거의 page는 1 이상의 정수(PDF 쪽)입니다.')
                text_field(sec, 'quote', w, problems)
            else:
                text_field(sec, 'body', w, problems)
        for k, item in enumerate(ch.get('check', []) or [], start=1):
            if not isinstance(item, dict):
                problems.append(f'{where} 확인 문제 {k}: 객체여야 합니다.')
                continue
            text_field(item, 'q', f'{where} 확인 문제 {k}', problems)
            text_field(item, 'a', f'{where} 확인 문제 {k}', problems)
    for k, g in enumerate(book.get('glossary', []) or [], start=1):
        if not isinstance(g, dict):
            problems.append(f'용어 {k}: 객체여야 합니다.')
            continue
        text_field(g, 'term', f'용어 {k}', problems)
        text_field(g, 'definition', f'용어 {k}', problems)
    return problems


def section_html(sec: dict) -> str:
    kind = sec['type']
    if kind == 'text':
        return rich(sec['body'])
    if kind == 'formula':
        explain = f'<div class="explain">{inline(sec["explain"])}</div>' if sec.get('explain') else ''
        return (f'<div class="formula"><div class="tex" data-tex="{esc(sec["tex"])}">{esc(sec["tex"])}</div>'
                f'{explain}</div>')
    if kind == 'evidence':
        where = f'논문 p.{sec["page"]}' + (f' · {esc(sec["section"])}' if sec.get('section') else '')
        note = f'<div>{inline(sec["note"])}</div>' if sec.get('note') else ''
        return (f'<div class="evidence"><span class="where">{where}</span>'
                f'<blockquote>{esc(sec["quote"])}</blockquote>{note}</div>')
    tag = '예시' if kind == 'example' else '팁'
    return f'<div class="{kind}"><span class="tag">{tag}</span>{rich(sec["body"])}</div>'


def build(book: dict) -> tuple[str, str]:
    toc, body = [], []
    paper = book['paper']
    meta = [v for v in (book.get('goal') and f'목적: {book["goal"]}', book.get('level') and f'수준: {book["level"]}',
                        book.get('date')) if v]
    paper_line = ' · '.join(esc(v) for v in (paper['title'], paper.get('authors'), paper.get('source')) if v)
    body.append('<header class="cover"><div class="eyebrow">SOLO LEVELING PAPER · 학습 교재</div>'
                f'<h1>{esc(book["title"])}</h1><p class="paper">{paper_line}</p>'
                '<ul class="meta">' + ''.join(f'<li>{esc(m)}</li>' for m in meta) + '</ul></header>')

    def add(anchor: str, label: str, inner: str, lede: str | None = None, num: str | None = None):
        toc.append(f'    <li><a href="#{anchor}">{esc(label)}</a></li>')
        number = f'<span class="num">{esc(num)}</span>' if num else ''
        lede_html = f'<p class="lede">{inline(lede)}</p>' if lede else ''
        body.append(f'<section class="block" id="{anchor}"><h2>{number}{esc(label)}</h2>{lede_html}{inner}</section>')

    if book.get('objectives'):
        add('objectives', '학습 목표', '<div class="card"><ul class="plain objectives">'
            + ''.join(f'<li>{inline(o)}</li>' for o in book['objectives']) + '</ul></div>')
    if book.get('path'):
        items = ''.join(f'<li><strong>{esc(p["name"])}</strong>'
                        + (f'<span class="why">{inline(p["why"])}</span>' if p.get('why') else '') + '</li>'
                        for p in book['path'] if isinstance(p, dict) and p.get('name'))
        add('path', '학습 순서', f'<ol class="path">{items}</ol>', book.get('path_note'))
    for n, ch in enumerate(book['chapters'], start=1):
        inner = '\n'.join(section_html(s) for s in ch['sections'])
        if ch.get('check'):
            inner += '<h3>확인 문제</h3>' + ''.join(
                f'<details class="check"><summary>{inline(c["q"])}</summary><div class="answer">{rich(c["a"])}</div></details>'
                for c in ch['check'])
        add(f'ch{n}', ch['title'], inner, ch.get('summary'), f'{n}.')
    if book.get('key_points'):
        add('key-points', '핵심 정리', '<div class="card"><ul class="plain keypoints">'
            + ''.join(f'<li>{inline(k)}</li>' for k in book['key_points']) + '</ul></div>')
    if book.get('glossary'):
        rows = ''.join(
            f'<tr><td><strong>{esc(g["term"])}</strong>' + (f'<br><span class="paper">{esc(g["en"])}</span>' if g.get('en') else '')
            + f'</td><td>{inline(g["definition"])}</td><td>{esc(g.get("page") or "")}</td></tr>'
            for g in book['glossary'])
        add('glossary', '용어 정리', '<table class="glossary"><thead><tr><th>용어</th><th>뜻</th><th>쪽</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')
    if book.get('next_steps'):
        add('next', '다음에 할 것', '<ul class="plain">' + ''.join(f'<li>{inline(s)}</li>' for s in book['next_steps']) + '</ul>')
    footer = book.get('notes') or '이 교재는 대화와 논문 원문을 바탕으로 만들었습니다. 쪽 번호는 PDF 쪽 번호입니다.'
    body.append(f'<footer>{inline(footer)}</footer>')
    return '\n'.join(toc), '\n'.join(body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('textbook_json', type=Path)
    parser.add_argument('--out', type=Path, default=Path('textbook.html'))
    args = parser.parse_args(argv)
    book = json.loads(args.textbook_json.read_text(encoding='utf-8'))
    problems = validate(book)
    if problems:
        print('교재 형식 오류 — 파일을 만들지 않았습니다:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        return 1
    toc, body = build(book)
    values = {'TITLE': esc(book['title']), 'TOC': toc, 'BODY': body}
    # 한 번에 치환해 본문 속 '{{...}}' 글자가 다시 치환되지 않게 한다.
    page = re.sub(r'\{\{(TITLE|TOC|BODY)\}\}', lambda m: values[m.group(1)], TEMPLATE.read_text(encoding='utf-8'))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding='utf-8')
    print(f'만듦: {args.out} ({len(book["chapters"])}개 장)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
