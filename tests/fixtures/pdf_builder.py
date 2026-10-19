"""
tests/fixtures — 테스트용 합성 PDF 생성

이 개발 환경엔 실제 arXiv 샘플 PDF가 없어, reportlab 없이도 pdfplumber가 읽을 수 있는
최소 유효 PDF를 바이트 단위로 직접 구성한다. 페이지마다 다른 텍스트를 넣어
"페이지 경계를 넘지 않는 청킹"을 검증할 수 있게 한다.
"""


def _page_content_stream(text: str) -> bytes:
    lines = text.split("\n")
    ops = ["BT", "/F1 12 Tf", "50 700 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            ops.append("0 -16 Td")
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1", errors="replace")


def build_minimal_pdf(page_texts: list[str]) -> bytes:
    """page_texts 개수만큼 페이지를 가진 최소 유효 PDF 바이트를 반환"""
    objects = []
    n_pages = len(page_texts)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())

    content_obj_start = 3 + n_pages
    for i in range(n_pages):
        content_ref = content_obj_start + i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {content_obj_start + n_pages} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_ref} 0 R >>".encode()
        )

    for text in page_texts:
        stream = _page_content_stream(text)
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buf = bytearray()
    buf += b"%PDF-1.4\n"
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_start = len(buf)
    n = len(objects) + 1
    buf += f"xref\n0 {n}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode()

    return bytes(buf)


def write_minimal_pdf(path: str, page_texts: list[str]) -> None:
    with open(path, "wb") as f:
        f.write(build_minimal_pdf(page_texts))


def build_styled_pdf(pages: list[list[tuple[float, bool, str]]]) -> bytes:
    """줄마다 글자 크기와 굵기를 정할 수 있는 최소 PDF. 섹션 제목 인식 테스트용.

    pages[i]는 (글자 크기, 굵게 여부, 줄 텍스트) 목록이다. 줄은 위에서 아래로 쌓인다.
    """
    objects = []
    n_pages = len(pages)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    content_start = 3 + n_pages
    font_regular, font_bold = content_start + n_pages, content_start + n_pages + 1
    for i in range(n_pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_regular} 0 R "
            f"/F2 {font_bold} 0 R >> >> /MediaBox [0 0 612 792] /Contents {content_start + i} 0 R >>".encode()
        )
    for lines in pages:
        ops = ["BT"]
        y = 740
        for size, bold, text in lines:
            escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            ops.append(f"/{'F2' if bold else 'F1'} {size} Tf")
            ops.append(f"1 0 0 1 50 {y} Tm ({escaped}) Tj")
            y -= size + 8
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", errors="replace")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    buf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_start = len(buf)
    n = len(objects) + 1
    buf += f"xref\n0 {n}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode()
    return bytes(buf)
