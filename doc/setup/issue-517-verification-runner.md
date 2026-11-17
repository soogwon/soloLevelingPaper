# #517 단발 worker 검증 진입점

## 이번 범위

`solo-leveling-worker-verify` 또는 `python -m solo_leveling.interfaces.cli.processing_verification`를 제공한다.
기본 동작은 로컬 JSON 검사뿐이다. 모델 import·DB 연결·runtime import·파일 업로드·API 호출을 하지 않는다.
검증 실행은 `--execute --runtime 검토한모듈:factory`를 모두 지정해야 한다.
Docker 기본 entrypoint는 여전히 이미지 사전 검사이며 자동 worker 시작으로 바꾸지 않는다.

## 비밀정보 없는 설정

아래 값은 합성 예시이며 실제 접수 후 검증할 단일 job 정보로 교체한다. 키·DSN·PDF 본문은 넣지 않는다.

```json
{
  "database_name": "verification",
  "schema_name": "verification_test",
  "owner_user_id": "synthetic-owner",
  "job_id": "00000000-0000-0000-0000-000000000001",
  "paper_id": "00000000-0000-0000-0000-000000000002",
  "version_id": "v1",
  "payload_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "worker_id": "verification-worker",
  "lease_seconds": 60,
  "max_staging_bytes": 100000
}
```

`payload_hash`는 PDF SHA-256이 아니라 **접수 job의 payload_hash**다.
단일 owner/job/paper/version/hash가 모두 일치하는 queued·attempt=0·full_processing만 claim한다.
실행은 attempt=1·retryable=false로 고정하며 다른 queued/만료 job을 정리하지 않는다.
대상이 없거나 이미 실행했으면 `not_claimed`로 끝난다. 자동 polling·재claim·추가 접수는 없다.
실패 후 재실행을 위해 attempt를 수동으로 초기화하지 않는다. usage·동의·staging을 확인한 별도 재시도 정책이 필요하다.

```sh
python -m solo_leveling.interfaces.cli.processing_verification --config /secure/verification.json
```

## 격리 환경과 runtime 계약

운영 DB가 아닌 일회용 검증 DB와 전용 비공개 schema를 준비하고 migration 0037까지 적용한다.
검증 DB 자격에는 운영 DB 접근 권한을 주지 않는다. 이름 검사는 운영/검증 DB의 진위를 증명하지 않는다.
매 연결에서 current_database/current_schema 및 전체 명시 search_path가 설정과 정확히 일치해야 한다.
public·시스템 schema와 다른 schema fallback은 거부한다. PostgreSQL pool은 dict_row와 검증 schema 단독 search_path를 설정한다.
기존 전역 worker를 이 검증 DB에 동시에 연결하지 않는다.

runtime은 사용자가 업로드한 파일이나 JSON에서 선택하는 확장이 아니라 **운영자가 검토한 신뢰하는 Python 코드**다.
`factory(config)`는 context manager이며 아래 속성이 있는 resources를 yield한다.

| 속성 | 계약 |
| --- | --- |
| pool | 검증 DB 전용 psycopg pool. context 종료 시 close 필수 |
| objects | 검증 R2 전용 object reader. 읽기 자격과 대상 버킷을 제한 |
| stage_options | create_processing_worker의 명시적 service_config, model_dir, embedding_spec, limits, retry_policy, policy_version, window_key, token_upper_bound, batch_size |

factory는 pool·스토리지 생성 도중 실패한 경우에도 이미 만든 자원을 닫아야 한다.
runner가 pool을 감싸 모든 worker·heartbeat·권한·게시 연결에서 DB/schema를 검사하고 지정 작업용 queue를 주입한다.
bootstrap이 queue·worker ID·lease·staging 제한을 덮어쓰는 옵션은 거부한다.
운영 bootstrap, 실제 Linux 의존성 lock, 모델 파일, 토큰 상한 추정기는 이번 변경에 임의 기본값으로 제공하지 않는다.
실제 서비스 키는 환경 secret에서 읽고 이미지나 검증 JSON에 포함하지 않는다.

승인과 준비가 완료된 뒤에만 다음 형태로 실행한다. 아래 모듈은 배포 환경에서 별도 준비할 이름의 예시다.

```sh
python -m solo_leveling.interfaces.cli.processing_verification \
  --config /secure/verification.json --execute --runtime approved_runtime:factory
```

## 결과·실패

- `config_valid`(0): 오프라인 설정 검사만 성공. DB·모델·처리 성공 증거가 아니다.
- `published`(0): 내부 worker가 publication을 반환했다. 실제 검색·웹 표시 검증은 별도다.
- `not_claimed`(3): 일치하는 최초 queued 작업 없음. 성공/취소/권한 사유를 추정하지 않는다.
- `not_published`(3): 실행 결과가 게시되지 않았다. usage·동의와 저장 상태를 별도로 확인한다.
- `verification_failed`(1): 설정/실행/자원 정리 오류 또는 사용자 중단. 원문 예외·DSN·키를 출력하지 않는다.

내부 Python stdout/stderr/logging은 실행·정리 동안 폐기하고 고정 상태만 출력한다.
신뢰하는 bootstrap/네이티브 라이브러리가 직접 FD나 외부 로그로 쓰는 행위까지 격리하는 sandbox는 아니다.
프로세스 강제 종료의 자원 정리·파서 제한은 컨테이너/운영 환경에서 별도로 보장해야 한다.
실패 시 job을 성공 처리하거나 usage를 환급/삭제하지 않으며 남은 lease는 운영자가 확인해야 한다.

## 검증과 남은 범위

A: 실제 PostgreSQL에서 정확한 단일 claim, 다른 queued/만료 job 불변, 잘못된 scope/DB 거부,
동시 실행 단일 승자, 만료 후 자동 재claim 금지를 검사한다.
B: 단발 runner는 기존 composition·동의·usage·게시 포트를 사용하며 모델·토큰 정책 기본값을 만들지 않는다.
C: 기본 오프라인 실행, runtime 미지정 거부, 비공개 출력 차단, 중단/예외 후 자원 정리와 상태 코드를 검사한다.

실제 배포·PDF 전송·유료 API·실제 모델 추론은 수행하지 않는다. 이후 Linux runtime/모델·secret 주입 bootstrap,
검증용 DB/R2, 자원 제한, PDF별 호출/토큰/비용/보관 승인, 단발 종단검증을 준비해야 한다.
이 코드의 --execute 옵션은 사용자 승인이나 제품 동의를 대신하지 않는다. #517은 열어둔다.
