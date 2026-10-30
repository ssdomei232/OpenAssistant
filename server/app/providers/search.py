"""Search providers: self-hosted style web search and LLM provider search."""
from __future__ import annotations

import html
import re
import time
import httpx

from .llm import OpenAICompatibleLLM
from .types import SearchResponse, SearchResult


class SearchUnavailable(RuntimeError):
    pass


class BuiltinSearchProvider:
    """Server-side search implemented directly by OpenAssistant.

    Fetches the public DuckDuckGo HTML endpoint (no API key) and parses the
    result list; the answer is the concatenated snippets.  Runs fully on the
    server, so the App's ``builtin`` option never needs a third-party SDK.
    """

    name = "builtin"

    def __init__(self, endpoint: str, timeout_s: float = 15.0, max_results: int = 5) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.max_results = max_results

    def search(self, query: str, limit: int = 5, context: str = "") -> SearchResponse:
        started = time.perf_counter()
        limit = max(1, min(limit or self.max_results, 10))
        results: list[SearchResult] = []
        error: str | None = None
        try:
            with httpx.Client(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; OpenAssistant/1.0)"},
            ) as client:
                response = client.get(self.endpoint, params={"q": query})
                response.raise_for_status()
                results = _parse_ddg_html(response.text, limit)
        except Exception as exc:  # network problems must not break the command flow
            error = f"search_failed: {type(exc).__name__}"

        if results:
            answer = "；".join(r.snippet for r in results if r.snippet)[:600] or results[0].title
        else:
            answer = "未能获取搜索结果。"
            if error:
                answer += "（" + error + "）"
        return SearchResponse(
            query=query,
            provider=self.name,
            answer=answer,
            results=results,
            took_ms=int((time.perf_counter() - started) * 1000),
        )


class DeepSeekSearchProvider:
    """Use the AI provider's own query / web capability (DeepSeek chat).

    The model is asked to look the answer up and cite URLs; when the provider
    has no browsing tool available it still returns knowledge based answers,
    which is exactly the trade-off the App exposes to the user.
    """

    name = "deepseek"

    def __init__(self, llm: OpenAICompatibleLLM, timeout_s: float = 45.0) -> None:
        self.llm = llm
        self.timeout_s = timeout_s

    def search(self, query: str, limit: int = 5, context: str = "") -> SearchResponse:
        started = time.perf_counter()
        if not self.llm.available:
            raise SearchUnavailable("deepseek_not_configured")
        system = (
            "你是联网查询助手。请基于你的知识（如具备联网工具则优先使用）回答用户查询，"
            "输出 2-5 句中文结论，并在末尾用「来源：」列出可核实的链接（没有链接则省略）。"
        )
        if context:
            system += "\n\n" + context
        try:
            answer = self.llm.complete(system, query, temperature=0.2, max_tokens=700)
        except Exception as exc:
            raise SearchUnavailable(f"deepseek_search_failed: {type(exc).__name__}") from exc
        urls = re.findall(r"https?://[^\s，。；）)]+", answer or "")
        results = [SearchResult(title=u, url=u, snippet="") for u in urls[:limit]]
        return SearchResponse(
            query=query,
            provider=self.name,
            answer=(answer or "").strip(),
            results=results,
            took_ms=int((time.perf_counter() - started) * 1000),
        )


def _parse_ddg_html(page: str, limit: int) -> list[SearchResult]:
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?(?:<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>'
        r'|<td[^>]+class="result__snippet"[^>]*>(?P<snippet2>.*?)</td>)?',
        re.S,
    )
    results: list[SearchResult] = []
    for match in pattern.finditer(page):
        href = html.unescape(match.group("href") or "")
        title = _strip_tags(match.group("title") or "")
        snippet = _strip_tags(match.group("snippet") or match.group("snippet2") or "")
        if "uddg=" in href:
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        if not href.startswith("http"):
            continue
        results.append(SearchResult(title=title, url=href, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


class StaticSearchProvider:
    """Deterministic search provider used by tests and offline demos."""

    name = "static"

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer

    def search(self, query: str, limit: int = 5, context: str = "") -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=self.name,
            answer=self.answer or f"关于“{query}”的离线示例结果。",
            results=[
                SearchResult(title=f"{query} - 示例结果 1", url="https://example.com/1", snippet="示例摘要 1"),
                SearchResult(title=f"{query} - 示例结果 2", url="https://example.com/2", snippet="示例摘要 2"),
            ][: max(1, limit)],
        )
