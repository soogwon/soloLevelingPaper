# 교재 JSON 형식

`scripts/build_textbook.py`가 읽는 형식이다. 모양(색·글꼴·배치)은 `assets/template.html`이 정하므로
JSON에는 내용만 쓴다. 본문 문자열은 HTML을 쓰지 않고 `**굵게**`, `` `코드` ``, 줄머리 `- ` 목록, 빈 줄 문단만 쓴다.

```json
{
  "title": "어텐션만으로 번역하기: Transformer 핵심 정리",
  "paper": {"title": "Attention Is All You Need", "authors": "Vaswani et al.", "source": "arXiv 1706.03762v7"},
  "goal": "이해",
  "level": "딥러닝 기초를 아는 학부생",
  "date": "2026-11-16",
  "objectives": ["셀프 어텐션이 순환 없이 문맥을 보는 방식을 설명할 수 있다."],
  "path": [
    {"name": "내적", "why": "→ 어텐션 점수 계산에 필요"},
    {"name": "셀프 어텐션", "why": "논문의 핵심 아이디어"}
  ],
  "path_note": "팀이 검수한 개념 지도 기준 순서입니다.",
  "chapters": [
    {
      "title": "스케일드 닷-프로덕트 어텐션",
      "summary": "쿼리와 키의 내적으로 관련도를 재고, 그 비율로 값을 섞는다.",
      "sections": [
        {"type": "text", "body": "각 단어는 **쿼리**, **키**, **값** 세 벡터를 가진다.\n\n- 쿼리: 내가 찾는 것\n- 키: 내가 가진 것"},
        {"type": "formula", "tex": "\\mathrm{Attention}(Q,K,V)=\\mathrm{softmax}\\left(\\frac{QK^\\top}{\\sqrt{d_k}}\\right)V",
         "explain": "√d_k로 나누어 내적이 너무 커지는 것을 막는다."},
        {"type": "evidence", "page": 4, "section": "3.2.1",
         "quote": "We suspect that for large values of d_k, the dot products grow large in magnitude",
         "note": "그래서 소프트맥스 기울기가 매우 작아진다고 설명한다."},
        {"type": "example", "body": "\"그 동물은 길을 건너지 않았다, 그것이 너무 피곤해서\"에서 \"그것\"의 쿼리는 \"동물\"의 키와 내적이 크다."},
        {"type": "tip", "body": "수식이 낯설면 먼저 내적 = 유사도라고만 기억하고 넘어가도 된다."}
      ],
      "check": [{"q": "√d_k로 나누지 않으면 어떤 문제가 생기나요?", "a": "내적이 커져 소프트맥스 기울기가 매우 작아진다 (p.4)."}]
    }
  ],
  "key_points": ["순환 없이 어텐션만으로 문맥을 본다 (p.2)."],
  "glossary": [{"term": "셀프 어텐션", "en": "Self-attention", "definition": "한 시퀀스 안의 위치끼리 서로를 참고하는 어텐션", "page": 2}],
  "next_steps": ["5절 학습 설정을 읽고 구현 체크리스트를 만든다."],
  "notes": "이 교재는 2026-11-16 대화와 논문 원문을 바탕으로 만들었습니다. 쪽 번호는 PDF 쪽 번호입니다."
}
```

## 필드

| 필드 | 필수 | 설명 |
|---|---|---|
| `title`, `paper.title` | 예 | 교재 제목, 논문 제목 (`paper.authors`, `paper.source`는 선택) |
| `goal`, `level`, `date` | 아니요 | 표지의 표시 정보 |
| `objectives` | 아니요 | 학습 목표 2~4개. "~을 설명할 수 있다" 꼴 |
| `path`, `path_note` | 아니요 | 학습 순서. 검수 개념 지도가 있을 때만 그 순서를 쓰고, 없으면 대화에서 실제로 공부한 순서만 쓴다 |
| `chapters[]` | 예 | 장마다 `title`, `sections`(1개 이상), 선택 `summary`, `check` |
| `sections[].type` | 예 | `text`(body) · `formula`(tex, explain) · `evidence`(page, quote, section, note) · `example`(body) · `tip`(body) |
| `key_points`, `glossary`, `next_steps`, `notes` | 아니요 | 마무리 정리. `glossary`는 `term`, `definition` 필수, `en`, `page` 선택 |

수식은 인터넷이 되는 환경에서 KaTeX로 그려지고, 오프라인이면 TeX 원문이 그대로 보인다.
