# 원격 MCP 스테이징 실측 — #388 / #409

기록일: 2026-11-16, 시각은 Asia/Seoul 기준.
이 문서는 운영 공개 승인이나 제품 도구·PDF·AI 처리 검증이 아니다.

## 환경과 증거

- 실측 배포: `54f1abc15fba8b840cfd715e1a7f6a1101e6192b`
- macOS 26.7.1, Claude Desktop 5.11492.5, Pro, 일반 대화
- 공개 MCP: `https://solo-leveling-staging.soogyung.com/mcp`
- resource와 전용 audience는 위 URL, 최소 scope는 `spike:read`
- Auth0의 전용 직접 등록 client를 사용했다. client ID·토큰·계정 식별자는 기록하지 않는다.
- [공개 경계·로그·롤백 결과](https://github.com/soogwon/soloLevelingPaper/issues/409#issuecomment-6896565330)
- [자동 갱신·복원 결과](https://github.com/soogwon/soloLevelingPaper/issues/388#issuecomment-6896987891)
- [독립 PKCE 재사용 거부 결과](https://github.com/soogwon/soloLevelingPaper/issues/388#issuecomment-6897123163)

이후 main 변경을 포함한 재검증 결과로 확대하지 않는다. 앞선 initialize/tools/list 연결
증거는 #388/#409의 2026-11-15 코멘트에서 구분해 확인한다.

## 실제 통과 범위

| 항목 | 결과와 한계 |
|---|---|
| 공개 HTTPS 연결 | Claude OAuth 연결 및 whoami 성공. 정상 Host는 421 없이 처리 |
| metadata | resource URL과 등록 MCP URL 일치, 무토큰 요청 401 |
| 공개 경계 | 위조 forwarded Host 421, 비허용 Origin 403. Cloudflare의 선행 거부와 앱 거부를 구분 |
| 로그 | 관측한 Render 앱 로그에 토큰·사용자 식별자·본문 없음. 전체 이력·모든 내부 hop을 감사한 것은 아님 |
| 플래그 중단 | false 배포 후 GET MCP/metadata 404, Claude POST 도구 호출 405. 기존 로그인 웹 정상 |
| 복원 | true 배포 후 metadata 200, 무토큰 MCP 401, 기존 Claude whoami 성공 |
| 카드 | 일반 대화에 카드 본문이 나타나지 않음. 합성 응답의 한국어 텍스트를 읽을 수 있음 |
| 지연 | probe_delay(5) 완료. 300초 한계나 실제 작업 성능을 검증하지 않음 |
| 합성 거부 | 무효 문자열 bearer 401, 합성 session ID 포함 요청 400. 실발급 토큰·두 계정 격리와 다름 |

## 자동 갱신과 회전

원래 전용 API는 Offline Access=false, access token 수명 86400초였다.
Claude client는 Refresh Token grant가 켜져 있으나 회전은 꺼져 있었다.
따라서 원래 설정에서 단순 재호출 성공을 자동 갱신 성공으로 판정하지 않았다.

승인된 임시 설정에서 API Offline Access 및 client 회전을 켜고 access token 수명을
300초로 줄였다. implicit/hybrid 수명도 최대 수명 이하로 맞춰 저장했다.
최초 저장 미반영 상태의 호출은 만료 검증에서 제외하고, 새로고침으로 저장값을 확인한
뒤 다시 인증했다. 동의 화면의 offline access와 전용 refresh 자격 발급을 확인했다.

- 00:20:09: 새 authorization code 교환
- 00:30:09.380: 재로그인 없이 whoami 성공, 전용 client의 Auth0
  `Successful Refresh Token exchange`와 대조
- 원래 API 수명 86400/7200초, Offline Access=false, client 회전 및
  idle/maximum refresh lifetime 옵션 비활성으로 복원하고 재조회
- 테스트 refresh 자격을 회수해 Devices 목록이 비었음을 확인
- 원래 최소 권한으로 Claude 재연결, whoami 성공 및 refresh 자격 미잔존 확인

별도 승인된 Native PKCE 임시 client에서 S256·state·정확한 루프백 callback을 사용했다.
코드/refresh grant만 켜고 회전 overlap 0초, 최대 refresh 수명 3600초로 제한했다.
토큰은 로컬 프로세스 메모리에만 보관하고 아래 상태/판정만 출력했다.

| 순서 | 실제 결과 |
|---|---|
| 코드 교환 | 200, refresh 발급 |
| 최초 갱신 | 200, refresh 값 변경 |
| 이전 refresh 재사용 | 403 invalid_grant |
| 재사용 탐지 후 새 refresh 사용 | 403 invalid_grant |

이는 Auth0의 회전·family 차단 실측이다. Claude가 이전 토큰을 재사용하도록 강제한
시험이나 제품 MCP의 실발급 잘못된 토큰 거부 시험은 아니다. 임시 client는 Render
allowlist에 추가하지 않았다.

회수 API의 빈 성공 응답을 JSON으로 해석하는 검증기 정리 오류가 발생했다. 네 단계
결과와 회수 성공을 구분하며, 수정 후 구문 확인만 했고 전체 인증을 재실행하지 않았다.
최종 정리는 관리 화면에서 승인받은 임시 client 삭제와 새로고침 후 목록 부재로
확인했다. API Offline Access=false 및 기존 수명도 다시 확인했다.

## 지원 대상 표: 검증 범위이며 제품 지원 확정이 아님

| 호스트·요금제 | 상태 | 남은 것 |
|---|---|---|
| macOS Desktop 5.11492.5 / Pro | 위 연결·갱신·텍스트 대체 실측 | 카드 렌더링 보장, 두 계정, 모든 실발급 거부 조건 |
| Claude 웹 / Pro | 브라우저 OAuth 화면 사용만 확인 | 웹 대화의 독립 도구·카드·갱신 검증 |
| Windows Desktop | 이번 실측에서 제외: 해당 환경을 사용하지 않음 | 별도 담당자의 버전·결과 |
| 모바일·Claude Code | 이번 실측에서 제외: 해당 클라이언트를 사용하지 않음 | 지원 대상으로 채택할 때 독립 검증 |
| Free·Max·Team·Enterprise | 이번 실측에서 제외: 해당 계정/요금제를 사용하지 않음 | 지원 대상으로 채택할 때 독립 검증 |

## 남은 순서와 필요한 입력

계정·발급 환경과 사례별 정상 대조군은
[A 협의용 준비사항](remote-mcp-auth0-test-preparation.md)에 정리했다.
기록일 기준으로 사용자가 테스트 계정 2개와 별도 발급 환경이 아직 준비되지 않았다고 확인했다.

1. 실발급 만료·잘못된 audience/scope/client·ID token·웹/기기 토큰의 제품 거부.
   각 거부 원인을 구분할 기준 정상 요청이 필요하다. 허용하지 않은 client 토큰의 401을
   audience 또는 scope 검증 증거로 재사용하지 않는다. 다른 issuer 시험은 별도 발급자 필요.
2. 두 승인된 테스트 계정과 허용되지 않은 도구 호출. 현재 모든 session ID를 거부하는
   stateless 계약을 제품 상태 세션의 사용자 격리 완료로 보지 않는다.
3. published identity의 Auth0 호환성 실측 및 직접 등록/DCR 대안 선택 근거.
   관리 화면에 metadata URL import 선택지가 보였다는 사실만으로 호환을 판정하지 않는다.
4. A·C와 최소 지원 조합을 확정하고 미검증 조합의 제외·재검토 조건을 합의한다.
5. #388 완료 조건을 다시 대조한 뒤 종료 여부를 판단한다. #77은 별도 판단한다.

후속 시험은 A가 발급 경계·테스트 계정을 확인하고 C가 호출·화면·결과를 검증하는 분담을 제안한다.
계정 비밀번호나 토큰을 티켓·채팅으로 전달하지 않는다. 설정 담당자가 임시 권한을
회수하고 C가 원복값·자격 잔존 여부를 재조회하는 방식으로 정리한다.
