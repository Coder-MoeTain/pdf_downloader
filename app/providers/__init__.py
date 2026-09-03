"""Provider registry. Additional academic sources can be registered here."""

from __future__ import annotations

from app.config import AppConfig, get_runtime_config, load_config
from app.providers.arxiv import ArxivProvider
from app.providers.base import ResearchProvider
from app.providers.core import CoreProvider
from app.providers.crossref import CrossrefProvider
from app.providers.europe_pmc import EuropePMCProvider
from app.providers.extra import (
    DoajProvider,
    ElsevierProvider,
    IeeeProvider,
    NasaAdsProvider,
    NasaNtrsProvider,
    SpringerProvider,
)
from app.providers.free import (
    BiorxivProvider,
    CiniiProvider,
    DataciteProvider,
    DblpProvider,
    DoabProvider,
    EconstorProvider,
    EricProvider,
    FatcatProvider,
    FigshareProvider,
    HalProvider,
    InspireProvider,
    MedrxivProvider,
    OapenProvider,
    OpenaireProvider,
    OsfProvider,
    OstiProvider,
    PlosProvider,
    ScieloProvider,
    WorldbankProvider,
    ZenodoProvider,
)
from app.providers.more import (
    CernProvider,
    ChemrxivProvider,
    DataverseProvider,
    EartharxivProvider,
    ElifeProvider,
    F1000ResearchProvider,
    FaoProvider,
    NberProvider,
    NdlProvider,
    OpenreviewProvider,
    PaperswithcodeProvider,
    PeerjProvider,
    ResearchSquareProvider,
    ScipostProvider,
    SsrnProvider,
    TechrxivProvider,
    UsgsProvider,
    WhoProvider,
    ZbmathProvider,
)
from app.providers.openalex import OpenAlexProvider
from app.providers.pubmed import PmcProvider, PubMedProvider
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
    PmcProvider,
    EuropePMCProvider,
    CoreProvider,
    DoajProvider,
    IeeeProvider,
    SpringerProvider,
    ElsevierProvider,
    NasaAdsProvider,
    NasaNtrsProvider,
    OpenaireProvider,
    HalProvider,
    ZenodoProvider,
    DblpProvider,
    PlosProvider,
    EricProvider,
    OstiProvider,
    DataciteProvider,
    OsfProvider,
    BiorxivProvider,
    MedrxivProvider,
    FigshareProvider,
    DoabProvider,
    ScieloProvider,
    CiniiProvider,
    InspireProvider,
    FatcatProvider,
    WorldbankProvider,
    OapenProvider,
    EconstorProvider,
    ChemrxivProvider,
    SsrnProvider,
    ResearchSquareProvider,
    TechrxivProvider,
    PeerjProvider,
    F1000ResearchProvider,
    NberProvider,
    EartharxivProvider,
    OpenreviewProvider,
    ElifeProvider,
    ScipostProvider,
    PaperswithcodeProvider,
    ZbmathProvider,
    UsgsProvider,
    DataverseProvider,
    FaoProvider,
    WhoProvider,
    CernProvider,
    NdlProvider,
]


def build_providers(client: AsyncHttpClient, config: AppConfig | None = None) -> list[ResearchProvider]:
    cfg = config or get_runtime_config()
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
    cfg = config or get_runtime_config()
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
