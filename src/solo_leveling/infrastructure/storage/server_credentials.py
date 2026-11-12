"""서버 기기 자격을 운영체제 자격 증명 저장소에 보관한다."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json


SERVICE_NAME = "solo-leveling-paper"


@dataclass(frozen=True)
class StoredDeviceCredentials:
    server_url: str
    device_id: str
    access_token: str
    access_expires_at: str
    refresh_token: str
    refresh_expires_at: str


class KeyringCredentialStore:
    def save(self, credentials: StoredDeviceCredentials) -> None:
        import keyring

        payload = json.dumps(asdict(credentials), separators=(",", ":"), ensure_ascii=True)
        keyring.set_password(SERVICE_NAME, _account(credentials.server_url), payload)

    def load(self, server_url: str) -> StoredDeviceCredentials | None:
        import keyring

        payload = keyring.get_password(SERVICE_NAME, _account(server_url))
        if payload is None:
            return None
        data = json.loads(payload)
        return StoredDeviceCredentials(**data)

    def delete(self, server_url: str) -> None:
        import keyring

        keyring.delete_password(SERVICE_NAME, _account(server_url))


def _account(server_url: str) -> str:
    digest = sha256(server_url.encode("utf-8")).hexdigest()[:24]
    return f"device:{digest}"
