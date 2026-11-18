-- 취소 전용 역할은 실행/성공/실패 전환이나 취소된 작업의 재활성화를 할 수 없다.
CREATE FUNCTION server_processing_cancel_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user=TG_ARGV[0] AND (
        OLD.status NOT IN ('queued','running') OR NEW.status <> 'cancelled'
        OR NEW.retryable OR NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL
        OR NEW.lease_expires_at IS NOT NULL OR NEW.heartbeat_at IS NOT NULL
        OR NEW.finished_at IS NULL
    ) THEN
        RAISE EXCEPTION 'processing_cancel_only' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
