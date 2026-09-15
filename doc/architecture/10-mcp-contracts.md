# 10. MCP 인터페이스 명세

상태: 로컬 M1 계약 초안 v0.7 (한국어 번역·인쇄 페이지 합의) / 2026-09-07

## 공통 규칙

stdio로 호출한다. 서비스 로그인·HTTP 업로드는 M1에 없다. 사용자 식별은 서버의 로컬 프로필에서 얻고 user_id 입력은 받지 않는다. context·version·evidence가 현재 로컬 데이터와 일치하는지 매 호출 검사한다. 파일 내부 지시나 모델 출력은 실행 명령이 아니다.

도구 결과는 structuredContent와 간단한 text로 제공한다. 등록에는 request_key를 사용하고 동일 로컬 프로필·도구·키에 같은 입력은 기존 결과, 다른 입력은 CONFLICT를 반환한다. 조회 도구는 readOnlyHint를 표시한다.

## 도구

| 도구 | 필수 입력 | 선택 입력 | 출력 |
|---|---|---|---|
| add_paper | source, request_key | 없음 | paper_id, version_id, job_id, status |
| get_paper_status | job_id | 없음 | status, stage, capabilities, limitations |
| start_learning | version_id, request_key | goal, known_concepts | context_id, 고정 버전, summary, summary_status |
| ask_paper | context_id, question | standalone_question, focus | status, answer_ko, claims, citations, reason_code |
| get_learning_guide | context_id | view, focus_concept_id | basis, steps, limitations |
| get_evidence | context_id, evidence_ids | 없음 | 한국어 번역·추출 원문·인쇄 번호·물리 페이지·파일 표시 이름 |

이름은 기존 프로젝트 계약을 유지하며 C 문서의 register_paper/evidence_qa 등과 기능상 대응한다. 호스트 Skill이 없어도 핵심 도구로 접근 가능하다.

## 파일 등록

로컬 입력 예:

```json
{"kind":"local_file","relative_path":"sample.pdf"}
```

사용자가 설정한 import 루트 기본 runtime/imports 기준이다. 실제 경로를 해석해 루트 안의 일반 PDF인지 확인하며 절대 경로·상위 탈출·symlink/junction 탈출을 거부한다. 폴더 전체 자동 스캔이나 다른 경로 탐색을 허용하지 않는다. 관리되는 runtime/pdfs로 복사해 해시·버전을 기록하며 사용자 제공 원본은 수정·삭제하지 않는다.

URL 입력은 {"kind":"url","url":"https://example.org/paper.pdf"}다. 허용 출처 PDF만 수집하며 웹페이지 본문으로 대체하지 않는다. title은 로컬 등록 목록의 후보 조회로 제한한다. 후보 선택은 새 request_key로 처리한다. HTTP upload_id 입력과 POST /uploads는 후속 공동 서버 계약이다.

초기 제안 한도는 30MB·150페이지이며 실제 파서·장비에서 검증한다. PDF 추출 불가 구간은 limitations로 반환한다.

## 작업·컨텍스트

등록 시 SQLite에 작업을 기록하고 프로세스 내부 실행기가 처리한다. queued/processing/ready/failed/interrupted를 구분한다. 단계는 download/parse/translate/index다. 한 번에 색인 1건부터 시작하고 retry_after_seconds로 상태 조회를 안내한다. 번역 실패 구간을 limitations에 기록하고 정상 번역·색인 구간이 없으면 ready로 게시하지 않는다.

재시작 시 미완료 processing 작업은 interrupted로 표시한다. 자동 이어 실행을 보장하지 않는다. 새 키로 같은 원본을 재등록해 재시도하되 부분 청크/벡터를 게시하지 않고 중복·고아 데이터를 정리한다. 삭제된 요청 결과는 재생성하지 않는다.

goal은 understand/implement/skim, 생략 시 understand다. 목적이 이미 있으면 재질문하지 않는다. known_concepts는 자기보고이며 유일하게 매칭된 이름만 사용한다. context는 원본·파싱·번역 리비전·검수 그래프 버전을 고정한다.

## 답변·근거

status는 ok/partial/insufficient_evidence/needs_clarification이다. claims는 주장 ID·텍스트·유효한 evidence_ids를 가진다. citations에는 원본·파싱·번역 리비전, printed_page_label, pdf_page, quote_ko, quote_original을 연결한다. 한국어 발췌는 '한국어 번역'으로 표시하며 원문 그대로의 인용으로 표현하지 않는다.

printed_page_label은 인쇄 번호를 확인한 문자열이며 사용자 표시의 기본이다. 없거나 불확실하면 null로 두고 'PDF 기준 N페이지'로 대체한다. pdf_page는 첫 페이지=1인 실제 파일 위치이며 이동·검증에 사용한다. 인쇄 번호 중복을 허용하고 단독 ID로 사용하지 않는다. focus는 물리 pdf_page 또는 section_id로 해석하며, 인쇄 번호만 받은 호스트는 중복 여부를 확인해 물리 위치로 변환하거나 확인 질문을 한다.

청크의 text는 한국어 번역문, original_text는 추출 원문이다. 기본 검색·생성은 text를 사용하되 의미 검증에는 원문도 제공한다. quote_ko는 번역문, quote_original은 추출 원문에 대해 각각 발췌 일치를 검사한다. 원문과 번역문 사이 문자 offset이 같다고 가정하지 않는다. 자세한 필드와 재번역 정책은 [22번 합의](22-local-m1-baseline.md)를 따른다.

서버가 실제 근거로 ID와 페이지를 연결한다. 모든 주장에 구조 검증을 적용하고 수치·비교·인과는 의미 검증한다. 실패 주장은 제한된 수정 후 제거하거나 보류하고, 최종 claims로 answer_ko를 조립한다. 모델 검증은 정확성 보장이 아니다.

후속 질문은 원 질문을 보존하고 지시 대상이 명확할 때만 standalone_question을 보충한다. 근거 부족 사유는 evidence_not_found/extraction_limited/conflicting_evidence/unsupported_scope/verification_failed로 구분한다. 외부 API 장애는 UPSTREAM_UNAVAILABLE이며 근거 없음으로 숨기지 않는다.

get_evidence는 해당 context에 고정된 번역 리비전의 번역문과 연결된 추출 원문만 반환한다. 호스트가 파일 열기를 지원하지 않으면 파일명·인쇄 번호(불명 시 명시적 PDF 기준 번호)·인용문으로 안내하며 임의 파일 열기 명령을 생성하지 않는다. 가이드는 검수 계획이 없으면 일반 섹션 가이드로 제한한다.

## 주요 오류

INVALID_ARGUMENT, NOT_FOUND, PAPER_NOT_READY, UNSUPPORTED_DOCUMENT, DOWNLOAD_FAILED, EXTRACTION_FAILED, CONFLICT, REQUEST_IN_PROGRESS, RATE_LIMITED, UPSTREAM_UNAVAILABLE, INTERNAL_ERROR를 구분한다. 로컬 경로 범위 위반은 INVALID_ARGUMENT으로 차단하고 절대 경로·키·스택을 오류 본문에 노출하지 않는다.

원격 인증·upload 만료 오류는 후속 계약에서 다시 정의한다. M2 퀴즈·인용 그래프·메모 도구는 M1에 노출하지 않는다.
