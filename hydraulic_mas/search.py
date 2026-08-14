from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from tavily import TavilyClient


@dataclass
class SearchBatch:
    results: list[dict[str, Any]]
    candidate_count: int = 0
    duplicate_count: int = 0
    extracted_count: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)


class SearchClient(Protocol):
    def search(self, query: str, *, max_results: int) -> SearchBatch | list[dict[str, Any]]: ...


_WEAK_DOMAINS = {
    "chegg.com",
    "facebook.com",
    "quizlet.com",
    "scribd.com",
    "youtube.com",
}
_STANDARD_DOMAINS = {"iso.org", "ansi.org", "din.de", "afnor.org"}
_TEXTBOOK_DOMAINS = {"eng.libretexts.org"}
_TECHNICAL_DOMAINS = {
    "fluidpowerjournal.com",
    "fluidpowerworld.com",
    "mobilehydraulictips.com",
    "powermotiontech.com",
}
_MANUFACTURER_HINTS = {
    "bosch",
    "danfoss",
    "eaton",
    "festo",
    "hawe",
    "hydac",
    "parker",
    "pilz",
    "rexroth",
    "sunhydraulics",
}


def normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


def queries_are_similar(first: str, second: str, *, threshold: float = 0.78) -> bool:
    stopwords = {"and", "for", "from", "into", "the", "with"}

    def tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", normalize_query(value))
            if len(token) > 2 and token not in stopwords
        }

    left, right = tokens(first), tokens(second)
    if not left or not right:
        return normalize_query(first) == normalize_query(second)
    return len(left & right) / len(left | right) >= threshold


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), host, path, parts.query, ""))


def classify_source(url: str, title: str | None = None) -> tuple[str, float]:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    title_text = (title or "").casefold()
    if any(host == domain or host.endswith(f".{domain}") for domain in _WEAK_DOMAINS):
        return "other", -0.8
    if any(host == domain or host.endswith(f".{domain}") for domain in _STANDARD_DOMAINS):
        return "standard", 1.0
    if host.endswith(".edu") or host.endswith(".ac.uk") or host in _TEXTBOOK_DOMAINS:
        return "textbook", 0.85
    if any(hint in host for hint in _MANUFACTURER_HINTS):
        return "manufacturer", 0.9
    if host in _TECHNICAL_DOMAINS:
        return "technical", 0.7
    if "paper" in title_text or "journal" in title_text:
        return "paper", 0.65
    if any(word in title_text for word in ("manual", "handbook", "lecture", "textbook")):
        return "textbook", 0.65
    if urlsplit(url).path.casefold().endswith(".pdf"):
        return "technical", 0.55
    return "other", 0.2


class TavilySearchClient:
    def __init__(
        self,
        api_key: str,
        *,
        max_documents_per_search: int = 3,
        max_document_chars: int = 12_000,
    ):
        self.client = TavilyClient(api_key=api_key)
        self.max_documents_per_search = max_documents_per_search
        self.max_document_chars = max_document_chars
        self._seen_urls: set[str] = set()
        self._document_cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def begin_run(self) -> None:
        with self._lock:
            self._seen_urls.clear()

    @staticmethod
    def _candidate(result: dict[str, Any]) -> dict[str, Any] | None:
        url = str(result.get("url") or "").strip()
        if not url:
            return None
        title = str(result.get("title") or "").strip() or None
        source_kind, source_score = classify_source(url, title)
        tavily_score = float(result.get("score") or 0.0)
        title_bonus = 0.15 if any(
            word in (title or "").casefold()
            for word in ("application note", "circuit", "diagram", "manual", "schematic")
        ) else 0.0
        return {
            "title": title,
            "url": url,
            "normalized_url": normalize_url(url),
            "snippet": str(result.get("content") or "")[:2500],
            "score": result.get("score"),
            "source_kind": source_kind,
            "quality_score": round(source_score + 0.3 * tavily_score + title_bonus, 4),
        }

    def _reserve_candidates(
        self, candidates: list[dict[str, Any]], limit: int
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        duplicates = 0
        with self._lock:
            for candidate in sorted(candidates, key=lambda item: item["quality_score"], reverse=True):
                normalized = candidate["normalized_url"]
                if candidate["quality_score"] < 0:
                    rejected.append({"url": candidate["url"], "reason": "low_quality_source"})
                    continue
                if normalized in self._seen_urls:
                    duplicates += 1
                    rejected.append({"url": candidate["url"], "reason": "duplicate_url"})
                    continue
                if len(selected) >= limit:
                    rejected.append({"url": candidate["url"], "reason": "lower_rank"})
                    continue
                self._seen_urls.add(normalized)
                selected.append(candidate)
        return selected, duplicates, rejected

    def _extract_selected(self, selected: list[dict[str, Any]], query: str) -> int:
        missing: list[dict[str, Any]] = []
        with self._lock:
            for item in selected:
                cached = self._document_cache.get(item["normalized_url"])
                if cached:
                    item["raw_content"] = cached
                else:
                    missing.append(item)
        if not missing:
            return len(selected)
        try:
            response = self.client.extract(
                [item["url"] for item in missing],
                extract_depth="advanced",
                format="text",
                query=query,
                chunks_per_source=5,
                timeout=45,
            )
        except Exception:
            return len(selected) - len(missing)
        extracted_by_url = {
            normalize_url(str(item.get("url") or "")): str(item.get("raw_content") or item.get("content") or "")
            for item in response.get("results", [])
        }
        extracted_count = len(selected) - len(missing)
        with self._lock:
            for item in missing:
                content = extracted_by_url.get(item["normalized_url"], "")
                if content:
                    content = content[: self.max_document_chars]
                    self._document_cache[item["normalized_url"]] = content
                    item["raw_content"] = content
                    extracted_count += 1
        return extracted_count

    @staticmethod
    def _result(candidate: dict[str, Any]) -> dict[str, Any]:
        raw_content = str(candidate.get("raw_content") or "")
        content = raw_content or candidate["snippet"]
        return {
            "title": candidate["title"],
            "url": candidate["url"],
            "content": content,
            "score": candidate["score"],
            "source_kind": candidate["source_kind"],
            "quality_score": candidate["quality_score"],
            "is_full_content": bool(raw_content),
            "selection_reason": "highest-quality unique source",
        }

    def search(self, query: str, *, max_results: int = 6) -> SearchBatch:
        response = self.client.search(
            query=query,
            max_results=max(max_results * 2, 8),
            search_depth="advanced",
            include_answer=False,
            include_raw_content=False,
        )
        candidates = [candidate for raw in response.get("results", []) if (candidate := self._candidate(raw))]
        selected, duplicates, rejected = self._reserve_candidates(
            candidates,
            min(self.max_documents_per_search, max_results),
        )
        extracted = self._extract_selected(selected, query)
        return SearchBatch(
            results=[self._result(item) for item in selected],
            candidate_count=len(candidates),
            duplicate_count=duplicates,
            extracted_count=extracted,
            rejected=rejected,
        )

    def extract(self, urls: list[str], *, query: str, max_results: int = 3) -> SearchBatch:
        candidates: list[dict[str, Any]] = []
        for url in urls[:max_results]:
            source_kind, source_score = classify_source(url)
            candidates.append(
                {
                    "title": None,
                    "url": url,
                    "normalized_url": normalize_url(url),
                    "snippet": "",
                    "score": None,
                    "source_kind": source_kind,
                    "quality_score": source_score,
                }
            )
        extracted = self._extract_selected(candidates, query)
        return SearchBatch(
            results=[self._result(item) for item in candidates if item.get("raw_content")],
            candidate_count=len(candidates),
            extracted_count=extracted,
        )
