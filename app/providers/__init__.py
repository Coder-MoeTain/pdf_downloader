"""Provider registry. Additional academic sources can be registered here."""

from __future__ import annotations

from app.config import AppConfig, load_config
from app.providers.arxiv import ArxivProvider
from app.providers.base import ResearchProvider
from app.providers.core import CoreProvider
from app.providers.crossref import CrossrefProvider
from app.providers.europe_pmc import EuropePMCProvider
from app.providers.extra import DoajProvider, ElsevierProvider, IeeeProvider, NasaAdsProvider, SpringerProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.pubmed import PubMedProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.utils.http import AsyncHttpClient
from app.utils.logger import get_logger

logger = get_logger("app.providers")

PROVIDER_CLASSES: list[type[ResearchProvider]] = [
    OpenAlexProvider,
    CrossrefProvider,
    SemanticScholarProvider,
    ArxivProvider,
    PubMedProvider,
    EuropePMCProvider,
    CoreProvider,
    DoajProvider,
    IeeeProvider,
    SpringerProvider,
    ElsevierProvider,
    NasaAdsProvider,
]


def build_providers(client: AsyncHttpClient, config: AppConfig | None = None) -> list[ResearchProvider]:
    cfg = config or load_config()
    providers: list[ResearchProvider] = []
    for cls in PROVIDER_CLASSES:
        pcfg = cfg.providers.get(cls.name)
        provider = cls(client, cfg, pcfg)
        if not provider.is_available():
            logger.info("Skipping provider %s (disabled or missing API key)", cls.name)
            continue
        providers.append(provider)
    return providers


def provider_status(config: AppConfig | None = None) -> list[dict[str, object]]:
    cfg = config or load_config()
    rows = []
    dummy = None
    for cls in PROVIDER_CLASSES:
        pcfg = cfg.providers.get(cls.name)
        provider = cls(dummy, cfg, pcfg)
        rows.append(
            {
                "name": provider.name,
                "display_name": provider.display_name,
                "enabled": provider.provider_config.enabled,
                "requires_key": provider.provider_config.requires_key,
                "has_key": provider.has_api_key(),
                "available": provider.is_available(),
                "requests_per_second": provider.requests_per_second,
            }
        )
    return rows
