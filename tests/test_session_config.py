import pytest

from app.config import is_strong_secret, load_config, validate_startup_config
from app.exceptions import ConfigurationError


def test_weak_session_secret_rejected():
    assert not is_strong_secret("")
    assert not is_strong_secret("change-me-in-production-please-use-a-long-random-string")
    assert not is_strong_secret("short")
    assert is_strong_secret("pytest-session-secret-value-must-be-32bytes-min")


def test_production_requires_explicit_allowed_hosts(monkeypatch):
    from app.config import AppConfig, EnvSettings, load_config

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "pytest-session-secret-value-must-be-32bytes-min")
    monkeypatch.setenv("ALLOWED_HOSTS", "")
    load_config.cache_clear()
    cfg = AppConfig(
        env=EnvSettings(
            app_env="production",
            session_secret="pytest-session-secret-value-must-be-32bytes-min",
            allowed_hosts="",
        )
    )
    with pytest.raises(ConfigurationError, match="ALLOWED_HOSTS"):
        validate_startup_config(cfg)
    load_config.cache_clear()


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


def test_development_allows_any_host(monkeypatch):
    from app.config import allowed_hosts, load_config

    monkeypatch.setenv("APP_ENV", "development")
    load_config.cache_clear()
    assert allowed_hosts() == ["*"]
    load_config.cache_clear()


def test_production_allowed_hosts_include_bind_address(monkeypatch):
    from app.config import allowed_hosts, load_config

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOWED_HOSTS", "research.example.com")
    monkeypatch.setenv("APP_HOST", "10.0.0.8")
    load_config.cache_clear()
    hosts = allowed_hosts()
    assert "research.example.com" in hosts
    assert "localhost" in hosts
    assert "127.0.0.1" in hosts
    assert "10.0.0.8" in hosts
    assert "*" not in hosts
    load_config.cache_clear()
