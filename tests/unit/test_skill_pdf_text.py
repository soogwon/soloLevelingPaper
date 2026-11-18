"""skills/의 pdf_text.py가 2단 편집·깨진 파일·그림 라벨을 바르게 다루는지 확인한다."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'skills' / 'solo-leveling-guide' / 'scripts' / 'pdf_text.py'


def _load():
    spec = importlib.util.spec_from_file_location('skill_pdf_text', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdf(streams: list[str]) -> bytes:
    """페이지마다 주어진 내용 스트림을 그대로 쓰는 최소 PDF (Helvetica)."""
    n = len(streams)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               f'<< /Type /Pages /Kids [{" ".join(f"{3 + i} 0 R" for i in range(n))}] /Count {n} >>'.encode()]
    font = 3 + 2 * n
    for i in range(n):
        objects.append(f'<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font} 0 R >> >> '
                       f'/MediaBox [0 0 612 792] /Contents {3 + n + i} 0 R >>'.encode())
    for stream in streams:
        data = stream.encode('latin-1')
        objects.append(b'<< /Length ' + str(len(data)).encode() + b' >>\nstream\n' + data + b'\nendstream')
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    buf, offsets = bytearray(b'%PDF-1.4\n'), []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f'{idx} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(buf)
    buf += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    buf += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    buf += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return bytes(buf)


def _text(x: float, y: float, line: str) -> str:
    return f'BT /F1 10 Tf {x} {y} Td ({line}) Tj ET'


def _two_column_page() -> str:
    ops = [_text(150, 740, 'A Shared Title That Spans Across The Whole Page Width Center')]
    for i in range(14):
        y = 680 - 16 * i
        ops.append(_text(50, y, f'left column line {i:02d}'))
        ops.append(_text(330, y, f'right column line {i:02d}'))
    return '\n'.join(ops)


def _one_column_page() -> str:
    return '\n'.join(_text(50, 700 - 16 * i, f'single column sentence number {i:02d} runs across the middle of the page')
                     for i in range(14))


def test_two_column_page_reads_left_column_before_right(tmp_path):
    pdf = tmp_path / 'two.pdf'
    pdf.write_bytes(_pdf([_two_column_page()]))
    assert _load().main([str(pdf), '--out', str(tmp_path / 'out')]) == 0
    lines = (tmp_path / 'out' / 'page_001.txt').read_text(encoding='utf-8').splitlines()
    assert lines[0].startswith('A Shared Title')
    left = [i for i, line in enumerate(lines) if line.startswith('left column')]
    right = [i for i, line in enumerate(lines) if line.startswith('right column')]
    assert len(left) == len(right) == 14
    assert max(left) < min(right)
    assert not any('left column' in line and 'right column' in line for line in lines)


def test_one_column_page_is_unchanged(tmp_path):
    pdf = tmp_path / 'one.pdf'
    pdf.write_bytes(_pdf([_one_column_page()]))
    assert _load().main([str(pdf), '--out', str(tmp_path / 'out')]) == 0
    text = (tmp_path / 'out' / 'page_001.txt').read_text(encoding='utf-8')
    assert text.splitlines()[0] == 'single column sentence number 00 runs across the middle of the page'
    assert len(text.splitlines()) == 14


def test_unreadable_file_returns_code_2_without_output(tmp_path, capsys):
    bad = tmp_path / 'bad.pdf'
    bad.write_bytes(b'not a pdf')
    assert _load().main([str(bad), '--out', str(tmp_path / 'out')]) == 2
    assert 'PDF를 열 수 없습니다' in capsys.readouterr().err
    assert not (tmp_path / 'out' / 'paper.json').exists()


def test_existing_output_folder_is_not_overwritten(tmp_path):
    pdf = tmp_path / 'one.pdf'
    pdf.write_bytes(_pdf([_one_column_page()]))
    module = _load()
    assert module.main([str(pdf), '--out', str(tmp_path / 'out')]) == 0
    before = json.loads((tmp_path / 'out' / 'paper.json').read_text(encoding='utf-8'))
    assert module.main([str(pdf), '--out', str(tmp_path / 'out')]) == 1
    assert json.loads((tmp_path / 'out' / 'paper.json').read_text(encoding='utf-8')) == before


def test_figure_labels_are_not_heading_candidates():
    module = _load()
    assert module.looks_like_heading('4.2 SQuAD v1.1')
    assert module.looks_like_heading('A.1 Illustration of the Pre-training Tasks')
    assert not module.looks_like_heading('75 BiT ViT-L/32 40')
    assert not module.looks_like_heading('0.3 Depth Depth')
    assert not module.looks_like_heading('3 S E e m g b m e e d n d t ings')
