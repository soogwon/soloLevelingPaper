# 검증 이력 보존·정리 절차 (#82)

범위: #83이 저장하는 의미 검증 이력(`verification_runs`·`verification_claims`·`verification_claim_refs`)의 용량 관리.
학습 맥락·답변 근거(evidences)·청크·논문 파일은 정리 대상이 아니다. 자동 삭제와 예약 실행은 없다.

## 원칙

- 보존 기간은 합의 전이라 **기본값이 없다.** `--older-than-days`를 매번 직접 정해야 하며 값이 없으면 실행하지 않는다.
- 정리 단위는 검증 **실행** 하나다. 실행을 지우면 그 주장·근거 연결도 함께 지운다(참조 → 주장 → 실행 순서).
- 이력은 실행·주장·참조가 한 트랜잭션으로 저장되므로 반쯤 저장된 실행은 보이지 않는다.
  정리는 `created_at`이 기한보다 새로운 실행을 절대 지우지 않는다.
- 변경 명령은 `--yes`가 있어야만 실행한다. 먼저 `plan`(dry-run)으로 대상을 본다.
- 출력과 로그에는 건수·기간·결과 코드만 쓴다. 주장 문구·사유·청크 내용·경로는 쓰지 않는다.

## 명령

```
solo-leveling-verification-retention plan    --db <DB경로> --older-than-days N
solo-leveling-verification-retention run     --db <DB경로> --older-than-days N --yes [--backup-to <백업경로>]
solo-leveling-verification-retention reclaim --db <DB경로> --yes [--backup-to <백업경로>]
```

`--backup-to`는 SQLite 백업 API로 변경 전에 일관된 사본을 만들고, 이미 있는 파일이면 아무것도 바꾸지 않고 중단한다.

## 실행 순서(수동)

1. 앱(MCP 서버)을 쓰지 않는 시간에 한다. 정리는 짧은 쓰기 트랜잭션을 반복하므로 동시에 저장해도 깨지지 않지만,
   쓰기와 겹치면 `result=locked`로 멈춘다. 끝난 배치는 유지되며 다시 실행하면 이어서 지운다.
2. `plan`으로 대상 건수·기간(`eligible_*`, `oldest`, `newest_eligible`)을 확인한다.
3. `run --yes --backup-to ...`를 실행한다. 결과의 `fk_violations`는 0이어야 한다.
4. 공간이 필요하면 `reclaim --yes`를 따로 실행한다.

## 결과 코드

| result | 뜻 | 다음 행동 |
|---|---|---|
| completed | 기한이 지난 이력이 더 남지 않음 | 없음 |
| stopped | 배치 한도에서 멈춤(코드에서만 사용) | 다시 실행 |
| locked | 다른 쓰기와 겹침 | 잠시 뒤 다시 실행 |
| failed | 저장소 오류, 진행 중 배치는 되돌림 | 백업에서 상태 확인 후 문의 |
| insufficient_disk | 공간 회수에 필요한 여유 공간 부족 | 공간 확보 후 재실행 |

## 파일 공간

- 행을 지워도 SQLite 파일 크기는 줄지 않는다. 비워진 공간은 파일 안에서 재사용된다(`reclaimable_bytes`).
- `reclaim`(VACUUM)은 전체 쓰기 잠금이 걸리고, 임시로 DB 크기만큼 더 필요하다. 여유 공간이 DB의 2배 미만이면 하지 않는다.
- VACUUM은 `INTEGER PRIMARY KEY`가 없는 테이블의 rowid를 다시 매길 수 있다. 실제로는 기존 순서대로 다시 넣지만
  SQLite가 보장하는 동작은 아니므로, rowid를 동률 정렬 보조키로 쓰는 조회(#145 퀴즈 이력 등)는 같은 시각의 기록 순서가
  바뀔 수 있다. 그래서 공간 회수는 정리와 분리해 수동으로만 한다.

## 백업·복구

- 정리 전 `--backup-to`로 만든 사본이 복구 수단이다. 복구는 앱을 끈 뒤 DB 파일을 사본으로 교체한다.
- 정리가 지운 이력은 그 사본에만 남는다. 원본 근거(청크)는 지우지 않으므로 context·`get_evidence`·답변은 정리 전후 같다.

## 이번 범위 밖

보존 기간 확정, 일정 기반 자동 정리, 실제 사용자 DB 대상 실행, 이력 조회 UI.
원본 근거가 바뀐 이력의 '재검토 불가' 표시는 이력 조회 계약(조회 쪽)에서 다룬다. 정리는 해당 이력을 최신 데이터로 대체하지 않는다.
