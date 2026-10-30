"""Nebius Token Factory client (OpenAI-compatible chat completions over plain HTTP).

Three NVIDIA Nemotron models, three jobs:
  fast   -> Nemotron 3 Nano   : pull every cited claim out of the document, match search hits
  judge  -> Nemotron 3 Super  : decide whether the source supports each claim
  reason -> Nemotron 3 Ultra  : second opinion on risky / low-confidence verdicts, final report
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

import httpx

from .config import DEFAULT_PRICES, Settings

Emit = Callable[[dict], Awaitable[None]]

ROLE_LABEL = {"fast": "Nemotron 3 Nano", "judge": "Nemotron 3 Super", "reason": "Nemotron 3 Ultra"}
ROLE_FAMILY = {"fast": "nemotron-3-nano", "judge": "nemotron-3-super", "reason": "nemotron-3-ultra"}


class LLMError(RuntimeError):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    # A reasoning model may emit an unterminated <think> block followed by an answer.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    elif text.lstrip().lower().startswith("<think>"):
        return ""  # reasoning was cut off before any answer
    return text.strip()


def parse_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply (tolerates fences, prose, trailing commas)."""
    text = strip_thinking(text)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
    raise ValueError("unterminated JSON in model output")


class Usage:
    def __init__(self) -> None:
        self.by_role: dict[str, dict[str, float]] = {}

    def add(self, role: str, tokens_in: int, tokens_out: int, ms: float) -> None:
        r = self.by_role.setdefault(role, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "ms": 0.0, "usd": 0.0})
        price_in, price_out = DEFAULT_PRICES.get(role, (0.0, 0.0))
        r["calls"] += 1
        r["tokens_in"] += tokens_in
        r["tokens_out"] += tokens_out
        r["ms"] += ms
        r["usd"] += tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out

    def summary(self) -> dict:
        total = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0}
        for r in self.by_role.values():
            for k in total:
                total[k] += r[k]
        return {"by_role": self.by_role, "total": total}


class TokenFactory:
    """Async client for Nebius Token Factory with model auto-resolution and robust JSON replies."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=15.0))
        self._resolved: dict[str, str] | None = None
        self._resolve_lock = asyncio.Lock()
        self._no_json_mode: set[str] = set()
        self._no_template_kwargs: set[str] = set()

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.nebius_api_key}", "Content-Type": "application/json"}

    async def list_models(self) -> list[str]:
        resp = await self.client.get(f"{self.s.nebius_base_url}/models", headers=self.headers)
        resp.raise_for_status()
        return [m.get("id", "") for m in resp.json().get("data", [])]

    async def resolve_models(self) -> dict[str, str]:
        """Map each role to a model ID that actually exists on this account.

        Model IDs are written with different casing across Nebius pages, so we match the
        configured ID case-insensitively and fall back to the model family name.
        """
        async with self._resolve_lock:
            if self._resolved is not None:
                return self._resolved
            resolved = {role: self.s.model_for(role) for role in ROLE_FAMILY}
            try:
                available = await self.list_models()
            except Exception:
                available = []
            if available:
                lower = {m.lower(): m for m in available}
                for role, wanted in resolved.items():
                    if wanted in available:
                        continue
                    if wanted.lower() in lower:
                        resolved[role] = lower[wanted.lower()]
                        continue
                    family = [m for m in available if ROLE_FAMILY[role] in m.lower()]
                    if family:
                        resolved[role] = sorted(family, key=len)[0]
            self._resolved = resolved
            return resolved

    async def chat(
        self,
        role: str,
        messages: list[dict],
        *,
        json_mode: bool = True,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        emit: Emit | None = None,
        action: str = "",
        claim_id: str | None = None,
        usage: Usage | None = None,
    ) -> Any:
        models = await self.resolve_models()
        model = models[role]
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode and model not in self._no_json_mode:
            body["response_format"] = {"type": "json_object"}
        if role == "fast" and not self.s.fast_thinking and model not in self._no_template_kwargs:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        started = time.perf_counter()
        data = await self._post_with_fallbacks(body, model)
        ms = (time.perf_counter() - started) * 1000

        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if not strip_thinking(content) and choice.get("finish_reason") == "length" and max_tokens < 16000:
            # Reasoning used the whole budget before the answer: retry once with more room.
            body["max_tokens"] = min(16000, max_tokens * 2)
            data = await self._post_with_fallbacks(body, model)
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            ms = (time.perf_counter() - started) * 1000
        u = data.get("usage") or {}
        tokens_in, tokens_out = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        if usage is not None:
            usage.add(role, tokens_in, tokens_out, ms)
        if emit:
            await emit({
                "type": "trace", "actor": role, "label": ROLE_LABEL[role], "model": model,
                "action": action, "claim_id": claim_id, "ms": round(ms),
                "tokens_in": tokens_in, "tokens_out": tokens_out,
            })
        if not json_mode:
            return strip_thinking(content)
        try:
            return parse_json(content)
        except ValueError:
            # One repair attempt: ask the same model to re-emit valid JSON only.
            repair = messages + [
                {"role": "assistant", "content": content[-6000:]},
                {"role": "user", "content": "Your reply was not valid JSON. Reply again with ONLY the JSON object, no prose."},
            ]
            body["messages"] = repair
            data = await self._post_with_fallbacks(body, model)
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return parse_json(content)

    async def _post_with_fallbacks(self, body: dict, model: str) -> dict:
        url = f"{self.s.nebius_base_url}/chat/completions"
        for attempt in range(4):
            try:
                resp = await self.client.post(url, headers=self.headers, json=body)
            except httpx.TransportError as exc:
                if attempt == 3:
                    raise LLMError(f"Token Factory unreachable: {exc}") from exc
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (400, 422):
                text = resp.text.lower()
                # Some models reject optional params; drop them and retry instead of failing.
                if "response_format" in body and ("response_format" in text or "json" in text):
                    self._no_json_mode.add(model)
                    body.pop("response_format", None)
                    continue
                if "chat_template_kwargs" in body:
                    self._no_template_kwargs.add(model)
                    body.pop("chat_template_kwargs", None)
                    continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                retry_after = float(resp.headers.get("retry-after", 0) or 0)
                await asyncio.sleep(max(retry_after, 1.5 * (attempt + 1)))
                continue
            if resp.status_code >= 400:
                raise LLMError(f"Token Factory error {resp.status_code} for {model}: {resp.text[:300]}")
            return resp.json()
        raise LLMError(f"Token Factory request failed for {model}")
