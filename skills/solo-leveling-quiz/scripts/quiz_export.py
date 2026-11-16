"""퀴즈 JSON을 검사하고 문제지·정답지를 Markdown(기본)과 PDF(선택)로 만든다.

사용법:
    python quiz_export.py quiz.json --out quiz_output [--pdf] [--combined]

만드는 파일:
    {이름}-문제지.md, {이름}-정답지.md   (--combined면 {이름}.md 하나에 문제 뒤 정답)
    --pdf를 주면 같은 이름의 .pdf도 만든다 (한글 글꼴은 reportlab 내장 CID 글꼴 사용).

입력 형식은 references/quiz-format.md를 따른다. 형식 오류가 있으면 파일을 만들지 않고
문제를 모두 출력한 뒤 종료 코드 1을 돌려준다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

LEVELS = ('정의', '관계', '적용')
TYPES = ('multiple_choice', 'short_answer')
MAX_QUESTIONS = 30
MARKS = '①②③④⑤'


def validate(quiz: dict) -> list[str]:
    problems = []
    if not isinstance(quiz, dict):
        return ['최상위는 객체여야 합니다.']
    for key in ('title', 'paper'):
        if not isinstance(quiz.get(key), str) or not quiz[key].strip():
            problems.append(f'{key}가 필요합니다.')
    questions = quiz.get('questions')
    if not isinstance(questions, list) or not questions:
        return problems + ['questions에 문항이 하나 이상 있어야 합니다.']
    if len(questions) > MAX_QUESTIONS:
        problems.append(f'문항은 최대 {MAX_QUESTIONS}개입니다.')
    seen = set()
    for n, q in enumerate(questions, start=1):
        where = f'{n}번'
        if not isinstance(q, dict):
            problems.append(f'{where}: 객체여야 합니다.')
            continue
        if q.get('level') not in LEVELS:
            problems.append(f'{where}: level은 {"/".join(LEVELS)} 중 하나입니다.')
        if q.get('type') not in TYPES:
            problems.append(f'{where}: type은 multiple_choice 또는 short_answer입니다.')
        prompt = q.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            problems.append(f'{where}: prompt가 비어 있습니다.')
        else:
            key = ' '.join(prompt.split()).lower()
            if key in seen:
                problems.append(f'{where}: 같은 문항이 이미 있습니다.')
            seen.add(key)
        if q.get('type') == 'multiple_choice':
            choices = q.get('choices')
            if not isinstance(choices, list) or not 3 <= len(choices) <= 5 \
                    or any(not isinstance(c, str) or not c.strip() for c in choices):
                problems.append(f'{where}: 객관식 선택지는 비어 있지 않은 문자열 3~5개입니다.')
            elif len({c.strip() for c in choices}) != len(choices):
                problems.append(f'{where}: 선택지가 중복됩니다.')
            index = q.get('correct_index')
            if not isinstance(index, int) or isinstance(index, bool) \
                    or not isinstance(choices, list) or not 0 <= index < len(choices):
                problems.append(f'{where}: correct_index는 선택지 범위 안의 정수(0부터)입니다.')
        if q.get('type') == 'short_answer':
            for field in ('answer', 'grading_criteria'):
                if not isinstance(q.get(field), str) or not q[field].strip():
                    problems.append(f'{where}: 주관식에는 {field}가 필요합니다.')
        if not isinstance(q.get('explanation'), str) or not q['explanation'].strip():
            problems.append(f'{where}: explanation(해설)이 필요합니다.')
        evidence = q.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            problems.append(f'{where}: 근거(evidence)가 하나 이상 필요합니다.')
        else:
            for e in evidence:
                page = e.get('page') if isinstance(e, dict) else None
                quote = e.get('quote') if isinstance(e, dict) else None
                if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                    problems.append(f'{where}: 근거의 page는 1 이상의 정수입니다.')
                if not isinstance(quote, str) or not quote.strip():
                    problems.append(f'{where}: 근거의 quote(원문 인용)가 필요합니다.')
    return problems


def answer_text(q: dict) -> str:
    if q['type'] == 'multiple_choice':
        i = q['correct_index']
        return f'{MARKS[i]} {q["choices"][i]}'
    return q['answer']


def evidence_text(q: dict) -> str:
    return ' / '.join(f'p.{e["page"]} "{e["quote"]}"' for e in q['evidence'])


def header_lines(quiz: dict, kind: str) -> list[str]:
    lines = [f'# {quiz["title"]} — {kind}', '', f'- 논문: {quiz["paper"]}']
    if quiz.get('goal'):
        lines.append(f'- 학습 목적: {quiz["goal"]}')
    levels = {lv: sum(q['level'] == lv for q in quiz['questions']) for lv in LEVELS}
    lines.append(f'- 문항: {len(quiz["questions"])}개 (' + ', '.join(f'{k} {v}' for k, v in levels.items() if v) + ')')
    if quiz.get('note'):
        lines.append(f'- 안내: {quiz["note"]}')
    return lines + ['']


def question_lines(quiz: dict) -> list[str]:
    lines = []
    for n, q in enumerate(quiz['questions'], start=1):
        kind = '객관식' if q['type'] == 'multiple_choice' else '주관식'
        lines.append(f'**{n}. [{q["level"]}·{kind}]** {q["prompt"]}')
        lines.append('')
        if q['type'] == 'multiple_choice':
            lines += [f'   {MARKS[i]} {c}' for i, c in enumerate(q['choices'])]
        else:
            lines.append('   답: ____________________________________________')
        lines.append('')
    return lines


def answer_lines(quiz: dict) -> list[str]:
    lines = []
    for n, q in enumerate(quiz['questions'], start=1):
        lines.append(f'**{n}. 정답:** {answer_text(q)}')
        if q['type'] == 'short_answer':
            lines.append(f'   - 채점 기준: {q["grading_criteria"]}')
        lines.append(f'   - 해설: {q["explanation"]}')
        lines.append(f'   - 근거: {evidence_text(q)}')
        lines.append('')
    return lines


FONT_DIR = Path(__file__).resolve().parent.parent / 'fonts'


def register_fonts(pdfmetrics, UnicodeCIDFont) -> tuple[str, str]:
    """함께 넣은 나눔고딕을 PDF에 심는다. 없으면 내장 CID 글꼴로 대신한다(일부 뷰어에서 한글이 안 보일 수 있음)."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase.ttfonts import TTFont
    regular, bold = FONT_DIR / 'NanumGothic.ttf', FONT_DIR / 'NanumGothicBold.ttf'
    if regular.is_file() and bold.is_file():
        pdfmetrics.registerFont(TTFont('NanumGothic', str(regular)))
        pdfmetrics.registerFont(TTFont('NanumGothic-Bold', str(bold)))
        addMapping('NanumGothic', 0, 0, 'NanumGothic')
        addMapping('NanumGothic', 1, 0, 'NanumGothic-Bold')
        return 'NanumGothic', 'NanumGothic-Bold'
    print('경고: fonts/의 나눔고딕을 찾지 못해 내장 글꼴을 씁니다. 일부 PDF 뷰어에서 한글이 보이지 않을 수 있습니다.',
          file=sys.stderr)
    pdfmetrics.registerFont(UnicodeCIDFont('HYGothic-Medium'))
    return 'HYGothic-Medium', 'HYGothic-Medium'


# PDF 글꼴에 없는 수학 기호를 PDF에서만 비슷한 글자로 바꾼다. Markdown에는 원문 그대로 둔다.
PDF_SUBSTITUTES = {'\u2212': '-', '\u2013': '-', '\u2014': '-', 'ₖ': '_k', 'ᵥ': '_v', '\u2009': ' ', '\u202f': ' '}


def for_pdf(text: str) -> str:
    return ''.join(PDF_SUBSTITUTES.get(c, c) for c in text)


def missing_glyphs(font_name: str, text: str) -> list[str]:
    from reportlab.pdfbase import pdfmetrics
    face = getattr(pdfmetrics.getFont(font_name), 'face', None)
    table = getattr(face, 'charToGlyph', None)
    if not table:
        return []
    return sorted({c for c in text if not c.isspace() and ord(c) not in table})


def write_pdf(path: Path, title: str, blocks: list[tuple[str, str]]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    regular, bold = register_fonts(pdfmetrics, UnicodeCIDFont)
    styles = {
        'h1': ParagraphStyle('h1', fontName=bold, fontSize=16, leading=22, spaceAfter=8),
        'meta': ParagraphStyle('meta', fontName=regular, fontSize=9.5, leading=14, textColor='#444444'),
        'q': ParagraphStyle('q', fontName=bold, fontSize=11, leading=17, spaceBefore=10),
        'body': ParagraphStyle('body', fontName=regular, fontSize=10.5, leading=16, leftIndent=12),
    }
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm)
    blocks = [(style, for_pdf(text)) for style, text in blocks]
    missing = missing_glyphs(regular, ''.join(text for _, text in blocks))
    if missing:
        print(f'경고: 글꼴에 없는 글자가 PDF에서 빈칸으로 보입니다: {" ".join(missing)} '
              '(예: d_k처럼 글자로 바꿔 쓰세요)', file=sys.stderr)
    story = []
    for style, text in blocks:
        if style == 'space':
            story.append(Spacer(1, 4))
            continue
        html = escape(text)
        html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)
        story.append(Paragraph(html, styles[style]))
    doc.build(story)


def md_to_blocks(lines: list[str]) -> list[tuple[str, str]]:
    blocks = []
    for line in lines:
        if not line.strip():
            blocks.append(('space', ''))
        elif line.startswith('# '):
            blocks.append(('h1', line[2:]))
        elif line.startswith('- '):
            blocks.append(('meta', line[2:]))
        elif line.startswith('**'):
            blocks.append(('q', line))
        else:
            blocks.append(('body', line.strip().removeprefix('- ')))
    return blocks


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '-', title).strip('-')[:60] or 'quiz'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('quiz_json', type=Path)
    parser.add_argument('--out', type=Path, default=Path('quiz_output'))
    parser.add_argument('--pdf', action='store_true', help='PDF도 만든다')
    parser.add_argument('--combined', action='store_true', help='문제와 정답을 한 파일에')
    args = parser.parse_args(argv)

    quiz = json.loads(args.quiz_json.read_text(encoding='utf-8'))
    problems = validate(quiz)
    if problems:
        print('퀴즈 형식 오류 — 파일을 만들지 않았습니다:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    name = safe_name(quiz['title'])
    if args.combined:
        docs = {f'{name}.md': header_lines(quiz, '문제와 정답') + question_lines(quiz)
                + ['---', '', '## 정답과 해설', ''] + answer_lines(quiz)}
    else:
        docs = {f'{name}-문제지.md': header_lines(quiz, '문제지') + question_lines(quiz),
                f'{name}-정답지.md': header_lines(quiz, '정답지') + answer_lines(quiz)}
    for filename, lines in docs.items():
        (args.out / filename).write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
        print(f'만듦: {args.out / filename}')
        if args.pdf:
            pdf_lines = [l for l in lines if l not in ('---',)]
            pdf_lines = [l.replace('## ', '# ') if l.startswith('## ') else l for l in pdf_lines]
            target = args.out / filename.replace('.md', '.pdf')
            write_pdf(target, quiz['title'], md_to_blocks(pdf_lines))
            print(f'만듦: {target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
