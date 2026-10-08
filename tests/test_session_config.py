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
    monkeypatch.setattr("app.config._read_persisted_session_secret", lambda: "")
    monkeypatch.setattr("app.config._ensure_persisted_session_secret", lambda: "")
    load_config.cache_clear()
    cfg = AppConfig(env=EnvSettings(app_env="production", session_secret=""))
    with pytest.raises(ConfigurationError):
        validate_startup_config(cfg)
    load_config.cache_clear()


def test_session_secret_survives_process_restart(tmp_path, monkeypatch):
    from app import config as cfg

    secret_file = tmp_path / ".session_secret"
    monkeypatch.setattr(cfg, "SESSION_SECRET_FILE", secret_file)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_SECRET", "")
    load_config.cache_clear()
    cfg._EPHEMERAL_SESSION_SECRET = ""

    class _Env:
        session_secret = ""

    class _Cfg:
        env = _Env()

    monkeypatch.setattr(cfg, "load_config", lambda: _Cfg())
    first = cfg.session_secret_value()
    cfg._EPHEMERAL_SESSION_SECRET = ""
    second = cfg.session_secret_value()
    assert is_strong_secret(first)
    assert first == second
    assert secret_file.read_text(encoding="utf-8").strip() == first
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


def test_production_requires_mysql(monkeypatch):
    from app.config import AppConfig, EnvSettings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "pytest-session-secret-value-must-be-32bytes-min")
    monkeypatch.setenv("ALLOWED_HOSTS", "research.example.com")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")
    monkeypatch.setenv("MYSQL_HOST", "")
    load_config.cache_clear()
    cfg = AppConfig(
        env=EnvSettings(
            app_env="production",
            session_secret="pytest-session-secret-value-must-be-32bytes-min",
            allowed_hosts="research.example.com",
            trusted_proxy_ips="127.0.0.1",
            mysql_host="",
        )
    )
    with pytest.raises(ConfigurationError, match="MySQL"):
        validate_startup_config(cfg)
    load_config.cache_clear()
