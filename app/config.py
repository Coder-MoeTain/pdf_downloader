"""Application configuration loaded from config.yaml and environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    contact_email: str = "you@example.com"
    unpaywall_email: str = ""
    semantic_scholar_api_key: str = ""
    core_api_key: str = ""
    springer_api_key: str = ""
    elsevier_api_key: str = ""
    ieee_api_key: str = ""
    ncbi_api_key: str = ""
    nasa_ads_token: str = ""
    max_concurrent_requests: int = 8
    max_concurrent_downloads: int = 3
    request_timeout_seconds: float = 15
    download_timeout_seconds: float = 120
    max_redirects: int = 5
    database_path: str = "data/research.db"
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "research_collector"
    settings_sqlite_path: str = "data/settings.db"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_admin_emails: str = ""
    session_secret: str = "change-me-in-production-please-use-a-long-random-string"
    admin_email: str = ""
    admin_password: str = ""
    admin_name: str = ""

    @property
    def polite_email(self) -> str:
        return (self.unpaywall_email or self.contact_email).strip()


class RankingConfig(BaseModel):
    title_weight: float = 0.35
    abstract_weight: float = 0.30
    keyword_weight: float = 0.15
    citation_weight: float = 0.10
    recency_weight: float = 0.10
    recency_horizon_years: int = 20
    citation_cap: int = 1000
    semantic_enabled: bool = False
    semantic_model: str = "all-MiniLM-L6-v2"


class DedupConfig(BaseModel):
    title_similarity_threshold: int = 92
    use_fuzzy_title: bool = True


class QueryExpansionConfig(BaseModel):
    enabled: bool = True
    max_expanded: int = 5
    synonyms: dict[str, list[str]] = Field(default_factory=dict)


class RetryConfig(BaseModel):
    max_attempts: int = 2
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.3


class ProviderConfig(BaseModel):
    enabled: bool = True
    requests_per_second: float = 5.0
    requests_per_second_with_key: float | None = None
    requires_key: bool = False


class TopicConfig(BaseModel):
    name: str
    query: str
    year_from: int | None = None
    year_to: int | None = None
    max_results: int | None = None
    open_access_only: bool = False


class AppConfig(BaseModel):
    name: str = "ResearchPaper Collector"
    version: str = "1.0.0"
    user_agent: str = "ResearchPaperCollector/1.0 (academic research; mailto:{email})"
    library_dir: Path = Path("research_library")
    exports_dir: Path = Path("exports")
    logs_dir: Path = Path("logs")
    fulltext_dir: Path = Path("data/fulltext")
    min_pdf_size_bytes: int = 2048
    max_file_size_bytes: int = 50 * 1024 * 1024
    download_limit: int = 100
    max_filename_length: int = 120
    prefer_https: bool = True
    check_robots_txt: bool = True
    show_paywalled: bool = True
    timezone: str = "UTC"
    default_max_results: int = 50
    default_sort: str = "relevance"
    provider_timeout_seconds: float = 12
    provider_phase_seconds: float = 16
    oa_concurrency: int = 8
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    query_expansion: QueryExpansionConfig = Field(default_factory=QueryExpansionConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    topics: list[TopicConfig] = Field(default_factory=list)
    env: EnvSettings = Field(default_factory=EnvSettings)

    @property
    def database_url(self) -> str:
        db_path = ROOT_DIR / self.env.database_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"

    def user_agent_header(self) -> str:
        return self.user_agent.format(email=self.env.polite_email)

    def resolve_path(self, path: Path | str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = ROOT_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


def parse_size(value: str | int | None, default: int = 50 * 1024 * 1024) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip().upper().replace(" ", "")
    multipliers = (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1))
    for suffix, mult in multipliers:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * mult)
    return int(text)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _provider_map(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    return {name: ProviderConfig(**(cfg or {})) for name, cfg in raw.items()}


@lru_cache(maxsize=1)
def load_config(config_path: str | Path | None = None) -> AppConfig:
    yaml_path = Path(config_path) if config_path else ROOT_DIR / "config.yaml"
    data = _load_yaml(yaml_path)
    env = EnvSettings()

    app = data.get("app") or {}
    search = data.get("search") or {}
    ranking_raw = data.get("ranking") or {}
    semantic = ranking_raw.pop("semantic", {}) or {}
    ranking = RankingConfig(
        **{k: v for k, v in ranking_raw.items() if k != "semantic"},
        semantic_enabled=bool(semantic.get("enabled", False)),
        semantic_model=str(semantic.get("model", "all-MiniLM-L6-v2")),
    )
    http = data.get("http") or {}
    retry = RetryConfig(**(http.get("retry") or {}))

    os.environ.setdefault("CONTACT_EMAIL", env.contact_email)

    try:
        from app.utils.time import normalize_timezone, set_active_timezone

        timezone_name = normalize_timezone(str(app.get("timezone") or "UTC"))
        set_active_timezone(timezone_name)
    except Exception:
        timezone_name = "UTC"

    return AppConfig(
        name=app.get("name", "ResearchPaper Collector"),
        version=app.get("version", "1.0.0"),
        user_agent=app.get("user_agent", AppConfig.model_fields["user_agent"].default),
        library_dir=Path(app.get("library_dir", "research_library")),
        exports_dir=Path(app.get("exports_dir", "exports")),
        logs_dir=Path(app.get("logs_dir", "logs")),
        fulltext_dir=Path(app.get("fulltext_dir", "data/fulltext")),
        min_pdf_size_bytes=int(app.get("min_pdf_size_bytes", 2048)),
        max_file_size_bytes=parse_size(app.get("max_file_size")),
        download_limit=int(app.get("download_limit", 100)),
        max_filename_length=int(app.get("max_filename_length", 120)),
        prefer_https=bool(app.get("prefer_https", True)),
        check_robots_txt=bool(app.get("check_robots_txt", True)),
        timezone=timezone_name,
        default_max_results=int(search.get("default_max_results", 50)),
        default_sort=str(search.get("default_sort", "relevance")),
        provider_timeout_seconds=float(search.get("provider_timeout_seconds", 12)),
        provider_phase_seconds=float(search.get("provider_phase_seconds", 16)),
        oa_concurrency=int(search.get("oa_concurrency", 8)),
        ranking=ranking,
        dedup=DedupConfig(**(data.get("dedup") or {})),
        query_expansion=QueryExpansionConfig(**(data.get("query_expansion") or {})),
        retry=retry,
        providers=_provider_map(data.get("providers") or {}),
        topics=[TopicConfig(**t) for t in (data.get("topics") or [])],
        env=env,
    )


def reload_config() -> AppConfig:
    load_config.cache_clear()
    return load_config()


def get_runtime_config() -> AppConfig:
    """YAML + env defaults, overlaid with MySQL-stored settings and academic sources."""
    cfg = load_config().model_copy(deep=True)
    try:
        from app.database.settings_repository import apply_runtime_overlay

        return apply_runtime_overlay(cfg)
    except Exception:
        return cfg
