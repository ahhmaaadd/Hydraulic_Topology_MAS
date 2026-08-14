from __future__ import annotations

import httpx
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from .config import Settings


def _use_aalto_responses_endpoint(request: httpx.Request) -> None:
    """Route OpenAI Responses calls through Aalto's API Management path."""
    request.url = request.url.copy_with(path="/v1/openai/responses")


def _aalto_headers(settings: Settings) -> dict[str, str]:
    headers = dict(settings.default_headers)
    if settings.openai_api_key:
        headers.setdefault("Ocp-Apim-Subscription-Key", settings.openai_api_key)
    return headers


def build_chat_model(settings: Settings, *, fast: bool = False):
    """Build the requested design or fast model for the configured API mode."""
    model_name = settings.fast_model if fast else settings.model
    common = {
        "max_retries": 3,
        "timeout": 180,
    }
    if settings.uses_azure:
        return AzureChatOpenAI(
            azure_deployment=model_name,
            azure_endpoint=settings.azure_endpoint,
            api_key=settings.azure_api_key,
            api_version=settings.azure_api_version,
            **common,
        )
    if settings.uses_aalto_gateway:
        api_base = (settings.openai_base_url or "https://aalto-openai-apigw.azure-api.net").rstrip("/")
        headers = _aalto_headers(settings)
        if fast:
            return ChatOpenAI(
                model=model_name,
                api_key=settings.openai_api_key,
                base_url=f"{api_base}/v1/openai/deployments/{model_name}",
                default_headers=headers,
                http_client=httpx.Client(),
                **common,
            )
        responses_http_client = httpx.Client(
            event_hooks={"request": [_use_aalto_responses_endpoint]},
        )
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            base_url=api_base,
            default_headers=headers,
            http_client=responses_http_client,
            use_responses_api=True,
            reasoning={"effort": "medium", "summary": "auto"},
            **common,
        )
    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        default_headers=settings.default_headers or None,
        **common,
    )

