-- 호출자 신고인 최초 작성 주체. 소유 계정·전송 기기 정보와 구분한다.
ALTER TABLE server_annotations ADD COLUMN creation_origin text NOT NULL DEFAULT 'unknown'
    CHECK (creation_origin IN ('user', 'ai', 'unknown'));
