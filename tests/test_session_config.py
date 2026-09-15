import pytest

from app.config import is_strong_secret, load_config, validate_startup_config
from app.exceptions import ConfigurationError


def test_weak_session_secret_rejected():
    assert not is_strong_secret("")
    assert not is_strong_secret("change-me-in-production-please-use-a-long-random-string")
    assert not is_strong_secret("short")
    assert is_strong_secret("pytest-session-secret-value-must-be-32bytes-min")


def test_production_requires_session_secret(monkeypatch):
    from app.config import AppConfig, EnvSettings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "")
    load_config.cache_clear()
    cfg = AppConfig(env=EnvSettings(app_env="production", session_secret=""))
    with pytest.raises(ConfigurationError):
        validate_startup_config(cfg)
    load_config.cache_clear()


def test_invalid_host_header_rejected(auth_client):
    response = auth_client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400
