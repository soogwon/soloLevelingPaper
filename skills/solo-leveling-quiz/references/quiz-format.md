# 퀴즈 JSON 형식

`scripts/quiz_export.py`가 읽는 형식이다. 이 형식으로 `quiz.json`을 쓴 뒤 스크립트로 파일을 만든다.

```json
{
  "title": "Attention Is All You Need 이해도 퀴즈",
  "paper": "Vaswani et al., Attention Is All You Need (arXiv 1706.03762v7)",
  "goal": "이해",
  "note": "자료 없이 풀어 보세요. 10분 권장.",
  "questions": [
    {
      "level": "정의",
      "type": "multiple_choice",
      "prompt": "스케일드 닷-프로덕트 어텐션에서 점수를 sqrt(d_k)로 나누는 이유로 가장 알맞은 것은?",
      "choices": ["계산량을 줄이려고", "내적 값이 커져 소프트맥스 기울기가 매우 작아지는 것을 막으려고",
                  "출력 차원을 맞추려고", "학습률을 대신하려고"],
      "correct_index": 1,
      "explanation": "d_k가 크면 내적의 크기가 커져 소프트맥스가 기울기가 매우 작은 영역으로 밀린다고 논문이 설명한다.",
      "evidence": [{"page": 4, "quote": "We suspect that for large values of d_k, the dot products grow large in magnitude"}]
    },
    {
      "level": "적용",
      "type": "short_answer",
      "prompt": "순서 정보가 없는 셀프 어텐션 모델에 단어 순서를 알려 주려면 입력에 무엇을 더해야 하나요?",
      "answer": "위치 인코딩(positional encoding)",
      "grading_criteria": "위치 인코딩·위치 임베딩·positional encoding 모두 정답. '순서 정보'만 쓰면 부분 점수.",
      "explanation": "모델에 순환도 합성곱도 없어서 토큰의 위치 정보를 임베딩에 더한다.",
      "evidence": [{"page": 6, "quote": "we add \"positional encodings\" to the input embeddings"}]
    }
  ]
}
```

## 필드 규칙

| 필드 | 규칙 |
|---|---|
| `level` | `정의`(용어·구성 요소가 무엇인지), `관계`(개념 사이의 이유·비교·인과), `적용`(새 상황에 써 보기) |
| `type` | `multiple_choice`: `choices` 3~5개, `correct_index`(0부터). `short_answer`: `answer`와 `grading_criteria` |
| `explanation` | 왜 그 답인지 한두 문장 |
| `evidence` | 하나 이상. `page`는 PDF 쪽 번호, `quote`는 그 쪽 텍스트 파일에서 실제로 찾은 원문 짧은 인용 |
| 문항 수 | 1~30개. 같은 문항 중복 금지 |

PDF로 만들 때 내장 한글 글꼴에 없는 기호(일부 수학 기호, 이모지)는 빈칸으로 나올 수 있다.
수식은 `sqrt(d_k)`처럼 글자로 쓰거나, PDF를 연 뒤 깨진 글자가 없는지 확인한다.
