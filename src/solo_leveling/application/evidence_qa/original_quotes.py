"""인용 원문을 변형하지 않고 요청별 선택 구절을 구성한다."""

from hashlib import sha256
import re


def original_quotes(claims, evidence):
    used = {eid for claim in claims for eid in claim.evidence_ids}
    result = []
    for item in evidence:
        if item.evidence_id not in used:
            continue
        text = item.original_text
        if not text.strip():
            continue
        # 줄바꿈은 PDF 문장 중간에도 있으므로 문장 부호 뒤 공백에서만 분리한다.
        spans, start = [(0, len(text))], 0
        for boundary in re.finditer(r'(?<=[.!?。！？])\s+', text):
            if text[start:boundary.start()].strip():
                spans.append((start, boundary.start()))
            start = boundary.end()
        if text[start:].strip():
            spans.append((start, len(text)))
        for start, end in dict.fromkeys(spans):
            key = f'{item.evidence_id}\0{text}\0{start}:{end}'
            result.append({'quote_id': 'oq_' + sha256(key.encode('utf-8')).hexdigest()[:24],
                'evidence_id': item.evidence_id, 'pdf_page': item.pdf_page,
                'start': start, 'end': end, 'text': text[start:end]})
    return result
