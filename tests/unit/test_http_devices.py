"""기기 저장소의 소유권 SQL과 웹 관리 API를 검증한다."""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.devices import DeviceRecord, PostgresDeviceStore
from solo_leveling.interfaces.http.settings import DatabaseSettings, load_database_settings


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


class Result:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, many

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0)


class Pool:
    def __init__(self, results):
        self.conn = Connection(results)

    def connection(self):
        return nullcontext(self.conn)


def row(device_id="00000000-0000-0000-0000-000000000001", owner="user-1"):
    return {
        "device_id": device_id,
        "owner_user_id": owner,
        "display_name": "My PC",
        "created_at": NOW,
        "revoked_at": None,
    }


def test_postgres_queries_always_include_owner_and_active_state():
    pool = Pool([Result(one={"?column?": 1}), Result(many=[row()]), Result()])
    store = PostgresDeviceStore(pool)

    assert store.is_active("user-1", "device-1") is True
    assert store.list_active("user-1")[0].owner_user_id == "user-1"
    store.revoke("user-1", "device-1")

    assert pool.conn.calls[0][1] == ("user-1", "device-1")
    assert "owner_user_id = %s" in pool.conn.calls[0][0] and "revoked_at IS NULL" in pool.conn.calls[0][0]
    assert pool.conn.calls[1][1] == ("user-1",)
    assert "owner_user_id = %s" in pool.conn.calls[1][0] and "revoked_at IS NULL" in pool.conn.calls[1][0]
    assert pool.conn.calls[2][1] == ("user-1", "device-1")
    assert "owner_user_id = %s" in pool.conn.calls[2][0] and "revoked_at IS NULL" in pool.conn.calls[2][0]


class MemoryStore:
    def __init__(self):
        self.items = []
        self.calls = []

    def __len__(self):
        return 0

    def is_active(self, user_id, device_id):
        return any(item.owner_user_id == user_id and item.device_id == device_id for item in self.items)

    def register(self, owner_user_id, display_name):
        self.calls.append(("register", owner_user_id, display_name))
        item = DeviceRecord("00000000-0000-0000-0000-000000000001", owner_user_id, display_name, NOW)
        self.items.append(item)
        return item

    def list_active(self, owner_user_id):
        self.calls.append(("list", owner_user_id))
        return tuple(item for item in self.items if item.owner_user_id == owner_user_id)

    def revoke(self, owner_user_id, device_id):
        self.calls.append(("revoke", owner_user_id, device_id))


def route(app, path, method):
    return next(item for item in app.routes if item.path == path and method in item.methods).endpoint


def test_web_user_can_register_list_and_revoke_without_exposing_owner_id():
    store = MemoryStore()
    app = create_app(device_store=store)
    principal = AuthenticatedPrincipal("user-1", "web_session")

    created = route(app, "/v1/devices", "POST")(SimpleNamespace(display_name="My PC"), principal)
    listed = route(app, "/v1/devices", "GET")(principal)
    response = route(app, "/v1/devices/{device_id}", "DELETE")(
        UUID("00000000-0000-0000-0000-000000000001"), principal
    )

    assert created.model_dump() == {
        "device_id": "00000000-0000-0000-0000-000000000001",
        "display_name": "My PC",
        "created_at": NOW,
    }
    assert [item.model_dump() for item in listed] == [created.model_dump()]
    assert response.status_code == 204
    assert store.calls[-1] == ("revoke", "user-1", "00000000-0000-0000-0000-000000000001")
    assert "owner_user_id" not in created.model_dump()


def test_device_credential_cannot_manage_devices():
    app = create_app(device_store=MemoryStore())
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")

    with pytest.raises(HTTPException) as caught:
        route(app, "/v1/devices", "GET")(principal)

    assert caught.value.status_code == 403
    assert caught.value.detail == {"code": "web_session_required"}


def test_openapi_exposes_device_management_contract():
    schema = create_app().openapi()
    paths = schema["paths"]

    assert set(paths["/v1/devices"]) >= {"get", "post"}
    assert "delete" in paths["/v1/devices/{device_id}"]
    assert paths["/v1/devices"]["post"]["security"] == [{"ServerAccessToken": []}]


def test_database_settings_are_optional_but_only_accept_postgres():
    assert load_database_settings({}) is None
    assert load_database_settings({"DATABASE_URL": "postgresql://db/service"}) == DatabaseSettings(
        "postgresql://db/service"
    )
    with pytest.raises(ValueError, match="PostgreSQL"):
        load_database_settings({"DATABASE_URL": "sqlite:///local.db"})
