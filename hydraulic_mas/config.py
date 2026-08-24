from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    model: str
    fast_model: str
    openai_api_key: str | None
    openai_base_url: str | None
    default_headers: dict[str, str]
    azure_api_key: str | None
    azure_endpoint: str | None
    azure_api_version: str
    tavily_api_key: str | None
    openai_api_mode: str = "aalto"
    max_research_rounds: int = 3
    max_searches: int = 10
    max_results_per_search: int = 8
    max_documents_per_search: int = 3
    max_document_chars: int = 12000
    max_topology_rounds: int = 4
    max_requirements_rounds: int = 2
    enable_sizing: bool = True
    max_sizing_rounds: int = 3

    @property
    def uses_azure(self) -> bool:
        return bool(self.azure_endpoint)

    @property
    def uses_aalto_gateway(self) -> bool:
        return not self.uses_azure and self.openai_api_mode == "aalto"

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        load_dotenv()
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        configured_base_url = os.getenv("OPENAI_BASE_URL")
        configured_mode = os.getenv("OPENAI_API_MODE")
        if configured_mode:
            api_mode = configured_mode.strip().lower()
        elif azure_endpoint:
            api_mode = "azure"
        elif configured_base_url and "aalto-openai-apigw.azure-api.net" not in configured_base_url.lower():
            api_mode = "compatible"
        else:
            api_mode = "aalto"

        openai_base_url = configured_base_url
        if api_mode == "aalto" and not openai_base_url:
            openai_base_url = "https://aalto-openai-apigw.azure-api.net"

        raw_headers = os.getenv("OPENAI_DEFAULT_HEADERS_JSON", "{}")
        try:
            headers = json.loads(raw_headers)
        except json.JSONDecodeError as exc:
            raise ValueError("OPENAI_DEFAULT_HEADERS_JSON must be valid JSON") from exc
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("OPENAI_DEFAULT_HEADERS_JSON must be a JSON object of string values")

        settings = cls(
            model=os.getenv("HYDRAULIC_MODEL", "gpt-5.5-2026-04-24"),
            fast_model=os.getenv("HYDRAULIC_FAST_MODEL", "gpt-4o-2024-11-20"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=openai_base_url,
            default_headers=headers,
            azure_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=azure_endpoint,
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            tavily_api_key=os.getenv("TAVILY_API_KEY"),
            openai_api_mode=api_mode,
            max_research_rounds=int(os.getenv("MAX_RESEARCH_ROUNDS", "3")),
            max_searches=int(os.getenv("MAX_SEARCHES", "10")),
            max_results_per_search=int(os.getenv("MAX_RESULTS_PER_SEARCH", "8")),
            max_documents_per_search=int(os.getenv("MAX_DOCUMENTS_PER_SEARCH", "3")),
            max_document_chars=int(os.getenv("MAX_DOCUMENT_CHARS", "12000")),
            max_topology_rounds=int(os.getenv("MAX_TOPOLOGY_ROUNDS", "4")),
            max_requirements_rounds=int(os.getenv("MAX_REQUIREMENTS_ROUNDS", "2")),
            enable_sizing=os.getenv("ENABLE_SIZING", "1") not in {"0", "false", "False"},
            max_sizing_rounds=int(os.getenv("MAX_SIZING_ROUNDS", "3")),
        )
        clean = {key: value for key, value in overrides.items() if value is not None}
        return replace(settings, **clean)

    def validate_live_run(self) -> None:
        if self.openai_api_mode not in {"aalto", "compatible", "azure"}:
            raise ValueError("OPENAI_API_MODE must be one of: aalto, compatible, azure")
        if self.uses_azure:
            if not self.azure_api_key:
                raise ValueError("AZURE_OPENAI_API_KEY is required when AZURE_OPENAI_ENDPOINT is set")
        elif not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
        if not self.tavily_api_key:
            raise ValueError("TAVILY_API_KEY is required for the adaptive research loop")
        if self.max_research_rounds < 1 or self.max_searches < 1:
            raise ValueError("Research limits must be positive")
        if self.max_topology_rounds < 1:
            raise ValueError("max_topology_rounds must be positive")
