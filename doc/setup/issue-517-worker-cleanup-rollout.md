# #517 새 DB 이후 worker·정리 실행 준비

## 현재 상태와 분리 원칙

- 웹: v2 DB의 제한 웹 계정, migration check, 시작 시 두 cleanup 비활성.
- 처리 worker: `slp_staging_worker`, schema `slp_staging`. standby에는 DB/R2/API 자격을 주입하지 않는다.
- 정리: 별도 계정과 자격이 필요하다. 현재 cleanup 계정은 아직 구성·검증하지 않았다.
  웹/처리 worker/DB 관리자 자격을 정리용으로 재사용하지 않는다.
- 이 문서는 자원 생성·결제·R2 삭제·AI 전송 승인을 대체하지 않는다.

## standby 배포 입력

| 항목 | 값 |
| --- | --- |
| 서비스 | Background Worker, Oregon, 인스턴스 1개 |
| 이름 | solo-leveling-worker-staging |
| 이미지 | ghcr.io/soogwon/solo-leveling-worker@sha256:5998d4188de1c644be9e24611606850cf3e5c8e5e6a679dbc9b0c33e9df17bc4 |
| 실행 | 이미지 기본 ENTRYPOINT와 CMD 유지 (`--mode standby`) |
| 자격 | GHCR 비공개 이미지 pull 전용 자격만 사용자 입력 |
| 금지 | DB/R2/OpenAI 키, verify-once 설정, 외부 API 승인 플래그 주입 금지 |
| 실제 사양 | 1 CPU/2GB, 인스턴스 1개 |
| 비용 | 2026-11-17 생성 화면 월 $25 확인. standby 중에도 과금 |

성공 조건은 고정 `render_ready` 로그, 재시작 시 정상 사전 검사, 종료 처리다.
standby 성공은 queue 처리나 PDF 종단 처리 성공이 아니다. 대기 중에도 비용이 발생한다.
검증 종료 후 계속 대기할지 중지할지 사용자와 결정한다. 기존 게시 이미지의 코드 커밋은
최신 main과 다르므로 실제 verify-once 전에 현재 DB/코드 계약 호환성을 재검증하고 필요하면 재게시한다.

## 배포와 최신 main 비교 (2026-11-17)

배포 이미지의 기준 커밋은 `9fadf7edf3dbbcb9dab4f24005a5752f3f4121c9`,
비교한 main은 `d34b883f547f086480cc5747b8ab4509687982bb`다.
`git diff`로 `src/solo_leveling`, `deploy/worker`, `pyproject.toml`의 차이가 없음을 확인했다.
이후 5개 커밋은 제한 권한·복구 검증과 웹/Blueprint 설정 변경이다.
따라서 이 비교 시점에는 worker 이미지 재게시가 필요하지 않다.
향후 소스·의존성·모델 입력이 바뀌면 다시 비교하고, 기존 digest를 최신 이미지라고 간주하지 않는다.
이 결론은 실제 DB/R2 연결 검증을 대신하지 않는다.

Render 서비스 `srv-dbu3edu7bikc7386kpd0`의 배포
`dep-dbu3eee7bikc7386kqc0`가 20:15:45 KST Live가 되었고
`render_ready`, `render_standby`를 확인했다.
기본 preflight는 파일·패키지 무결성 검사이며 모델 로딩은 하지 않는다.
별도 Web Shell 프로세스에서 로컬 모델과 합성 문장 1개를 사용해 검증했다.
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `local_files_only=True`,
`trust_remote_code=False`, CPU 1 thread 조건이다.

- 결과 `[1, 384]`, 모든 값 finite
- import 이후 모델 생성+추론 1.64초 (전체 기동 시간 아님)
- 프로세스 최대 RSS 1090.02 MiB
- cgroup peak 972.32 MiB, 한도 2048 MiB, OOM/kill 0
- 검증 프로세스 종료 후 cgroup 현재 메모리 48.16 MiB

RSS와 cgroup는 산정 방식이 다르다. 종료 후 대기 메모리는 모델 상주 메모리가 아니다.
실제 PDF 전체 처리나 동시 처리에 2GB가 충분하다는 결론은 아직 낼 수 없다.
PDF 전송·AI 호출·DB 변경·R2 삭제는 수행하지 않았다.

## 별도 정리 실행 준비

모델 이미지 대신 서버 Dockerfile 이미지의 `python /app/deploy/cleanup/run.py`를 사용한다.
기본 실행은 계획만 출력하고 DB/R2를 읽지 않는다.

```sh
python /app/deploy/cleanup/run.py uploads
python /app/deploy/cleanup/run.py deletions
```

실제 실행 전 준비:

1. `slp_staging_cleanup` 전용 계정의 최소 권한을 로컬 PostgreSQL에서 검증한다.
   uploads는 intent/upload의 조회·행 잠금·상태 변경이 필요하고,
   deletions는 tombstone·lease 확인과 논문 파생 테이블 삭제가 필요하다.
   둘의 권한을 합칠지 별도 계정으로 나눌지는 회귀 검증 결과로 확정한다. 아직 실제 DB에 grant하지 않는다.
2. 제한 계정으로 migration 이력 조회를 허용하되 schema CREATE와 migration 적용을 금지한다.
3. R2 자격을 검증 bucket 범위로 제한하고 삭제 대상·보관 정책을 승인받는다.
   기존 R2 객체를 새 DB의 orphan으로 간주해 일괄 삭제하지 않는다.
4. `APP_ENV=staging`, `SLP_CLEANUP_DATABASE_URL`, 기존 CLI의 `OBJECT_STORAGE_*`를 전용 실행 환경에 입력한다.
5. 승인 후에만 `SLP_CLEANUP_APPROVED=true`와 `--apply`를 함께 사용한다.

```sh
python /app/deploy/cleanup/run.py uploads --apply
python /app/deploy/cleanup/run.py deletions --apply
```

실행은 migration check 후 선택한 명령만 실행한다. uploads 최대 100개,
deletions 최대 10개/lease 300초다. 각 자식 명령의 최대 대기 시간은 600초이며
실패·시간 초과 시 자동 재시도하지 않는다. 원본 자식 출력은 폐기하고 고정 상태만 기록한다.
시간 초과는 이미 수행된 DB/R2 변경을 되돌리지 않으며, lease와 멱등 상태 확인 후 수동 재시도한다.

주기 실행은 아직 설정하지 않았다. 후보는 별도 Render Cron Job 두 개이며 생성 화면에서
요금·스케줄(UTC)·명령을 검토한 뒤 승인받는다. 모델 worker와 같은 프로세스에 붙이지 않는다.
root render.yaml에는 유료 worker/cron을 추가하지 않아 Blueprint 동기화로 자원이 생성되지 않는다.

## 역할별 남은 범위

- A: cleanup 제한 역할 실DB 회귀와 별도 자격, 실행 주기. GHCR 연결·2GB 생성은 완료.
- B: 단문 모델 실측은 완료. verify-once 전 새 DB 연결·사용량·동의 계약과 실제 처리 메모리 검증.
- C: standby 고정 로그와 정상 종료 확인, 실제 처리와 준비 상태 구분, 후속 웹 종단 확인.

공식 자료: https://render.com/pricing , https://render.com/docs/background-workers ,
https://render.com/docs/cronjobs .
