ALTER TABLE server_dispatch_controls ADD COLUMN intake_enabled boolean NULL;
UPDATE server_dispatch_controls SET intake_enabled=true WHERE intake_enabled IS NULL;
ALTER TABLE server_dispatch_controls ALTER COLUMN intake_enabled SET NOT NULL;
