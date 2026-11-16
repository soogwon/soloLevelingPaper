# #393 원격 PDF 등록 대역 미리보기

최신 main의 `RemoteUploadIntent`/migration 0021 상태를 참고한 **별도 화면 초안**이다.
제품 웹 라우트·MCP 도구에 연결하지 않는다. 실제 보관/AI 동의·presigned PUT·완료 검증·큐 접수는 없다.

```sh
node --test ui/remote-upload-preview/upload.test.mjs
python3 -m http.server 8765 --bind 127.0.0.1 --directory ui/remote-upload-preview
```

브라우저에서 `http://127.0.0.1:8765`를 열어 파일을 선택하고 대역 상태를 바꿔 볼 수 있다.
서버는 이 미리보기 디렉터리만 제공한다. PDF 바이트 읽기/전송 코드는 없다.
파일명/크기만 브라우저 메모리에 표시하며 localStorage에도 저장하지 않는다.
테스트는 Node 모델/HTML 문자열 검증이며 실제 브라우저 클릭·레이아웃 검증은 아니다.

- 선택/변경/취소마다 세대를 바꾸고 이전 intent·상태를 지운다. 이전 선택의 늦은 응답을 무시한다.
- 서버 응답은 대역이다. 향후 인증 adapter가 owner·요청 키·파일 hash·대상을 검증한 조회만 연결해야 한다.
- Store에는 생성·업로드 예약·검증 완료 저장·완료 조회가 구현되어 있다. 이 미리보기에 연결된 인증 웹 상태 조회 adapter는 아직 없다. 선택 세대 검사는 서버 소유권 검사를 대신하지 않는다.
- 파일명·MIME·최대 50 MiB 검사는 화면 사전 안내다. 실제 PDF 형식·크기·SHA 검증의 대체물이 아니다.
- created/uploading/verified/failed/expired를 처리 접수/완료와 구분한다. verified라도 AI 동의·예약·job을 만들지 않는다.
- 동일 세대의 응답은 실제 adapter에서 직렬 조회해야 한다. 서버 revision 기반 역전 응답 방지는 계약 확정 후 연결한다.
- 선택은 업로드 의도/보관 동의가 아니다. 검토 버튼으로 상태를 바꾸어도 어떤 API도 호출하지 않는다.

A의 상태 조회·완료 검증·#396 원자 접수 계약 뒤 실제 인증 웹으로 연결한다. 운영 정책/외부 wire 코드를
이 대역으로 확정하지 않으며 #393은 열어둔다.

실제 브라우저 확인 절차와 범위는 [브라우저 검증 기록](../../doc/setup/issue-393-browser-preview-validation.md)을 참고한다.

## 처리 접수/복구 표시 대역

verified 상태 뒤 `기존 작업 조회 대역 표시`로 queued/running/succeeded/failed/cancelled 및 응답 유실을
검토할 수 있다. 상태 DTO는 내부 봉투 `{intentId, job: ProcessingAdmission}`이며 확정된 HTTP wire 계약이 아니다.
job 필드는 A의 `ProcessingAdmission`과 맞추되 업로드 검증과 job 성공을 별개로 표시한다.
버튼은 합성 결과 표시만 수행하고 파일 읽기·동의 발급·처리 접수·네트워크 요청을 하지 않는다.

조회마다 새 표식을 만들고 최신 표식의 응답만 반영한다. 중복 응답/역전 응답, 다른 intent/job/version,
파일 변경/취소 뒤 이전 응답을 거부한다. 응답 유실은 같은 작업 재조회로 복구하며 새 접수 키를 생성하지 않는다.
표식은 권한이나 멱등성 키가 아니다. 인증 API·동의/예약 결합과 실제 브라우저→서버 검증은 남아 있다.

## 파일 선택창 취소

선택창의 취소 이벤트와 화면의 선택 취소 버튼은 파일 입력·선택 세대·이전 작업 표시를
함께 초기화한다. 같은 파일을 그대로 다시 선택해도 브라우저가 취소 이벤트를 발생시킬
수 있으며, 이 경우 파일을 다시 선택해야 한다. 이 초기화는 서버 작업 취소가 아니다.
실제 HTML 이벤트 연결을 실행하는 자동 회귀와 macOS Chrome 관찰 결과는
[브라우저 검증 기록](../../doc/setup/issue-393-browser-preview-validation.md)에 구분해 기록했다.
