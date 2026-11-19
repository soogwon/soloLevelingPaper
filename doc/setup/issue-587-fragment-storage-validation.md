# #587 하위 조각 저장·검색 검증

## 계약

- A: migration 0049는 기존 `server_embedding_vectors`와 과거 set을 변경하지 않고 `server_embedding_fragments`를 추가한다. owner·판본·parse·번역·embedding set·현재 lease에 귀속하며 ready 이후에는 수정·삭제를 거부한다. 논문 삭제의 연쇄 정리는 허용한다.
- B: `split_policy`가 명시된 새 set만 하위 조각을 검색한다. 부모별 최소 cosine distance로 묶은 뒤 정렬·LIMIT하므로 같은 부모의 조각이 결과 자리를 중복 점유하지 않는다. 페이지·섹션·소유자·고정 publication 필터는 집계 전에 적용한다.
- C: 반환 근거 ID는 원래 부모 chunk ID다. 질문 모델의 기본 지문을 그대로 검사하면서 분할 정책까지 결과 지문에 보존한다.

## 완전성 검사

새 set을 ready로 변경할 때 부모 수, 시작·끝 구간, stride, payload, 일관된 부모 토큰 수, 차원을 검사한다. 누락 구간, 중복 꼬리, 기존 단일 벡터와의 혼합을 거부한다. 기존 active pointer는 실패한 새 set으로 바뀌지 않는다.

## 검증

로컬 격리 PostgreSQL과 대역 질문 모델에서 관련 테스트 50개가 통과했다. 부모 집계·페이지 필터·다른 소유자 차단·동일 요청 재생·충돌·만료 lease·ready 이후 변경 차단·과거 context·논문 삭제를 확인했다. 실제 모델 다운로드·외부 API·staging 변경은 하지 않았다.

## 남은 범위와 적용 전 승인

아직 worker의 토큰 하위 조각 인코딩·staging payload·원자 게시 조립은 연결하지 않았다. 기존 제품 worker는 #632의 길이 검사 경로를 유지한다. 이 PR만으로 긴 논문을 처리하거나 #587을 종료할 수 없다.

실제 staging 적용에는 migration 0049와 fragment 쓰기/조회 역할의 최소 권한 변경 승인이 필요하다. 코드 병합은 해당 적용을 수행하지 않는다. 로컬 재색인과 #391 품질·성능 실측도 남아 있다.
