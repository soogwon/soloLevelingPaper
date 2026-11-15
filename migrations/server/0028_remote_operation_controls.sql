ALTER TABLE server_dispatch_controls ADD COLUMN intake_enabled boolean NULL;
UPDATE server_dispatch_controls SET intake_enabled=true WHERE intake_enabled IS NULL;
ALTER TABLE server_dispatch_controls ALTER COLUMN intake_enabled SET NOT NULL;
-- 기존 행은 허용을 보존하되, 새 제어 행은 명시적으로 열기 전까지 접수를 막는다.
ALTER TABLE server_dispatch_controls ALTER COLUMN intake_enabled SET DEFAULT false;
