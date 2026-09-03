"""CRUD for MySQL-backed settings and academic sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig, ProviderConfig, load_config
from app.database.settings_models import AcademicSource, AppSetting
from app.database.settings_store import settings_session
from app.database.source_catalog import BUILTIN_SOURCES, SOURCE_KEY_FIELDS
from app.utils.time import clear_timezone_cache, format_local, normalize_timezone, set_active_timezone, utc_now

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")

_ENV_KEY_GETTERS: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": "semantic_scholar_api_key",
    "CORE_API_KEY": "core_api_key",
    "SPRINGER_API_KEY": "springer_api_key",
    "ELSEVIER_API_KEY": "elsevier_api_key",
    "IEEE_API_KEY": "ieee_api_key",
    "NCBI_API_KEY": "ncbi_api_key",
    "NASA_ADS_TOKEN": "nasa_ads_token",
}


class SettingsError(ValueError):
    pass


def _as_str(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def get_setting(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return None if row is None else row.value


def set_setting(session: Session, key: str, value: object | None, *, group: str = "general", secret: bool = False) -> AppSetting:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, group_name=group, is_secret=secret)
        session.add(row)
    row.value = _as_str(value)
    row.group_name = group
    row.is_secret = secret
    row.updated_at = utc_now()
    return row


def all_settings(session: Session) -> dict[str, str]:
    return {row.key: row.value for row in session.scalars(select(AppSetting)).all()}


def seed_default_settings() -> None:
    cfg = load_config()
    with settings_session() as session:
        existing = all_settings(session)
        defaults: list[tuple[str, object, str, bool]] = [
            ("contact_email", cfg.env.contact_email, "workspace", False),
            ("unpaywall_email", cfg.env.unpaywall_email, "workspace", False),
            ("library_dir", str(cfg.library_dir), "workspace", False),
            ("check_robots_txt", cfg.check_robots_txt, "workspace", False),
            ("prefer_https", cfg.prefer_https, "workspace", False),
            ("timezone", cfg.timezone, "workspace", False),
            ("show_paywalled", cfg.show_paywalled, "workspace", False),
            ("download_limit", cfg.download_limit, "search", False),
            ("default_max_results", cfg.default_max_results, "search", False),
            ("max_concurrent_requests", cfg.env.max_concurrent_requests, "search", False),
            ("max_concurrent_downloads", cfg.env.max_concurrent_downloads, "search", False),
            ("request_timeout_seconds", cfg.env.request_timeout_seconds, "search", False),
            ("download_timeout_seconds", cfg.env.download_timeout_seconds, "search", False),
            ("max_redirects", cfg.env.max_redirects, "search", False),
        ]
        for key, value, group, secret in defaults:
            if key not in existing:
                set_setting(session, key, value, group=group, secret=secret)


def seed_academic_sources() -> None:
    cfg = load_config()
    with settings_session() as session:
        existing = {row.slug: row for row in session.scalars(select(AcademicSource)).all()}
        for item in BUILTIN_SOURCES:
            slug = str(item["slug"])
            pcfg = cfg.providers.get(slug, ProviderConfig())
            env_name = item.get("api_key_env")
            env_value = ""
            if isinstance(env_name, str) and env_name in _ENV_KEY_GETTERS:
                env_value = getattr(cfg.env, _ENV_KEY_GETTERS[env_name], "") or ""
            if slug in existing:
                continue
            session.add(
                AcademicSource(
                    slug=slug,
                    display_name=str(item["display_name"]),
                    description=str(item.get("description") or "") or None,
                    homepage_url=str(item.get("homepage_url") or "") or None,
                    api_base_url=str(item.get("api_base_url") or "") or None,
                    docs_url=str(item.get("docs_url") or "") or None,
                    enabled=bool(pcfg.enabled),
                    requires_key=bool(item.get("requires_key", pcfg.requires_key)),
                    api_key=env_value or None,
                    api_key_env=str(env_name) if env_name else None,
                    requests_per_second=float(pcfg.requests_per_second),
                    requests_per_second_with_key=pcfg.requests_per_second_with_key,
                    builtin=True,
                    sort_order=int(item.get("sort_order") or 100),
                )
            )


def list_academic_sources(session: Session | None = None) -> list[AcademicSource]:
    def _query(sess: Session) -> list[AcademicSource]:
        return list(
            sess.scalars(select(AcademicSource).order_by(AcademicSource.sort_order, AcademicSource.display_name)).all()
        )

    if session is not None:
        return _query(session)
    with settings_session() as sess:
        rows = _query(sess)
        sess.expunge_all()
        return rows


def get_academic_source(source_id: int, session: Session | None = None) -> AcademicSource | None:
    if session is not None:
        return session.get(AcademicSource, source_id)
    with settings_session() as sess:
        row = sess.get(AcademicSource, source_id)
        if row is not None:
            sess.expunge(row)
        return row


def get_academic_source_by_slug(slug: str, session: Session | None = None) -> AcademicSource | None:
    stmt = select(AcademicSource).where(AcademicSource.slug == slug)
    if session is not None:
        return session.scalar(stmt)
    with settings_session() as sess:
        row = sess.scalar(stmt)
        if row is not None:
            sess.expunge(row)
        return row


def _clean_url(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not re.match(r"^https?://", text, re.I):
        raise SettingsError("URLs must start with http:// or https://")
    return text


def _clean_slug(value: str) -> str:
    slug = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not SLUG_RE.match(slug):
        raise SettingsError("Source id must be 2–63 characters: start with a letter, then letters, numbers, or underscores.")
    return slug


def _clean_rps(value: object, *, field: str = "Rate limit") -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{field} must be a number.") from exc
    if number <= 0 or number > 100:
        raise SettingsError(f"{field} must be between 0.01 and 100 requests per second.")
    return number


def _payload_from_form(data: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    display_name = str(data.get("display_name") or "").strip()
    if not display_name:
        raise SettingsError("Display name is required.")
    payload: dict[str, Any] = {
        "display_name": display_name[:128],
        "description": str(data.get("description") or "").strip() or None,
        "homepage_url": _clean_url(data.get("homepage_url")),
        "api_base_url": _clean_url(data.get("api_base_url")),
        "docs_url": _clean_url(data.get("docs_url")),
        "enabled": str(data.get("enabled") or "").lower() in {"1", "true", "on", "yes"},
        "requires_key": str(data.get("requires_key") or "").lower() in {"1", "true", "on", "yes"},
        "notes": str(data.get("notes") or "").strip() or None,
        "requests_per_second": _clean_rps(data.get("requests_per_second") or 5),
    }
    with_key = data.get("requests_per_second_with_key")
    if with_key not in (None, ""):
        payload["requests_per_second_with_key"] = _clean_rps(with_key, field="Rate limit with key")
    else:
        payload["requests_per_second_with_key"] = None
    if creating:
        payload["slug"] = _clean_slug(str(data.get("slug") or display_name))
        payload["api_key_env"] = str(data.get("api_key_env") or "").strip().upper() or None
        payload["sort_order"] = int(data.get("sort_order") or 200)
        payload["builtin"] = False
    api_key = data.get("api_key")
    clear_key = str(data.get("clear_api_key") or "").lower() in {"1", "true", "on", "yes"}
    if clear_key:
        payload["api_key"] = None
    elif api_key is not None and str(api_key).strip():
        payload["api_key"] = str(api_key).strip()
    return payload


def create_academic_source(data: dict[str, Any]) -> AcademicSource:
    payload = _payload_from_form(data, creating=True)
    with settings_session() as session:
        if session.scalar(select(AcademicSource).where(AcademicSource.slug == payload["slug"])):
            raise SettingsError(f"A source named “{payload['slug']}” already exists.")
        row = AcademicSource(**payload)
        session.add(row)
        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row


def update_academic_source(source_id: int, data: dict[str, Any]) -> AcademicSource:
    payload = _payload_from_form(data, creating=False)
    with settings_session() as session:
        row = session.get(AcademicSource, source_id)
        if row is None:
            raise SettingsError("Academic source not found.")
        for key, value in payload.items():
            setattr(row, key, value)
        row.updated_at = utc_now()
        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row


def delete_academic_source(source_id: int) -> None:
    with settings_session() as session:
        row = session.get(AcademicSource, source_id)
        if row is None:
            raise SettingsError("Academic source not found.")
        if row.builtin:
            raise SettingsError("Built-in academic sources cannot be deleted. Disable them instead.")
        session.delete(row)


def toggle_academic_source(source_id: int, enabled: bool | None = None) -> AcademicSource:
    with settings_session() as session:
        row = session.get(AcademicSource, source_id)
        if row is None:
            raise SettingsError("Academic source not found.")
        row.enabled = (not row.enabled) if enabled is None else bool(enabled)
        row.updated_at = utc_now()
        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row


def save_workspace_settings(data: dict[str, Any]) -> None:
    contact = str(data.get("contact_email") or "").strip()
    if contact and "@" not in contact:
        raise SettingsError("Contact email looks invalid.")
    unpaywall = str(data.get("unpaywall_email") or "").strip()
    if unpaywall and "@" not in unpaywall:
        raise SettingsError("Unpaywall email looks invalid.")
    library_dir = str(data.get("library_dir") or "research_library").strip() or "research_library"
    if Path(library_dir).is_absolute() is False:
        Path(library_dir)
    try:
        timezone_name = normalize_timezone(str(data.get("timezone") or "UTC"))
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    with settings_session() as session:
        set_setting(session, "contact_email", contact, group="workspace")
        set_setting(session, "unpaywall_email", unpaywall, group="workspace")
        set_setting(session, "library_dir", library_dir, group="workspace")
        set_setting(session, "check_robots_txt", bool(data.get("check_robots_txt")), group="workspace")
        set_setting(session, "prefer_https", bool(data.get("prefer_https")), group="workspace")
        set_setting(session, "timezone", timezone_name, group="workspace")
        set_setting(session, "show_paywalled", bool(data.get("show_paywalled", True)), group="workspace")
    set_active_timezone(timezone_name)
    clear_timezone_cache()


def save_search_settings(data: dict[str, Any]) -> None:
    def _int(name: str, minimum: int, maximum: int) -> int:
        try:
            value = int(float(data.get(name)))
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{name.replace('_', ' ').title()} must be a number.") from exc
        if value < minimum or value > maximum:
            raise SettingsError(f"{name.replace('_', ' ').title()} must be between {minimum} and {maximum}.")
        return value

    def _float(name: str, minimum: float, maximum: float) -> float:
        try:
            value = float(data.get(name))
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{name.replace('_', ' ').title()} must be a number.") from exc
        if value < minimum or value > maximum:
            raise SettingsError(f"{name.replace('_', ' ').title()} must be between {minimum} and {maximum}.")
        return value

    with settings_session() as session:
        set_setting(session, "download_limit", _int("download_limit", 1, 1000), group="search")
        set_setting(session, "default_max_results", _int("default_max_results", 1, 500), group="search")
        set_setting(session, "max_concurrent_requests", _int("max_concurrent_requests", 1, 20), group="search")
        set_setting(session, "max_concurrent_downloads", _int("max_concurrent_downloads", 1, 10), group="search")
        set_setting(session, "request_timeout_seconds", _float("request_timeout_seconds", 5, 120), group="search")
        set_setting(session, "download_timeout_seconds", _float("download_timeout_seconds", 15, 600), group="search")
        set_setting(session, "max_redirects", _int("max_redirects", 0, 10), group="search")


def save_credential_settings(data: dict[str, Any]) -> None:
    """Update API keys on matching academic sources. Empty fields keep the current value."""
    with settings_session() as session:
        for slug, _field in SOURCE_KEY_FIELDS.items():
            clear = str(data.get(f"clear_{slug}") or "").lower() in {"1", "true", "on", "yes"}
            raw = str(data.get(slug) or "").strip()
            row = session.scalar(select(AcademicSource).where(AcademicSource.slug == slug))
            if row is None:
                continue
            if clear:
                row.api_key = None
            elif raw:
                row.api_key = raw
            row.updated_at = utc_now()


def source_has_key(row: AcademicSource) -> bool:
    if row.api_key:
        return True
    cfg = load_config()
    field = SOURCE_KEY_FIELDS.get(row.slug)
    if field:
        return bool(getattr(cfg.env, field, ""))
    return False


def source_is_available(row: AcademicSource) -> bool:
    if not row.enabled:
        return False
    if row.requires_key and not source_has_key(row):
        return False
    return True


def source_to_dict(row: AcademicSource, *, include_secret: bool = False) -> dict[str, Any]:
    has_key = source_has_key(row)
    payload = {
        "id": row.id,
        "slug": row.slug,
        "display_name": row.display_name,
        "description": row.description or "",
        "homepage_url": row.homepage_url or "",
        "api_base_url": row.api_base_url or "",
        "docs_url": row.docs_url or "",
        "enabled": bool(row.enabled),
        "requires_key": bool(row.requires_key),
        "has_key": has_key,
        "api_key_env": row.api_key_env or "",
        "requests_per_second": row.requests_per_second,
        "requests_per_second_with_key": row.requests_per_second_with_key,
        "builtin": bool(row.builtin),
        "notes": row.notes or "",
        "sort_order": row.sort_order,
        "available": source_is_available(row),
        "kind": "Built-in" if row.builtin else "Custom",
        "updated_at": format_local(row.updated_at) if row.updated_at else "",
    }
    if include_secret:
        payload["api_key"] = row.api_key or ""
    return payload


def apply_runtime_overlay(cfg: AppConfig) -> AppConfig:
    """Mutate a copied AppConfig with MySQL-stored settings and source flags."""
    try:
        with settings_session() as session:
            stored = all_settings(session)
            sources = list(session.scalars(select(AcademicSource)).all())
    except Exception:
        return cfg

    def _bool(key: str, default: bool) -> bool:
        raw = stored.get(key)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _int(key: str, default: int) -> int:
        raw = stored.get(key)
        if raw in (None, ""):
            return default
        try:
            return int(float(raw))
        except ValueError:
            return default

    def _float(key: str, default: float) -> float:
        raw = stored.get(key)
        if raw in (None, ""):
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    if stored.get("contact_email"):
        cfg.env.contact_email = stored["contact_email"]
    if "unpaywall_email" in stored:
        cfg.env.unpaywall_email = stored.get("unpaywall_email") or ""
    cfg.env.max_concurrent_requests = _int("max_concurrent_requests", cfg.env.max_concurrent_requests)
    cfg.env.max_concurrent_downloads = _int("max_concurrent_downloads", cfg.env.max_concurrent_downloads)
    cfg.env.request_timeout_seconds = _float("request_timeout_seconds", cfg.env.request_timeout_seconds)
    cfg.env.download_timeout_seconds = _float("download_timeout_seconds", cfg.env.download_timeout_seconds)
    cfg.env.max_redirects = _int("max_redirects", cfg.env.max_redirects)
    cfg.download_limit = _int("download_limit", cfg.download_limit)
    cfg.default_max_results = _int("default_max_results", cfg.default_max_results)
    cfg.check_robots_txt = _bool("check_robots_txt", cfg.check_robots_txt)
    cfg.prefer_https = _bool("prefer_https", cfg.prefer_https)
    cfg.show_paywalled = _bool("show_paywalled", cfg.show_paywalled)
    if stored.get("timezone"):
        try:
            cfg.timezone = normalize_timezone(stored["timezone"])
        except ValueError:
            pass
    set_active_timezone(cfg.timezone)
    if stored.get("library_dir"):
        cfg.library_dir = Path(stored["library_dir"])

    for row in sources:
        pcfg = cfg.providers.get(row.slug) or ProviderConfig()
        pcfg.enabled = bool(row.enabled)
        pcfg.requires_key = bool(row.requires_key)
        pcfg.requests_per_second = float(row.requests_per_second)
        pcfg.requests_per_second_with_key = row.requests_per_second_with_key
        cfg.providers[row.slug] = pcfg
        field = SOURCE_KEY_FIELDS.get(row.slug)
        if field and row.api_key:
            setattr(cfg.env, field, str(row.api_key).strip())
    return cfg
