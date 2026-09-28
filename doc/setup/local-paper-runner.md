# PDF 로컬 실행기

PowerShell에서 저장소 루트로 이동한 뒤 실행한다.
설정은 저장소 `.env`를 읽고 같은 이름의 프로세스 환경 변수가 우선한다.

```powershell
cd D:\fd\soloLeveling
# 입력·설정 형식만 확인한다. 키의 유효성이나 PDF 파싱 성공은 보장하지 않는다.
.\.venv\Scripts\python.exe scripts/run_paper.py "D:\papers\sample.pdf" --question "주요 실험 결과는?"
# 실제 원문·질문·근거 외부 전송과 비용이 발생한다.
.\.venv\Scripts\python.exe scripts/run_paper.py "D:\papers\sample.pdf" --question "주요 실험 결과는?" --live
```

필요한 경우 의존성을 먼저 설치한다.
`python -m pip install -e ".[local,generation,translation]"`
실제 실행에는 `.env`의 `ALLOW_EXTERNAL_API=true`, `LOCAL_ONLY=false`와 유효한 키가 필요하다.
`--env-file`로 설정 경로, `--top-k`로 초기 검색 개수(1~20, 기본 5)를 지정한다.

## 처리와 출력

PDF 등록 → 청크별 번역 → 로컬 임베딩·색인 → 질문 검색·답변 → 저장된 근거 재조회.
등록 상태·limitations, 답변·claims·citations, 원문·번역·페이지·파일명을 출력한다.
근거 부족도 정상 답변 결과이며 종료 코드 0이 될 수 있다. status·reason_code를 확인한다.
구조 검증만 수행하며 사실성이나 번역 품질을 보장하지 않는다.

실행마다 시스템 임시 폴더에 `paper-local-` 접두사의 별도 DB·Chroma를 만든다.
경로는 콘솔에 출력하며 성공·실패·중단 후에도 자동 삭제하지 않는다.
기존 서비스 DB와 원본 PDF는 수정하지 않는다. 원본 파일은 복사하지 않으므로 보존해야 한다.
테스트 폴더에는 논문 원문·번역·근거가 남으므로 민감한 자료의 보관에 주의한다.

## 범위와 비용

- 매 실행은 새 등록이다. 이전 번역 재사용·기존 version 질문 옵션은 없다.
- 먼저 1~2페이지의 공개 PDF로 시험한다. 청크마다 번역 요청하며 긴 논문은 비용·시간이 증가한다.
- 답변 생성은 근거 부족 시 추가 검색으로 최대 두 번 호출될 수 있다.
- 임베딩 모델 최초 사용 시 다운로드가 필요하고 로컬 모델 캐시가 생성될 수 있다.
- PDF 헤더 검사만 사전 수행한다. 스캔 PDF OCR·크기/페이지 한도·비용 상한은 구현하지 않았다.
- 종료 코드: 0 완료/설정 확인, 1 처리 실패, 2 입력/설정/허용 오류, 130 사용자 중단.
- 이 도구는 사용자가 직접 지정한 파일을 처리하는 로컬 CLI다. MCP import 루트 보안이나 서버 작업 실행기를 대체하지 않는다.

자동 테스트: `python -m pytest tests/unit/test_paper_runner.py -v`
