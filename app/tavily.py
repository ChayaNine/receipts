"""Tavily client: Search finds a cited source, Extract reads it (reranked to the claim being checked)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import Settings
from .llm import Emit


class TavilyError(RuntimeError):
    pass


class Tavily:
    BASE = "https://api.tavily.com"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        self.credits = 0.0

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.tavily_api_key}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict) -> dict:
        for attempt in range(3):
            try:
                resp = await self.client.post(f"{self.BASE}{path}", headers=self.headers, json=body)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise TavilyError(f"Tavily unreachable: {exc}") from exc
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 429 and attempt < 2:
                await asyncio.sleep(float(resp.headers.get("retry-after", 2) or 2))
                continue
            if resp.status_code >= 400:
                raise TavilyError(f"Tavily {path} error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            self.credits += float((data.get("usage") or {}).get("credits", 0) or 0)
            return data
        raise TavilyError(f"Tavily {path} failed")

    async def search(
        self,
        query: str,
        *,
        depth: str = "advanced",
        max_results: int = 6,
        include_domains: list[str] | None = None,
        topic: str = "general",
        emit: Emit | None = None,
        claim_id: str | None = None,
    ) -> list[dict]:
        body: dict[str, Any] = {
            "query": query[:400],
            "search_depth": depth,
            "max_results": max_results,
            "chunks_per_source": 3,
            "topic": topic,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": True,
        }
        if include_domains:
            body["include_domains"] = include_domains
        started = time.perf_counter()
        data = await self._post("/search", body)
        results = data.get("results") or []
        if emit:
            await emit({
                "type": "trace", "actor": "tavily", "label": "Tavily Search", "action": f"search: {query[:90]}",
                "claim_id": claim_id, "ms": round((time.perf_counter() - started) * 1000),
                "results": len(results), "credits": (data.get("usage") or {}).get("credits"),
            })
        return results

    async def extract(
        self,
        url: str,
        *,
        query: str | None = None,
        chunks_per_source: int = 5,
        depth: str = "basic",
        emit: Emit | None = None,
        claim_id: str | None = None,
    ) -> dict | None:
        """Return {"url", "raw_content"} for one URL, or None if Tavily could not read it.

        With `query`, Tavily reranks the page and returns only the chunks most relevant
        to the claim, which keeps the judge's context small and on-topic.
        """
        body: dict[str, Any] = {"urls": [url], "extract_depth": depth, "format": "markdown", "include_usage": True}
        if query:
            body["query"] = query[:400]
            body["chunks_per_source"] = chunks_per_source
        started = time.perf_counter()
        data = await self._post("/extract", body)
        results = data.get("results") or []
        if emit:
            await emit({
                "type": "trace", "actor": "tavily", "label": "Tavily Extract",
                "action": ("extract (reranked to claim): " if query else "extract: ") + url[:90],
                "claim_id": claim_id, "ms": round((time.perf_counter() - started) * 1000),
                "ok": bool(results), "credits": (data.get("usage") or {}).get("credits"),
            })
        if not results or not (results[0].get("raw_content") or "").strip():
            return None
        return results[0]
