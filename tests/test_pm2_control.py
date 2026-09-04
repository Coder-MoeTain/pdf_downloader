"""Tests for PM2 control helpers."""

from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from app.utils import pm2_control


def test_pm2_status_found(monkeypatch):
    payload = [
        {
            "name": "researchpaper",
            "monit": {"pid": 1234, "memory": 1048576, "cpu": 2},
            "pm2_env": {"status": "online", "pm_uptime": 1_700_000_000_000, "restart_time": 3},
        }
    ]

    def fake_run(*args, **kwargs):
        return CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    status = pm2_control.pm2_status("researchpaper")
    assert status["ok"] is True
    assert status["status"] == "online"
    assert status["pid"] == 1234
    assert status["restarts"] == 3


def test_pm2_restart_success(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        calls.append(list(args))
        if args[:2] == ("restart", "researchpaper"):
            return CompletedProcess(args, 0, stdout="[PM2] Applying action restartProcessId on app [researchpaper]", stderr="")
        payload = [{"name": "researchpaper", "monit": {}, "pm2_env": {"status": "online", "pm_uptime": 0}}]
        return CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    result = pm2_control.pm2_restart("researchpaper")
    assert result["ok"] is True
    assert "restart" in calls[0]


def test_pm2_logs_success(monkeypatch):
    def fake_run(*args, **kwargs):
        assert args[:4] == ("logs", "researchpaper", "--lines", "50")
        assert args[4] == "--nostream"
        return CompletedProcess(args, 0, stdout="researchpaper-out.log last lines:\nhello", stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    result = pm2_control.pm2_logs("researchpaper", lines=50)
    assert result["ok"] is True
    assert "hello" in result["output"]


def test_pm2_missing_process(monkeypatch):
    def fake_run(*args, **kwargs):
        return CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    status = pm2_control.pm2_status("missing-app")
    assert status["ok"] is False
    assert "not found" in status["error"].lower()


def test_pm2_not_installed(monkeypatch):
    def fake_run(*args, **kwargs):
        raise pm2_control.Pm2Error("pm2 is not installed on this server.")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    with pytest.raises(pm2_control.Pm2Error):
        pm2_control.pm2_restart()


def test_pm2_status_ignores_daemon_banner(monkeypatch):
    payload = [
        {
            "name": "researchpaper",
            "monit": {"pid": 99, "memory": 2048, "cpu": 1},
            "pm2_env": {"status": "online", "pm_uptime": 1_700_000_000_000, "restart_time": 1},
        }
    ]
    banner = "[PM2][WARN] Current process is under PM2\n>>>> In-memory PM2 is out-of-date\n"

    def fake_run(*args, **kwargs):
        return CompletedProcess(args, 0, stdout=banner + json.dumps(payload), stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    status = pm2_control.pm2_status("researchpaper")
    assert status["ok"] is True
    assert status["pid"] == 99
    assert status["status"] == "online"


def test_pm2_status_reads_dump_when_jlist_is_garbage(monkeypatch, tmp_path):
    dump = [
        {
            "name": "researchpaper",
            "pm2_env": {"status": "online", "pm_pid": 4242, "restart_time": 2, "pm_uptime": 0},
        }
    ]
    dump_file = tmp_path / "dump.pm2"
    dump_file.write_text(json.dumps(dump), encoding="utf-8")

    def fake_run(*args, **kwargs):
        return CompletedProcess(args, 0, stdout="Initializing folder...\nnot json", stderr="")

    monkeypatch.setattr(pm2_control, "_run_pm2", fake_run)
    monkeypatch.setattr(pm2_control, "_pm2_home", lambda: tmp_path)
    status = pm2_control.pm2_status("researchpaper")
    assert status["ok"] is True
    assert status["pid"] == 4242
    assert status["status"] == "online"


def test_parse_jlist_accepts_wrapped_object():
    processes = pm2_control._parse_jlist(json.dumps({"processes": [{"name": "researchpaper"}]}))
    assert processes[0]["name"] == "researchpaper"
