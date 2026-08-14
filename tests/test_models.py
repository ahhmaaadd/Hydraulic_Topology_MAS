from __future__ import annotations

import httpx

from hydraulic_mas.config import Settings
from hydraulic_mas import models


def _settings(**overrides: object) -> Settings:
    values = {
        "model": "gpt-5.5-2026-04-24",
        "fast_model": "gpt-4o-2024-11-20",
        "openai_api_key": "test-key",
        "openai_base_url": "https://aalto-openai-apigw.azure-api.net",
        "default_headers": {},
        "azure_api_key": None,
        "azure_endpoint": None,
        "azure_api_version": "test",
        "tavily_api_key": "test-tavily",
        "openai_api_mode": "aalto",
    }
    values.update(overrides)
    return Settings(**values)


def test_aalto_design_model_uses_responses_api(monkeypatch) -> None:
    monkeypatch.setattr(models, "ChatOpenAI", lambda **kwargs: kwargs)

    result = models.build_chat_model(_settings())

    assert result["base_url"] == "https://aalto-openai-apigw.azure-api.net"
    assert result["model"] == "gpt-5.5-2026-04-24"
    assert result["use_responses_api"] is True
    assert result["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert result["default_headers"]["Ocp-Apim-Subscription-Key"] == "test-key"
    request = httpx.Request("POST", "https://example.test/responses")
    result["http_client"]._event_hooks["request"][0](request)
    assert request.url.path == "/v1/openai/responses"
    result["http_client"].close()


def test_aalto_fast_model_uses_deployment_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(models, "ChatOpenAI", lambda **kwargs: kwargs)

    result = models.build_chat_model(_settings(), fast=True)

    assert result["base_url"].endswith("/v1/openai/deployments/gpt-4o-2024-11-20")
    assert "use_responses_api" not in result
    result["http_client"].close()
