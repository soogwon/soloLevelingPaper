# 기존 원격 논문의 제목·저자·출판 연도 채우기

PDF 처리 worker는 새로 처리하는 논문의 첫 페이지에서 제목·저자·출판 연도를 찾아
`server_papers`의 **비어 있는 열에만** 채웁니다. 이 기능 배포 전에 처리된 논문은
아래 관리자 명령으로 한 번 채웁니다. 번역·임베딩은 다시 하지 않고, 저장된 PDF를
내려받아 첫 페이지만 읽습니다. 이미 값이 있는 열(사용자·로컬 기기가 넣은 제목 등)은
덮어쓰지 않습니다.

## 실행

저장소 checkout의 가상환경에서 실행합니다. 환경 변수는 셸에만 두고 파일로 남기지 않습니다.

| 변수 | 값 |
|---|---|
| `DATABASE_URL` | `server_papers`를 UPDATE할 수 있는 관리자 URL. staging은 끝에 `?options=-csearch_path%3Dslp_staging` |
| `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_BUCKET` | 웹 서비스와 같은 값 |
| `OBJECT_STORAGE_ACCESS_KEY_ID`, `OBJECT_STORAGE_SECRET_ACCESS_KEY` | PDF 읽기 권한이 있는 자격 정보 |
| `OBJECT_STORAGE_REGION` | R2는 `auto`(기본값) |

```bash
# 1) 미리보기: 무엇이 채워질지 논문별 JSON 한 줄씩 출력하고 DB는 바꾸지 않습니다.
solo-leveling-paper-front-matter-backfill

# 2) 결과가 맞으면 저장합니다.
solo-leveling-paper-front-matter-backfill --apply
```

각 줄의 `status`는 `would_update`(미리보기에서 채울 예정), `updated`, `unchanged`
(채울 빈 열 없음), `not_found`(첫 페이지에서 못 찾음), `failed`(PDF 읽기 실패)입니다.
마지막 줄은 전체 건수입니다. 실패가 있으면 종료 코드 1입니다. 기본 한도는 200건이며
`--limit`으로 1~1000을 지정합니다.

채운 값이 틀렸다면 해당 논문의 열을 직접 고칩니다(명령을 다시 실행해도 값이 있는 열은
바꾸지 않습니다).

## worker 권한

worker가 새 논문에 값을 저장하려면 worker DB 역할에 다음 권한이 필요합니다.
권한이 없으면 처리·게시는 정상으로 끝나고 로그에 `processing_front_matter_not_applied`만
남습니다.

```sql
GRANT UPDATE (title, authors, publication_year, revision, updated_at)
ON slp_staging.server_papers TO "실제_worker_역할";
```
