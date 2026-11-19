# 원격 복구·재파싱 미리보기

Refs #594. 이번 변경은 조회 전용 1차 연결이며 티켓 완료가 아니다.

`get_remote_paper_detail`의 `reprocessing_preview` 안내에서
`preview_remote_reprocessing(context_id, operation)`을 호출한다.
operation은 `translation_recovery` 또는 `reparse`다.
기존 제품 조회 flag·검증된 `papers:read`·계정 연결을 그대로 사용한다.

- 사용자 맥락에 고정된 publication·parse/translation revision·embedding set·모델 지문을 반환한다.
- 번역 복구 후보는 번역 행 누락, translation_failed, original_only다.
  translated·reference_content·translation_not_required는 제외한다.
- 재파싱 대상은 해당 PDF 판본 1개다. 현재 청크/페이지 집계는 새 파싱 결과나 PDF 전체 페이지 수를 뜻하지 않는다.
- 원문·번역 본문, 객체 경로, 자격, 원본 오류는 반환하지 않는다.
- 비용은 미산정(null)이며 0원 견적이 아니다. 전송 항목은 잠정 안내이고 실제 목적지·모델·본문·비용은 접수 전에 다시 확정해야 한다.
- 준비 조회는 작업·요청 키·동의·예산을 만들지 않는다. 기존 context와 active pointer도 바꾸지 않는다.
- submission_available=false다. PDF 존재·보관 동의와 예산, 복구 가능성은 아직 검사하지 않는다.

## 검증

단위/MCP 등록 10개와 로컬 PostgreSQL 회귀 10개 통과.
합성 자료만 사용해 실패/원문 전용/누락 집계, 제외 상태, 과거 게시 고정,
새 게시 유지, 반복 조회, 타 계정·삭제 차단 및 조회 전후 테이블 행 수 불변을 확인했다.
게시 후 수정 방어를 우회하지 않고 합성 상태는 게시 전에 구성했다.
추가 staging 변경·외부 API 호출은 없다.

## 다음 범위

1. 인증 웹 검토·복구 유형별 동의와 예산 확정, 접수 시 소유권·revision·PDF 재검사.
2. owner/context/request key 결합 접수·같은 키 복구, #595 상태·취소 연결.
3. #587 fragment/original-only 정책을 실제 복구 worker와 연결하고 새 결과를 원자 게시.
4. 명시적 새 색인 선택 전 기존 학습 맥락·과거 근거 보존.
5. 별도 승인 후 staging 권한 점검·배포·Claude 카드 및 실제 종단 검증.

이번 쿼리는 기존 제품 조회의 server_papers, server_execution_contexts,
server_processing_publications, server_embedding_sets, server_processing_chunks,
server_translation_chunks를 사용한다. 새 migration·DB 역할은 추가하지 않았다.
