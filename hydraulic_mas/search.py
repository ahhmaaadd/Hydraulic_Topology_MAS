from __future__ import annotations

from typing import Any, Protocol

from tavily import TavilyClient


class SearchClient(Protocol):
    def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]: ...


class TavilySearchClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, *, max_results: int = 6) -> list[dict[str, Any]]:
        response = TavilyClient(api_key=self.api_key).search(
            query=query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
            include_raw_content=False,
        )
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in response.get("results", []):
            url = str(result.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append(
                {
                    "title": result.get("title"),
                    "url": url,
                    "content": str(result.get("content") or "")[:2500],
                    "score": result.get("score"),
                }
            )
        return normalized


def normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


