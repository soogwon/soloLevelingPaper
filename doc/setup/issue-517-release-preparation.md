# #517 이미지 게시·검증 환경 설정 준비

기준: main 5cecc25 (#549 병합), 2026-11-17 Asia/Seoul.
GHCR private runtime·worker 게시와 재검증은 사용자 승인을 받았다. 아래 수동 Actions로 진행하며,
cloud DB/R2 변경, Render 생성/배포, 실제 PDF 전송 및 AI 호출은 별도 승인 전 수행하지 않는다. 비밀정보·실제 사용자 식별자를 포함하지 않는다.

## 1. 검증한 입력과 게시 후보

- 로컬 최종 이미지 ID: sha256:d7c9d1b2890939179e85992c7133ed29a6c8b55c442f2a455a391145ec90827e
- Linux amd64, 로컬 비압축 크기 2,666,186,832 bytes. registry 전송량/청구 용량과 같지 않다.
- Python base: python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
- 고정 모델 revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42, 10개 파일 해시 검증.
- 네이티브 Linux 결과/전체 의존성 lock: /private/tmp/slp517-native-linux-report.
- 로컬 Docker 결과/전체 lock: /private/tmp/slp517-linux-actual/reports.
- CI 증거: https://github.com/soogwon/soloLevelingPaper/actions/runs/44010226518 (artifact 7일).

최종 이미지에는 localhost runtime reference가 포함되어 있다. 외부 배포용으로 단순 tag만 붙이기보다,
아래 순서로 외부 reference와 runtime-lock을 일치시킨 새 최종 이미지를 검증한다.
검증한 wheel hash lock을 고정하며 새로 의존성을 해석하지 않는다. 현재 모델·wheel·이미지는 외부에 게시하지 않았다.

## 2. 승인된 게시 경로

승인한 목적지(게시 결과와 digest는 실행 artifact 및 #517에 기록):
- ghcr.io/soogwon/solo-leveling-worker-runtime — private
- ghcr.io/soogwon/solo-leveling-worker — private

전송 내용: Linux/Python·공개 CPU 라이브러리, 저장소 worker Python 코드, 고정 공개 모델과 metadata.
논문·사용자 기록·DB 덤프·환경 파일·토큰·API 키는 포함하지 않는다. 모델/패키지 라이선스·NOTICE 포함 여부를
게시 직전에 확인하며 필요한 고지는 모델 해시 대상 디렉터리 밖에 둔다.

게시 순서:
1. 두 package가 이미 존재하고 private인지 확인한다. 404도 중단한다. 신규 package 기본 공개 범위에 의존하지 않으며, 기존 package 정책을 임의 변경하지 않는다.
2. 검증 runtime을 게시하고 registry가 반환한 RepoDigest를 기록한다. 로컬 image ID와 혼동하지 않는다.
3. 해당 외부 runtime reference로 runtime-lock을 다시 캡처한다.
4. 같은 모델/소스/전체 의존성 lock으로 render target을 재빌드한다.
5. network=none에서 모델 추론/지문/기본 대기/종료를 재검증한다.
6. 최종 worker를 게시하고 RepoDigest·원본 commit·lock·재검증 결과를 기록한다.
7. Render에는 나중에 별도의 read:packages 자격과 최종 digest를 설정한다. 게시용 쓰기 자격을 전달하지 않는다.

`worker-publish.yml`의 수동 Actions에서 일회성 GITHUB_TOKEN의 packages:write 권한을 사용한다.
main에서만 실행하며 게시 전후 private 여부를 검사하고, 게시 digest를 다시 내려받아 오프라인 검증한다. 토큰을 채팅/Git/이미지에 넣지 않는다.
GHCR container 저장소/전송은 공식 문서상 현재 무료이나 Actions 및 향후 정책·계정 사용량은 별개다.

## 3. DB 준비안 — 기존 환경 읽기 확인 완료

Render workspace에는 staging 웹과 `solo-leveling-staging-db` 두 서비스만 표시된다.
DB는 PostgreSQL 18 Free이며, 별도 worker 검증용 DB 서비스는 없다.
기존 DB 내부의 별도 database/schema 유무는 아직 확인하지 않았다. 기존 staging DB에 변경하지 않았다.

- 운영 DB와 분리된 검증 DB, 전용 schema와 역할. 기본 이름 제안은 slp_worker_verify / worker_verify.
- 연결 대상 host/DB 소유자·비용·삭제 일정을 확인한 뒤에만 생성한다. 기존 staging public schema를 사용하지 않는다.
- PostgreSQL + pgvector, migration 0001~0037 및 checksum 일치 확인.
- vector extension 객체가 검증 schema 경로에서 해석되는지 확인한다. 이미 public에 설치된 extension을
  무단 이동하거나 public을 search_path에 추가해 격리 검사를 우회하지 않는다.
- migration 관리 역할과 worker 역할을 분리한다. worker가 운영 DB/다른 schema에 접근하지 못해야 한다.
- 모든 대여 연결은 current_database/current_schema/current_schemas가 bootstrap 계약과 일치해야 한다.
- 외부 DSN은 sslmode=verify-full. 신뢰 CA를 환경에 준비한다. sslmode=require로 낮추지 않는다.
- worker 이미지에는 migration SQL이 포함되지 않으므로 관리자용 별도 migration 절차가 필요하다.

사전 읽기 확인 항목: 적용 migration 버전/해시, 정확한 schema, 대상 사용자/paper/version/upload/job 연결,
queued·attempt=0·full_processing, tombstone 없음, 사용량 reserved/unknown/초과 없음.
실패한 작업의 attempt나 usage 원장을 초기화해 재실행하지 않는다.

## 4. R2와 제품 동의

Cloudflare 계정에는 `solo-leveling-staging-pdfs` bucket 하나(4개 객체, 3.51 MB)만 표시된다.
worker 검증 전용 bucket은 없으며, 확인 시점 R2 청구 표시는 $0.00이었다.
기존 객체 조회·다운로드·변경이나 신규 bucket·자격 생성은 하지 않았다.

- 검증 전용 비공개 bucket 제안: slp-worker-verification. 공개 접근을 켜지 않는다.
- worker에는 해당 bucket의 읽기 자격만 제공한다. 준비/업로드/정리 자격은 별도로 제한한다.
- endpoint는 검증 account의 https://ACCOUNT_ID.r2.cloudflarestorage.com, region=auto.
- 실제 객체 업로드 전 합성 PDF 내용·보관 기간·삭제 담당/시점을 승인한다. 이 문서만으로 업로드하지 않는다.
- object key/size/SHA-256/content-type은 기존 upload intent→prepare→PUT→complete 경로로 연결한다.
  임의 URL이나 손으로 만든 job만으로 정상 접수/동의를 대체하지 않는다.
- storage와 ai_provider 동의는 각각 제품 고지와 확인을 받아 기록한다. 동의 철회·notice version·
  입력 hash와 upload binding을 검사한다. DB에 임의 INSERT한 fixture를 실제 사용자 동의로 간주하지 않는다.
- verification payload_hash는 PDF hash가 아니라 실제 접수 job의 payload hash다.

## 5. 예산·전송 차단

초기 준비 중에는 Render standby, SLP_VERIFY_ALLOW_EXTERNAL_API=false, SLP_VERIFY_LOCAL_ONLY=true를 유지한다.
검증 DB의 provider_dispatch control도 승인 전 비활성 상태여야 한다. runtime secret/API 키는 아직 필요하지 않다.

실행 승인에 필요한 값:
- 번역 모델·고지 버전·합성 PDF·선택 원문과 provider 목적지
- 최대 요청 수, 입력/출력 토큰 합계, 호출별 최대 출력, 금액 상한, 검증 window와 종료 시각
- 정확한 API payload digest 목록과 모델별 tokenizer/overhead를 검토한 입력 토큰 상한

영속 원장에는 같은 policy_version/window_key의 두 행이 필요하다:
- scope_type=service, scope_id=service
- scope_type=owner, scope_id=실제 검증 owner

두 행 모두 검사하고 공유 사용량/예약을 보존한다. 기존 행을 덮어써 소비량을 0으로 만들지 않는다.
requests 한도는 이 원장을 대체하지 않으며 토큰 상한만으로 달러 비용을 자동 보장하지 않는다.
모델 가격을 확인해 금액 상한과 토큰 상한을 함께 검토한다. reserved/unknown은 임의 환급하지 않는다.

## 6. 설정 파일·secret 연결

verification.json: 승인한 DB/schema/owner/job/paper/version/job payload_hash/worker/lease/staging 한도.
runtime.json: 위 scope_digest, 외부 runtime digest, 이미지 내 runtime/model lock 경로, 실제 embedding_spec,
PDF/pages/chunks/batch 한도, 번역 모델/timeout/output, policy/window, R2, 승인 payload별 요청 한도.
경로는 /opt/worker/runtime-lock.json, /opt/worker/model-lock.json 및 /opt/models/고정revision이다.

전용 secret 목록: SLP_VERIFY_DATABASE_URL, SLP_VERIFY_R2_ACCESS_KEY_ID,
SLP_VERIFY_R2_SECRET_ACCESS_KEY, SLP_VERIFY_OPENAI_API_KEY, SLP_VERIFY_RUNTIME_CONFIG.
WORKER_BASE_REFERENCE는 이미지 값과 일치해야 하며 임의로 덮어쓰지 않는다.
기본 JSON 검사 → 파일/metadata 검사 → 승인 후 실제 자원 연결 순서를 지킨다.
빈 owner/한도나 가짜 digest를 채운 설정을 배포 가능한 완성본으로 표시하지 않는다.

## 승인과 결제 시점

1. 승인 완료: GHCR private 두 이미지 게시, 일회성 Actions 인증. 실제 게시 완료 여부는 실행 결과로 판단한다.
2. 다음: 검증 DB·R2 대상(기존 격리 환경/신규), 생성·migration·접수/동의 기록의 변경 승인.
3. 실제 PDF 전송·AI 호출은 별도 구체적 범위/비용 승인.
4. Render Background Worker 생성/Deploy 직전에 사양·표시 요금·대기 중 과금·중지 계획을 안내한다.
   DB 등 다른 유료 자원도 생성 전에 안내한다. 현재 외부 자원 생성/결제는 수행하지 않았다.

## 근거

- GitHub registry/권한: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- GHCR 요금: https://docs.github.com/en/billing/concepts/product-billing/github-packages
- 저장소 계약: verification_bootstrap.py, verification.py, processing_authorization.py, dispatch_ledger.py,
  remote_upload_consents.py, migrations/server/0033·0035·0037.
- #517은 게시·DB/R2/동의·예산 준비만으로 종료하지 않는다.


## 2026-11-17 첫 게시 시 발견한 제한

실행 44014140496에서 runtime push는 성공했으나, 직후 검사에서 public으로 확인돼 중단됐다.
GitHub UI도 Public을 표시했다. 즉시 Private으로 복구했고 worker는 게시하지 않았다.
runtime에는 공개 라이브러리와 사전 검사 코드만 포함되며 사용자 PDF·DB 자격·API 키는 없다.
공개 저장소 source label과 권한 상속이 설정돼 있었지만, 공개 생성 원인은 추가 확인이 필요하다.
신규 package는 기본 private이라는 가정으로 재시도하지 않는다.

후속 보완은 **존재하는 private package만 허용**한다. 두 package 중 하나라도 미존재·공개·조회 실패이면
다운로드·빌드·push 전에 중단한다. worker package를 비공개로 사전 준비하는 절차가 확정되기 전
게시를 완료했다고 간주하거나 실행을 재개하지 않는다. 기존 runtime은 삭제하지 않았다.
