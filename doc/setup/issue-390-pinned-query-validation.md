# 고정 원격 질문 모델과 context 연결 검증

2026-11-17 기준. 원격 MCP 제품 도구 공개나 #390 전체 완료를 의미하지 않는다.

## 계약

- `PinnedQueryEmbeddingRuntime`은 서버가 지정한 `EmbeddingSpec`과 게시된 지문 전체가 같을 때만 질문을 임베딩한다.
- 기존 PyTorch 처리 adapter의 artifact, CPU/float32, pooling, normalization, 실제 package/module 버전 검사를 재사용한다. 기본 모델이나 다른 런타임으로 대체하지 않는다.
- `PostgresRemoteContextResolver.pin`은 owner·paper·version의 현재 publication을 호출자가 보관한 context UUID에 한 번만 연결한다. 재조회·재게시 이후에도 같은 키는 이전 publication을 유지한다. 다른 논문/판본 재사용은 충돌이다.
- 인증된 owner 전달은 호출 계층 책임이다. 모델 입력 owner를 신뢰하거나 이 adapter만으로 인증을 완료했다고 간주하지 않는다.

## 실행 결과

- 질문 모델/기존 처리 모델 회귀: 92 passed.
- 검색 composition·context·출처 및 PostgreSQL 관련 회귀: 34 passed. 위 집계와 질문 모델 테스트 1개가 중복된다.
- 기존 Linux worker 이미지의 고정 모델을 로컬 Docker 2GB/1CPU에서 로딩하여 합성 한국어 질문과 합성 한국어 청크를 각각 임베딩했다. 384차원·동일 지문 검사를 통과했다. 유료 API 0회, 모델 다운로드 없음.
- 해당 실제 모델 벡터를 로컬 일회용 PostgreSQL에 저장하고 원자 게시한 후 context 고정, 페이지 1 top1 일치, strict 페이지 2 빈 결과, 타 owner 조회 거부, 동일 context 재생을 확인했다(추가 실측 1 passed). 검색 fixture의 원문/번역은 합성이며 실제 논문 품질/회수율 검증은 아니다.

## staging 제한

기존 합성 게시 결과에 context를 연결하는 시도는 제한 웹 DB 역할의 권한 부족으로 중단됐다. API 호출은 없었다. 권한을 확장하거나 관리자 계정으로 대체하지 않았다. 원격 MCP용 context 생성·검색·출처 조회의 최소 역할 권한을 정리하고 로컬 제한 역할 검증 후 staging 적용 승인이 필요하다.

staging에서 원격 MCP→고정 질문 모델→실제 게시 벡터→근거 조회가 모두 성공했다고 보고하지 않는다. 실제 모델 품질·ANN 성능·다중 계정 호스트 검증도 남아 있다.
