"""Optional local/remote OpenAI-compatible LLM. Core features work with this disabled."""

from __future__ import annotations

import httpx

from app.config import get_runtime_config
from app.utils.logger import get_logger

logger = get_logger("app.research_llm")

FORBIDDEN = ("password", "session", "api_key", "secret", "token", "credential")


class ResearchLLMProvider:
    def enabled(self, *, allow_remote: bool = False) -> bool:
        cfg = get_runtime_config().env
        if not cfg.llm_enabled:
            return False
        provider = (cfg.llm_provider or "").strip().lower()
        if provider in {"openai", "remote"} and not allow_remote:
            return False
        return bool((cfg.llm_base_url or "").strip() or provider in {"ollama", "lmstudio", "openai"})

    def complete(self, prompt: str, *, allow_remote: bool = False, max_tokens: int = 700) -> str | None:
        if not self.enabled(allow_remote=allow_remote):
            return None
        lowered = prompt.lower()
        if any(word in lowered for word in FORBIDDEN) and "evidence" not in lowered:
            logger.info("Refusing LLM prompt that looks like it may leak secrets")
            return None
        cfg = get_runtime_config().env
        base = (cfg.llm_base_url or "").rstrip("/")
        model = cfg.llm_model or "llama3.2"
        if not base:
            if (cfg.llm_provider or "").lower() == "ollama":
                base = "http://127.0.0.1:11434"
            elif (cfg.llm_provider or "").lower() == "lmstudio":
                base = "http://127.0.0.1:1234"
            else:
                return None
        url = base + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {cfg.llm_api_key}"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. Only use the provided evidence. "
                        "Never invent citations, DOIs, metrics, datasets, or quotations. "
                        "If evidence is insufficient, say so."
                    ),
                },
                {"role": "user", "content": prompt[:12000]},
            ],
        }
        try:
            with httpx.Client(timeout=45.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            logger.warning("LLM request failed: %s", type(exc).__name__)
            return None


llm_provider = ResearchLLMProvider()
