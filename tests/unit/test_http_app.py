"""중앙 HTTP API의 실행 골격을 검증한다."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http import runtime
from solo_leveling.interfaces.http.settings import (
    HttpSettings,
    load_http_settings,
    load_web_auth_settings,
    load_web_static_root,
)


def test_package_import_without_server_dependencies():
    subprocess.run(
        [sys.executable, "-E", "-S", "-c", "from solo_leveling.interfaces.http import create_app"],
        cwd=Path(__file__).resolve().parents[2] / "src",
        check=True,
    )


def test_health_reports_process_status():
    app = create_app(HttpSettings(environment="test"))
    route = next(route for route in app.routes if route.path == "/health")
    response = route.endpoint()

    assert response == {
        "status": "ok",
        "service": "solo-leveling-api",
        "environment": "test",
    }


def test_http_settings_are_loaded_from_environment_mapping():
    settings = load_http_settings({"APP_ENV": "staging", "API_HOST": "0.0.0.0", "API_PORT": "8080"})

    assert settings == HttpSettings(environment="staging", host="0.0.0.0", port=8080)


def test_render_port_is_used_when_api_port_is_absent():
    assert load_http_settings({"PORT": "10000"}).port == 10000
    assert load_http_settings({"PORT": "10000", "API_PORT": "8080"}).port == 8080


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_api_port_is_rejected(port):
    with pytest.raises(ValueError, match="API_PORT"):
        load_http_settings({"API_PORT": port})


def test_runtime_starts_application_factory_with_environment(monkeypatch):
    calls = []
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8080")
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))))

    runtime.main()

    assert calls == [(('solo_leveling.interfaces.http.app:create_app',), {
        "factory": True,
        "access_log": False,
        "host": "0.0.0.0",
        "port": 8080,
    })]


def test_web_auth_settings_are_all_or_none_and_https():
    assert load_web_auth_settings({}) is None
    values = {
        "WEB_OIDC_AUTHORIZATION_URL": "https://id.example/authorize",
        "WEB_OIDC_TOKEN_URL": "https://id.example/token",
        "WEB_OIDC_USERINFO_URL": "https://id.example/userinfo",
        "WEB_OIDC_CLIENT_ID": "client", "WEB_OIDC_CLIENT_SECRET": "secret",
        "WEB_APP_ORIGIN": "https://app.example",
        "WEB_OIDC_REDIRECT_URI": "https://app.example/v1/auth/callback",
    }
    assert load_web_auth_settings(values).client_id == "client"
    with pytest.raises(ValueError):
        load_web_auth_settings(values | {"WEB_OIDC_TOKEN_URL": ""})
    with pytest.raises(ValueError):
        load_web_auth_settings(values | {"WEB_APP_ORIGIN": "http://app.example"})
    with pytest.raises(ValueError):
        load_web_auth_settings(values | {"WEB_OIDC_REDIRECT_URI": "https://api.example/v1/auth/callback"})


def test_static_web_root_is_validated_and_mounted_after_api_routes(tmp_path, monkeypatch):
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
    monkeypatch.setenv("WEB_STATIC_ROOT", str(web_root))

    assert load_web_static_root() == web_root
    app = create_app(HttpSettings(environment="test"))
    route_names = [route.name for route in app.routes]

    assert route_names.index("health") < route_names.index("external-site")
    assert app.routes[-1].name == "external-site"


def test_static_web_root_rejects_missing_index(tmp_path):
    with pytest.raises(ValueError, match="index.html"):
        load_web_static_root({"WEB_STATIC_ROOT": str(tmp_path)})
