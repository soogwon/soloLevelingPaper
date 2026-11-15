# #397 최신 main 저장·검색·삭제 검증 대조표

## 기준과 실행 근거

- 검토일: 2026-11-15, main `ce52f3d02776b07b25df6f625cf250ab5ece7254`.
- [main CI 실행](https://github.com/soogwon/soloLevelingPaper/actions/runs/43721484441) 및 [Python job](https://github.com/soogwon/soloLevelingPaper/actions/runs/43721484441/job/132834255921): 성공.
- 로그 집계: 단위 3,679 passed / 6 skipped / 69 subtests passed, 통합 353 passed / 29 skipped. 각 단계 warning 1개는 전체 집계에 포함되어 있다.
- CI 설정과 컨테이너 로그에서 PostgreSQL 16 + `pgvector/pgvector:0.8.7-pg16`, `SOLO_TEST_POSTGRES_DSN` 주입을 확인했다. 통합 디렉터리 전체 실행이다. `-q` 로그는 아래 개별 테스트 이름별 결과를 출력하지 않으므로 개별 이름별 실행 로그로 오인하지 않는다.
- 외부 호출을 막은 합성 데이터 검증이다. 실제 Auth0 로그인, R2, 유료 AI, Claude Desktop 원격 연결을 이 CI가 검증한 것으로 보지 않는다.

## 현재 테스트가 확인하는 범위

경로는 저장소 루트 기준이며, 아래 이름은 해당 기준 커밋의 테스트를 읽어 대조했다.

| 종단 표와 연결 | 구현·테스트 근거 | 확인하는 부분 | 아직 필요한 증거 |
| --- | --- | --- | --- |
| E2E-03 | `tests/integration/test_remote_retrieval_postgres.py`의 `test_foreign_context_and_missing_are_same_none` | 다른 owner의 context와 미존재 context가 resolver에서 모두 None | 인증 계정 경계, 외부 HTTP/MCP 응답 status·reason·schema 동일성, job 접근 |
| E2E-12 / EVD-01 | 같은 파일의 `test_actual_prefilter_preserves_allowed_evidence_before_top_k` | owner·페이지·섹션으로 필터링한 top_k에 필수 합성 근거가 남음 | 전체 생성·보충·의미 검증 경로의 strict_pages 전파, 전역 top_k 후 필터 oracle 대조 |
| E2E-11 / EVD-06 | 같은 파일의 `test_republication_does_not_change_old_context_or_old_source`, `test_ready_vectors_reject_direct_mutation` | P2 게시 후 P1 binding·chunk ID·원문 유지, 확정 벡터 수정 거부 | 저장 답변·번역 인용·요약·메모 전체 스냅샷 대조와 호스트 재조회 |
| E2E-13 / EVD-08 | 같은 파일의 `test_deleted_paper_cannot_be_resolved_or_searched_with_old_binding` | 직접 SQL 삭제 후 오래된 binding의 조회·검색 차단 및 연결 행 제거 | 제품 삭제 요청부터 객체 삭제까지의 실행 증거. 이 테스트만으로 tombstone 계약을 판정하지 않음 |
| E2E-13 / EVD-08 | `tests/integration/test_remote_deletions_postgres.py`의 4개 테스트 | tombstone 후 context·검색·publication·PDF 접근·완료 조회 차단, 요청 키 충돌, 미커밋 삭제와 후발 쓰기 경합 거부 | 실제 worker 종료·재시작·외부 객체 삭제 및 이전 작업 재생성 차단의 종단 관찰 |
| E2E-05 / 업로드 복구의 부분 근거 | `tests/integration/test_remote_upload_completion_postgres.py`의 2개 테스트 | 정리 실패·만료 후 완료 결과 재조회 시 복사·PDF 행 중복 없음, 만료 상태 저장과 예약 거부 | 실제 presigned PUT·R2 정리·브라우저 응답 유실, 보관 승인 및 처리 접수 연결 |

업로드 완료 재조회는 실행 원장/예약/AI attempt의 중복 방지와 다르다.
E2E-07/09/10 또는 EVD-07의 추가 provider 호출 0회 근거로 사용하지 않는다.

## 로컬 재검증

동일 main 기반의 문서 브랜치에서 실행:

```sh
python -m pytest -q \
  tests/unit/test_remote_deletions.py \
  tests/unit/test_remote_retrieval_contract.py \
  tests/unit/test_remote_context_resolver.py \
  tests/unit/test_remote_uploads.py \
  tests/unit/mcp/test_remote_context.py \
  tests/integration/test_remote_retrieval_postgres.py \
  tests/integration/test_remote_deletions_postgres.py \
  tests/integration/test_remote_upload_completion_postgres.py
```

결과: **82 passed, 11 skipped**. 로컬 DSN이 없어 DB 11개는 실행하지 못했다.
CI의 DB 실행과 로컬의 skip을 서로 대체하거나 합산하지 않는다.

## 다음 단계와 인수 기준

1. A: 테스트 계정 두 개, 인증/owner 경계, 실제 업로드·실행 원장·삭제·복원 경로를 준비한다.
2. B: 고정 합성 구절의 허용 집합, 답변·인용 대조표와 provider attempt 전후 카운터를 제공한다.
3. C: 같은 실행 별칭으로 웹 업로드·동의·호스트 연결 종료·재로그인·기존 키 재조회를 수행하고 카드/텍스트를 비교한다.
4. 위 근거를 종단 검증표 각 행에 연결한 후 공동으로 완료 여부를 판정한다.

현재는 DB/단위 수준의 부분 증거를 정리한 단계다. 종단 표는 미실행 상태를 유지하며 #397을 종료하지 않는다.
