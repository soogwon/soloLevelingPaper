def build_layout_pdf(streams: list[str]) -> bytes:
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
