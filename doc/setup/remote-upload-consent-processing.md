# 원격 PDF 동의와 처리 접수 계약

#393의 인증 웹 흐름은 업로드 의도, PDF 보관 동의, 외부 AI 전송 동의, 처리 접수를 서로
다른 서버 상태로 유지한다. OAuth 로그인, 링크 GET, 파일 정보 확인란, presigned PUT 성공,
업로드 검증 성공만으로 동의나 유료 작업을 만들지 않는다.

## 고지와 동의

`GET /v1/remote-uploads/intents/{intent_id}/consents`는 현재 서버 고지 버전과 보관·AI 고지,
각 동의의 최신 상태를 반환한다. 조회는 동의·요청 키·작업을 만들지 않는다. 응답에는
`Cache-Control: no-store`가 있으며 owner, input hash, object key는 포함하지 않는다.

- `storage`: PDF의 서버 보관과 처리 준비 승인
- `ai_provider`: OpenAI에 추출 원문·필요 구간을 처리 목적으로 전송하고 추가 비용이 생길 수
  있다는 승인. PDF 파일 자체의 제공자 전송 승인은 아니다.

현재 고지 버전은 `remote-pdf-processing-v1`이다. 사용자 삭제 안내는 확정된 정책을 따른다.

> 삭제 요청 즉시 해당 자료에 대한 접근과 처리가 중단됩니다. 활성 저장소와 백업을 포함한
> 완전 삭제는 최대 30일이 걸릴 수 있습니다. 삭제된 데이터가 복원이나 지연된 작업으로 다시
> 생성되지 않도록 최소한의 삭제 표식은 추가로 최대 30일간 보관됩니다.

내부 운영 기준은 `tombstone_retention_days=60`, `backup_expiry_days=30`,
`account_erasure_days=30`이다. 이 PR은 삭제 실행기를 변경하지 않고 고지 계약만 연결한다.

명시적 승인은 다음 경로로만 기록한다.

```http
POST /v1/remote-uploads/intents/{intent_id}/consents/{storage|ai_provider}
Idempotency-Key: <화면 동작에 고정한 키>
Content-Type: application/json

{"notice_version": "remote-pdf-processing-v1"}
```

서버는 인증 owner와 업로드 의도의 canonical payload hash, 목적, 현재 고지 버전을 직접 결합한다.
클라이언트는 조회하여 사용자에게 표시한 `notice_version`을 제출한다. 서버 현재 버전과 다르면
`409 remote_upload_consent_notice_changed`로 거부하고 새 고지 조회·재동의를 요구한다.
input hash, owner, `confirmed`는 받지 않는다. 동일 키·동일 입력은
기존 동의를 재생하고 같은 owner/purpose 키가 다른 intent에 쓰이면 `409`로 거부한다. 이미 현재
동의가 있을 때 새 키로 재확인해도 그 키를 별도 요청 원장에 결합해 이후 혼입을 막는다.
저장된 동의의 고지 버전이 현재 서버 버전과 다르면 조회 상태는 `stale`이며 새 처리 접수에
사용할 수 없다. 사용자가 새 고지를 보고 다시 승인하면 이전 동의를 철회하고 새 revision을 만든다.

보관 동의는 유효한 업로드 의도에서 받을 수 있다. AI 전송 동의는 실제 객체 검증이 끝난
`verified` 의도에서만 받을 수 있다. 만료·삭제·타 owner 의도는 미존재 대상과 같은 `404`다.

철회는 현재 revision을 명시해야 한다.

```http
POST /v1/remote-uploads/intents/{intent_id}/consents/{purpose}/revoke
{"expected_revision": 1}
```

같은 revision 재전송은 기존 철회 결과를 반환한다. 오래된 revision은 충돌이며, 재동의는 새
revision과 consent ID를 만든다. 과거 감사 행은 지우지 않는다.

## 처리 접수와 응답 유실 복구

검증된 업로드와 두 현재 동의를 다음 요청에서 기존 원자 접수 원장에 연결한다.

```http
POST /v1/remote-uploads/intents/{intent_id}/processing
Idempotency-Key: <처리 접수 키>

{
  "storage_consent_id": "uuid",
  "ai_consent_id": "uuid"
}
```

consent ID 자체는 권한이 아니다. 서버는 owner, intent, 목적, payload hash, 고지, 철회 상태,
업로드 `verified/ready`, 삭제 표식, 운영 intake 제어를 같은 트랜잭션에서 다시 검사한 뒤 하나의
`full_processing` job과 authorization을 만든다. 같은 키·같은 승인 또는 같은 intent의 중복
접수는 기존 job을 반환한다. 다른 승인 혼입은 충돌하고, 준비되지 않은 상태는
`remote_processing_not_ready`로 거부한다.

응답 유실 뒤에는 새 키로 자동 접수하지 않고 아래 읽기 전용 경로로 같은 intent의 기존 job을
조회한다.

```http
GET /v1/remote-uploads/intents/{intent_id}/processing
```

모든 경로는 웹 세션 전용이다. device 자격은 `403 web_session_required`, 인증 누락은 `401`이다.
DB 미설정/장애는 안전한 `503`으로 닫히며 내부 owner, hash, SQL, object key를 노출하지 않는다.

## 범위와 후속 검증

이 계약은 처리 job 접수까지만 연다. 실제 provider 전송은 #396의 전송 직전 동의·고지·예산
gate와 운영 dispatch 제어를 계속 통과해야 한다. 저장된 결과 조회와 새 유료 전송 허용은 별개다.

자동 검증은 HTTP 자격·오류·응답 유실 복구, 요청 키 혼입, 철회/revision, PostgreSQL 동시 승인과
기존 #215 업로드 회귀를 포함한다. 실제 R2와 두 테스트 계정의 종단 검증, 웹 버튼 연결,
만료 정리 스케줄·presigned URL 로그 비노출은 #393의 후속 범위다.

삭제 표식이 있는 논문은 기존 처리 job 재조회와 동일 키 재생에서도 제외한다.
