"""MySQL settings store should connect even when CREATE DATABASE is denied."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import OperationalError


def test_try_mysql_connects_without_create_privilege(monkeypatch):
    from app.database import settings_store

    calls: list[str] = []

    def fake_connect(host, port, user, password, database):
        calls.append("connect")
        return SimpleNamespace(dispose=lambda: None)

    def fake_ensure(*_args, **_kwargs):
        calls.append("create")
        raise AssertionError("CREATE DATABASE should not run when connect succeeds")

    monkeypatch.setattr(
        settings_store,
        "load_config",
        lambda: SimpleNamespace(
            env=SimpleNamespace(
                mysql_host="127.0.0.1",
                mysql_port=3306,
                mysql_user="cyber_admin",
                mysql_password="secret",
                mysql_database="research_collector",
            )
        ),
    )
    monkeypatch.setattr(settings_store, "_connect_mysql", fake_connect)
    monkeypatch.setattr(settings_store, "_ensure_mysql_database", fake_ensure)

    engine = settings_store._try_mysql()
    assert engine is not None
    assert calls == ["connect"]
    assert settings_store.store_status().backend == "mysql"


def test_try_mysql_falls_back_when_create_also_fails(monkeypatch):
    from app.database import settings_store

    def boom(*_args, **_kwargs):
        raise OperationalError("CREATE DATABASE IF NOT EXISTS", {}, Exception("1044"))

    monkeypatch.setattr(
        settings_store,
        "load_config",
        lambda: SimpleNamespace(
            env=SimpleNamespace(
                mysql_host="127.0.0.1",
                mysql_port=3306,
                mysql_user="cyber_admin",
                mysql_password="secret",
                mysql_database="research_collector",
            )
        ),
    )
    monkeypatch.setattr(settings_store, "_connect_mysql", boom)
    monkeypatch.setattr(settings_store, "_ensure_mysql_database", boom)

    assert settings_store._try_mysql() is None
