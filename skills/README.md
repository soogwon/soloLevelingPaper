# Solo Leveling Paper Skills (단독판)

MCP 없이 Claude에 올려 쓰는 학습용 Skill 3개다. 논문 PDF 하나로 대화 안에서 공부하는 체험판이며,
기록 저장·이어 하기·외부 웹은 MCP가 맡는다. 결정 배경은 "Learning Skill 개발·활용 결정안"과 노션
"Skill 버전으로 되는 것과 안 되는 것"을 본다.

| Skill | 하는 일 | 주요 파일 |
|---|---|---|
| `solo-leveling-guide` | 목적(이해·구현·훑어보기)별 읽는 순서, 요약, 키워드 설명, 읽기 전·후 질문, 원문 근거가 붙은 질의응답. 검수 논문 3편(Attention·BERT·ViT)은 개념 지도 학습 순서까지 | `scripts/pdf_text.py`, `scripts/concept_path.py`, `data/seed.json` |
| `solo-leveling-quiz` | 정의·관계·적용 수준 퀴즈를 문제지·정답지 Markdown/PDF로 생성 | `scripts/quiz_export.py`, `fonts/`(나눔고딕, OFL) |
| `solo-leveling-textbook` | 대화 내용을 고정 형식의 HTML 교재 한 파일로 생성 | `assets/template.html`, `scripts/build_textbook.py` |

## 결과물 샘플 (`samples/`)

실제 논문 PDF(Attention Is All You Need, arXiv 1706.03762v7)에 Skill 스크립트를 돌리고, 추출한 본문에서
찾은 원문 인용만 써서 만들었다. 인용문은 모두 해당 쪽 텍스트와 대조했다.

| 파일 | 내용 |
|---|---|
| `guide-attention-understand.md` | `solo-leveling-guide` 대화 흐름 예시 (목적 질문 → 학습 순서 → 읽기 전 질문 → 1단계 → 질의응답 → 요약) |
| `quiz-attention-문제지.pdf`, `quiz-attention-정답지.pdf` (+ `.md`, 입력 `quiz-attention.json`) | `solo-leveling-quiz` 기본값(10문항, 정의 4 · 관계 3 · 적용 3) 결과 |
| `textbook-attention.html` (입력 `textbook-attention.json`) | `solo-leveling-textbook` 결과. 브라우저로 열어 본다 |

실제 대화에서 Claude의 문장은 매번 조금씩 다르지만, 흐름·형식·근거 규칙은 샘플과 같다.

## 복사본 규칙

- `solo-leveling-guide/data/seed.json` = `src/solo_leveling/knowledge/seed.json`
- `solo-leveling-guide/scripts/knowledge_graph.py` = `src/solo_leveling/domain/knowledge_graph.py` (+ 첫 줄 출처 주석)
- `pdf_text.py`는 세 Skill에 같은 파일로 둔다. 추출 설정(x_tolerance 1.5, y_tolerance 5, 세로 글자 제외)은
  `src/solo_leveling/infrastructure/parsing/pdf_extractor.py`와 맞춘다.

원본을 고치면 복사본도 함께 고친다. `tests/unit/test_skill_copies.py`가 어긋나면 실패한다.

## 올려 쓰기

1. Claude 설정에서 코드 실행 기능을 켠다.
2. 각 Skill 폴더를 폴더째 zip으로 묶어 Claude의 Skills 메뉴에서 업로드한다.
3. 논문 PDF를 올리고 "이 논문 공부하고 싶어", "퀴즈 만들어 줘", "오늘 내용 HTML 교재로 만들어 줘"처럼 요청한다.

파일럿 퀴즈 실험 세션 동안에는 참가자 계정에서 이 Skill들을 꺼 둔다(비교 조건에 넣지 않음).
