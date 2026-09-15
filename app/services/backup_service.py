"""Backup and restore for the research library, settings DB, and config."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT_DIR, get_runtime_config

ALLOWED_MEMBERS = (
    "research.db",
    "research.db-wal",
    "research.db-shm",
    "settings.db",
    "settings.db-wal",
    "settings.db-shm",
    "research_library",
    "config.yaml",
    "manifest.json",
)


def _safe_member_name(name: str) -> str:
    text = name.replace("\\", "/").lstrip("/")
    if text.startswith("..") or "/../" in f"/{text}/":
        raise ValueError(f"Unsafe backup path: {name}")
    return text


def create_backup(dest_dir: Path | None = None) -> Path:
    cfg = get_runtime_config()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(dest_dir or (ROOT_DIR / "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"cyber-scholar-{stamp}.tar.gz"
    manifest: dict[str, object] = {
        "created_at": stamp,
        "version": cfg.version,
        "files": [],
    }
    files: list[str] = []
    with tarfile.open(archive, "w:gz") as tar:
        db_path = (ROOT_DIR / cfg.env.database_path).resolve()
        if db_path.exists():
            tar.add(db_path, arcname="research.db")
            files.append("research.db")
            for suffix in ("-wal", "-shm"):
                extra = Path(str(db_path) + suffix)
                if extra.exists():
                    tar.add(extra, arcname=f"research.db{suffix}")
                    files.append(f"research.db{suffix}")
        settings = (ROOT_DIR / cfg.env.settings_sqlite_path).resolve()
        if settings.exists():
            tar.add(settings, arcname="settings.db")
            files.append("settings.db")
        library = cfg.resolve_path(cfg.library_dir)
        if library.exists():
            tar.add(library, arcname="research_library")
            files.append("research_library")
        config_yaml = ROOT_DIR / "config.yaml"
        if config_yaml.exists():
            tar.add(config_yaml, arcname="config.yaml")
            files.append("config.yaml")
        manifest["files"] = files
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, fileobj=io.BytesIO(payload))
    return archive


def restore_backup(archive: Path, *, dest_root: Path | None = None) -> Path:
    path = Path(archive).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    root = Path(dest_root or ROOT_DIR).resolve()
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            name = _safe_member_name(member.name)
            top = name.split("/", 1)[0]
            if top not in ALLOWED_MEMBERS and name not in ALLOWED_MEMBERS:
                raise ValueError(f"Refusing to extract unexpected path: {member.name}")
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Refusing path traversal: {member.name}")
        tar.extractall(root, filter="data")
    return root
