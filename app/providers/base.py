"""Abstract research provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import AppConfig, ProviderConfig, load_config
from app.models.paper import PaperRecord
from app.models.search import SearchFilters
from app.utils.http import AsyncHttpClient


class ResearchProvider(ABC):
    name: str = "base"
    display_name: str = "Base"

    def __init__(
        self,
        client: AsyncHttpClient | None = None,
        config: AppConfig | None = None,
        provider_config: ProviderConfig | None = None,
    ) -> None:
        self.client = client
        self.config = config or load_config()
        self.provider_config = provider_config or self.config.providers.get(self.name, ProviderConfig())

    @property
    def requests_per_second(self) -> float:
        cfg = self.provider_config
        if self.has_api_key() and cfg.requests_per_second_with_key:
            return cfg.requests_per_second_with_key
        return cfg.requests_per_second

    def has_api_key(self) -> bool:
        return False

    def is_available(self) -> bool:
        if not self.provider_config.enabled:
            return False
        if self.provider_config.requires_key and not self.has_api_key():
            return False
        return True

    async def request_json(self, url: str, **kwargs: object):
        return await self.client.get_json(
            url,
            provider=self.name,
            requests_per_second=self.requests_per_second,
            **kwargs,
        )

    async def request_text(self, url: str, **kwargs: object) -> str:
        return await self.client.get_text(
            url,
            provider=self.name,
            requests_per_second=self.requests_per_second,
            **kwargs,
        )

    @abstractmethod
    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        raise NotImplementedError

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        return None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        return paper.pdf_url
