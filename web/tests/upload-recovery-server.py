"""제품 웹을 그대로 제공하는 로컬 오류 주입 서버. 외부 연결은 하지 않는다."""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

WEB = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
RELEASE = threading.Event()
STATE = {"requests": [], "intents": {}, "logged_out": False, "late_sent": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *_args):
        pass

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/__report":
            with LOCK:
                self.reply(200, STATE)
        elif self.path == "/v1/me":
            self.reply(401 if STATE["logged_out"] else 200,
                       {"user_id": "synthetic-owner", "display_name": "합성 검증 계정"})
        elif self.path == "/v1/devices":
            self.reply(200, [])
        elif self.path.startswith("/v1/remote-uploads/intents/"):
            target = self.path.rsplit("/", 1)[-1]
            with LOCK:
                STATE["requests"].append({"method": "GET", "id": target})
                found = next((v["response"] for v in STATE["intents"].values()
                              if v["response"]["intent_id"] == target), None)
            self.reply(200 if found else 404, found or {})
        elif self.path.startswith("/v1/"):
            self.reply(403, {"error": "검증 범위 밖의 API는 차단합니다"})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/v1/auth/logout":
            STATE["logged_out"] = True
            self.reply(200, {})
            RELEASE.set()
            return
        if self.path != "/v1/remote-uploads/intents":
            self.reply(403, {"error": "PDF 전송과 AI 호출은 지원하지 않습니다"})
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size < 2048:
            self.reply(413, {})
            return
        body = json.loads(self.rfile.read(size))
        if set(body) != {"file_name", "file_size", "content_type", "sha256"}:
            self.reply(422, {})
            return
        key = self.headers.get("Idempotency-Key")
        if not key:
            self.reply(422, {})
            return
        with LOCK:
            STATE["requests"].append({"method": "POST", "key": key, "body": body})
            existing = STATE["intents"].get(key)
            if existing and existing["body"] != body:
                self.reply(409, {})
                return
            first = not existing
            if first:
                existing = {"body": body, "response": {
                    "intent_id": str(uuid4()), "paper_id": str(uuid4()),
                    "version_id": str(uuid4()), "status": "created",
                    "expires_at": "2099-01-01T00:00:00Z"}}
                STATE["intents"][key] = existing
            count = len(STATE["intents"])
        if first and count == 1:
            # 저장은 완료했지만 JSON 본문 도중 연결을 끊는다.
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"intent_id":')
            self.wfile.flush()
            self.close_connection = True
            return
        if first and count == 2:
            if not RELEASE.wait(300):
                self.reply(503, {})
                return
            # 로그아웃 응답이 먼저 도착한 뒤 이전 성공 응답을 반환한다.
            time.sleep(1)
            self.reply(201, existing["response"])
            STATE["late_sent"] = True
            return
        self.reply(201, existing["response"])


if __name__ == "__main__":
    print("http://127.0.0.1:8793/#/uploads", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8793), Handler).serve_forever()
