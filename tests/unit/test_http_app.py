"""중앙 HTTP API의 실행 골격을 검증한다."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http import runtime
from solo_leveling.interfaces.http.settings import HttpSettings, load_http_settings


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
        "host": "0.0.0.0",
        "port": 8080,
    })]
