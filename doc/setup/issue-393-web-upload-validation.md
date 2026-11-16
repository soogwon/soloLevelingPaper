# #393 제품 웹 PDF 전송 연결과 대역 검증

기준 main: `1a3b154` (#522 서버 보관 동의 gate 포함). 브랜치: `feat/issue-393-web-pdf-upload`.

## 제품 흐름

1. 파일 선택 시 PDF 헤더·크기·SHA-256을 확인하고 해당 바이트의 Blob을 화면 메모리에 보관한다.
2. 파일 정보 전송 확인 → 등록 준비 생성 → 별도 보관 고지 조회·명시 승인.
3. 사용자가 PDF 업로드를 누르면 현재 보관 동의 ID·revision·고지 버전을 서버에서 다시 확인한다.
4. 같은 intent의 prepare → 동의 재확인 → 서명 URL로 PUT → 동의 재확인 → complete 순서로 실행한다.
5. complete의 intent/paper/version/파일 정보가 일치하고 verified인 경우에만 완료 표시한다. 이후 동의 재조회 시 AI 승인 확인란이 열린다. 처리 접수와 AI 호출은 연결하지 않는다.

서명 URL PUT은 웹 세션 API 어댑터와 분리한다. HTTPS·PUT·서버 헤더 형식을 검사하며 쿠키, CSRF, Referer를 보내지 않는다. 리다이렉트는 거부한다. 서버가 반환한 Content-Length는 Blob 크기와 대조한 뒤 브라우저가 생성하도록 생략한다. 서명 URL·저장소 오류 본문은 UI/로그에 출력하지 않는다.

취소·재선택·로그아웃·화면 이동은 진행 중 PUT의 AbortSignal을 중단하고 이전 실행의 후속 단계와 응답을 무시한다. 이미 전송된 바이트나 서버 작업을 취소/삭제했다고 표시하지 않는다. PUT 시도 이후 응답 유실은 같은 intent의 complete만 재확인하며 자동 PUT 재전송은 하지 않는다. 객체가 없거나 잘못된 경우 새 선택이 필요할 수 있다. 새로고침 후 복구는 기존과 같이 미지원이다.

## 대역 회귀

`node --test web/tests/*.test.mjs`: **83 passed, 실패·skip 0**.

- 승인 전 무전송, 목적별 동의 검사, prepare/PUT/complete 순서
- 각 동의 재확인 지점에서 철회 발견 시 다음 단계 차단
- PUT 및 complete 응답 유실 후 재전송 없는 복구
- 동의 조회/prepare/PUT/complete 각각 도중 취소·재선택·로그아웃의 늦은 응답 차단
- 다른 대상·만료 prepare, 다른 version 완료 거부, 중복 클릭 차단
- 저장소 자격 비전송·리다이렉트 차단·헤더/크기 검사·오류 URL 비노출
- 제품 버튼부터 실제 API 어댑터까지 fetch 대역으로 연결하여 완료 후에만 AI 동의를 열고 processing/AI 승인 자동 요청이 없는지 검증
- prepare/complete의 웹 세션·CSRF·같은 의도 경로 확인

실제 네트워크 PDF 전송, 실제 저장소, 실제 브라우저 업로드, AI 호출을 실행하지 않았다. 합성 8바이트 PDF 헤더를 메모리 fetch 대역에서만 읽었다. 기존 #516 실제 Chrome 동의 검증과 이번 자동 업로드 검증은 서로 다른 범위다.

## 배포 전 확인과 남은 범위

- PR #522에서 prepare의 URL 발급 전과 complete의 최종 게시 트랜잭션에 현재 보관 동의 검사가 연결됐다. 미동의·철회·이전 고지는 `remote_upload_storage_consent_required`(HTTP 409)로 거부하며, 웹은 이를 일반 충돌이 아닌 재동의 안내로 처리한다.
- 실제 R2 CORS의 PUT·Content-Type 허용, 서명된 Content-Length와 브라우저 요청의 호환성, URL 만료/네트워크 중단/동시 철회 검증.
- 두 계정, 만료 객체 정리 운영 설정, 로그 비노출 감사, 플랫폼·브라우저 확인.
- 처리 접수·예산·AI 동의·기존 job 조회 연결은 후속 범위다. PDF 업로드 성공은 학습 완료를 뜻하지 않는다.

#393은 열린 상태로 유지한다. 실제 배포·R2 업로드 검증 완료를 의미하지 않는다.

## 다음 단계와 담당자 확인

1. C: 실제 브라우저/R2에서 합성 PDF 한 건의 CORS PUT, 브라우저 Content-Length 처리, complete와 AI 동의 저장·철회를 검증한다. AI 실행은 별도 승인 전까지 하지 않는다.
2. A/C: prepare 뒤 보관 동의를 철회한 다음 잔여 유효시간의 URL로 PUT해도 complete가 409로 거부되고, 미게시 최종 객체가 남지 않는지 스테이징에서 확인한다.
3. A/C: 두 계정 격리, 만료 임시 객체 정리, presigned URL 로그 비노출을 운영 환경에서 확인한다.

이번에는 외부 전송 없이 주소 만료(조회 중 만료 포함), 원본 버퍼 변경, prepare 응답 유실 복구 회귀까지 추가했다. 서버 계약과 운영 설정은 임의 변경하지 않았다.

## staging 보관 동의 철회 경합 검증 모듈

일반 업로드 화면은 prepare부터 complete까지 실행 중 동의 버튼을 비활성화하므로 타이밍
클릭으로 경합을 재현하지 않는다. `web/js/validation/remote-upload-consent-race.js`는 로그인된
staging 페이지의 기존 API 객체와 사용자가 선택한 공개·합성 PDF를 주입받아 아래 순서를
명시적으로 분리한다.

1. intent와 현재 보관 동의를 조회한다.
2. prepare 뒤 동의 ID·revision이 그대로인지 다시 확인한다.
3. 호출자가 안전한 메타데이터만 받은 `confirmPrepared`에서 명시 승인한다.
4. 보관 동의를 철회한 뒤 이미 발급된 URL로 PUT한다.
5. complete가 `409 remote_upload_storage_consent_required`인지 확인한다.
6. 철회 상태와 intent 상태를 다시 읽고 URL·쿠키·CSRF·owner/object key가 없는 판정만 반환한다.

모듈은 공개 화면과 라우터에 연결하지 않으며 지정된 staging origin 이외에서는 실행을 거부한다.
실제 실행은 로그인된 staging 업로드 화면의
개발자 도구에서 기존 `api` 모듈과 현재 파일 입력을 직접 주입한다. 코드나 문서에 세션 쿠키,
CSRF, presigned URL을 복사하지 않는다. 실행 전에는 새 합성 intent와 현재 보관 동의가 필요하다.

```js
const [{ api }, { validateRemoteUploadConsentRace }] = await Promise.all([
  import('/js/api/index.js'),
  import('/js/validation/remote-upload-consent-race.js'),
]);
const intentId = document.querySelector('[data-intent]').textContent.match(
  /[0-9a-f]{8}-[0-9a-f-]{27,}/i,
)[0];
const file = document.querySelector('[data-file]').files[0];
await validateRemoteUploadConsentRace({
  api,
  file,
  intentId,
  confirmPrepared: safe => Promise.resolve(window.confirm(
    `보관 동의를 철회한 뒤 ${safe.file_name} PUT과 complete 409 검증을 진행할까요?`,
  )),
});
```

실행 결과의 `final_object_published=false`는 complete 거부로 확정 게시가 성립하지 않았다는
판정이다. R2 객체 부재를 직접 읽은 결과가 아니므로, 서버 로그에서 안전한 정리 결과를 확인하거나
R2에서 해당 intent와 DB 참조를 대조하기 전까지 물리 객체 삭제 완료로 확대 해석하지 않는다.
