"""합성 계정의 동의 UI 수동 검증용 서버. PDF 업로드·AI 실행은 지원하지 않는다."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
ID = "11111111-1111-4111-8111-111111111111"
VALUES = {"storage": None, "ai_provider": None}
REQUESTS = []
KEYS = {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *_args):
        pass

    def reply(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/__report":
            return self.reply(REQUESTS)
        if self.path == "/v1/me":
            return self.reply({"user_id": "synthetic", "display_name": "합성 동의 검증"})
        if self.path.endswith("/consents"):
            return self.reply({"intent_id": ID, "notice_version": "synthetic-v1",
                               "storage_notice": "합성 고지: PDF를 서버에 보관하는 별도 동의입니다.",
                               "ai_provider_notice": "합성 고지: OpenAI 전송과 비용에 대한 별도 동의입니다.",
                               **VALUES})
        if self.path == f"/v1/remote-uploads/intents/{ID}":
            # 실제 파일 전송 없이 AI 동의 UI를 검증하기 위한 상태 대역이다.
            return self.reply(self.intent("verified"))
        if self.path.startswith("/v1/"):
            return self.reply({}, 403)
        return super().do_GET()

    @staticmethod
    def intent(status):
        return {"intent_id": ID, "paper_id": ID, "version_id": ID,
                "status": status, "expires_at": "2099-01-01T00:00:00Z"}

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size < 2048:
            return self.reply({}, 413)
        body = json.loads(self.rfile.read(size))
        key = self.headers.get("Idempotency-Key")
        REQUESTS.append({"path": self.path, "body": body, "key": key})
        if self.path == "/v1/remote-uploads/intents":
            if set(body) != {"file_name", "file_size", "content_type", "sha256"}:
                return self.reply({}, 422)
            return self.reply(self.intent("created"))
        parts = self.path.split("/")
        if len(parts) not in (7, 8) or parts[5] != "consents" or parts[6] not in VALUES:
            return self.reply({}, 403)
        purpose = parts[6]
        previous = VALUES[purpose]
        if len(parts) == 8:
            if parts[7] != "revoke" or not previous or body != {"expected_revision": previous["revision"]}:
                return self.reply({}, 409)
            VALUES[purpose] = {**previous, "state": "revoked", "revoked_at": "2026-11-16T00:01:00Z"}
        else:
            if body != {"notice_version": "synthetic-v1"}:
                return self.reply({"detail": {"code": "remote_upload_consent_notice_changed"}}, 409)
            if (purpose, key) in KEYS:
                return self.reply(KEYS[purpose, key])
            VALUES[purpose] = {"consent_id": ID, "purpose": purpose, "state": "current",
                               "revision": previous["revision"] + 1 if previous else 1,
                               "notice_version": "synthetic-v1", "granted_at": "2026-11-16T00:00:00Z",
                               "revoked_at": None}
            KEYS[purpose, key] = VALUES[purpose]
        return self.reply(VALUES[purpose])


if __name__ == "__main__":
    print("http://127.0.0.1:8794/#/uploads", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8794), Handler).serve_forever()
