"""명확한 중앙 여백이 있는 2단 페이지의 읽기 영역을 나눈다."""

from collections import Counter


def reading_regions(page, *, x_tolerance: float, y_tolerance: float):
    """불확실한 배치는 원래 페이지를 반환한다. 단어를 재조합하지 않는다."""
    rows = {}
    for word in page.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance):
        rows.setdefault(round(word['top'] / 3), []).append(word)
    lines = [rows[key] for key in sorted(rows)]
    if len(lines) < 10:
        return [page]
    x0, top, x1, bottom = page.bbox
    mid = (x0 + x1) / 2
    margin = 9.0
    split = top
    body_started = False
    left_rows = right_rows = 0
    for line in lines:
        # 일반 단어 간 공백을 단 사이 여백으로 잘못 해석하지 않는다.
        if any(w['x0'] < mid + margin and w['x1'] > mid - margin for w in line):
            if body_started or min(w['top'] for w in line) >= top + page.height * 0.45:
                return [page]
            split = max(split, max(w['bottom'] for w in line) + 0.5)
            continue
        body_started = True
        left_rows += any(w['x1'] <= mid - margin for w in line)
        right_rows += any(w['x0'] >= mid + margin for w in line)
    if min(left_rows, right_rows) < 5:
        return [page]
    # 제목은 폭 전체로 보존하고 이후 왼쪽 단, 오른쪽 단 순으로 추출한다.
    regions = [page.crop((x0, top, x1, split))] if split > top else []
    regions += [page.crop((x0, split, mid, bottom)), page.crop((mid, split, x1, bottom))]
    # crop은 경계에 걸친 글자를 잘라 양쪽에 남길 수 있다. 좌표와 개수를
    # 포함해 원래 문자 집합과 같을 때만 분할 결과를 사용한다.
    def characters(chars):
        return Counter((c['text'], c['x0'], c['x1'], c['top'], c['bottom']) for c in chars)

    if characters(page.chars) != characters(c for region in regions for c in region.chars):
        return [page]
    return regions
