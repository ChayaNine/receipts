"""Check your setup in one command:  python scripts/doctor.py

Verifies both API keys, finds the exact Nemotron model IDs on your Token Factory account,
sends one tiny request to each model, and tries Tavily Search + Extract.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.llm import ROLE_FAMILY, ROLE_LABEL, TokenFactory  # noqa: E402
from app.tavily import Tavily  # noqa: E402

OK, FAIL, WARN = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m", "\033[33mWARN\033[0m"


async def main() -> int:
    problems = 0
    print("Receipts setup check\n")
    if settings.mock:
        print(f"{WARN}  RECEIPTS_MOCK=1 is set, so the app runs simulated. Remove it for real checks.\n")
    if not settings.nebius_api_key:
        print(f"{FAIL}  NEBIUS_API_KEY is missing. Create one at https://tokenfactory.nebius.com and put it in .env")
        problems += 1
    if not settings.tavily_api_key:
        print(f"{FAIL}  TAVILY_API_KEY is missing. Create one at https://app.tavily.com and put it in .env")
        problems += 1
    if problems:
        return 1

    llm = TokenFactory(settings)
    print(f"Token Factory base URL: {settings.nebius_base_url}")
    try:
        models = await llm.list_models()
        nemotron = sorted(m for m in models if "nemotron" in m.lower())
        print(f"{OK}  Key works. {len(models)} models available, {len(nemotron)} Nemotron:")
        for m in nemotron:
            print(f"      {m}")
    except httpx.HTTPStatusError as exc:
        print(f"{FAIL}  Listing models returned {exc.response.status_code}: {exc.response.text[:200]}")
        if exc.response.status_code in (401, 403):
            print("      The key was rejected. Copy it again from Token Factory > API keys.")
        print("      If your account is regional, try NEBIUS_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1")
        return 1
    except Exception as exc:
        print(f"{FAIL}  Could not reach Token Factory: {exc}")
        return 1

    resolved = await llm.resolve_models()
    print()
    for role, model in resolved.items():
        if model not in models:
            print(f"{FAIL}  {ROLE_LABEL[role]}: '{model}' is not on your account. Set MODEL_{ {'fast': 'FAST', 'judge': 'JUDGE', 'reason': 'REASON'}[role]} "
                  f"to one of the Nemotron IDs above containing '{ROLE_FAMILY[role]}'.")
            problems += 1
            continue
        started = time.perf_counter()
        try:
            reply = await llm.chat(role, [
                {"role": "system", "content": 'Reply with JSON only: {"ok": true, "model": "<your name>"}'},
                {"role": "user", "content": "Health check."},
            ], max_tokens=800)
            ms = (time.perf_counter() - started) * 1000
            print(f"{OK}  {ROLE_LABEL[role]:<17} {model}  ({ms:.0f} ms) -> {str(reply)[:60]}")
        except Exception as exc:
            print(f"{FAIL}  {ROLE_LABEL[role]:<17} {model}: {str(exc)[:240]}")
            problems += 1
    if llm._no_json_mode:
        print(f"      note: JSON mode was rejected by {sorted(llm._no_json_mode)}; Receipts falls back to plain JSON prompts automatically.")

    print()
    tavily = Tavily(settings)
    try:
        results = await tavily.search("Mata v. Avianca sanctions ChatGPT fake cases", max_results=3)
        print(f"{OK}  Tavily Search: {len(results)} results, e.g. {results[0]['url'] if results else '-'}")
    except Exception as exc:
        print(f"{FAIL}  Tavily Search: {exc}")
        problems += 1
    try:
        page = await tavily.extract("https://arxiv.org/abs/1706.03762", query="Transformer BLEU English-to-German")
        print(f"{OK}  Tavily Extract: {len(page['raw_content']) if page else 0} characters of relevant passages")
    except Exception as exc:
        print(f"{FAIL}  Tavily Extract: {exc}")
        problems += 1

    print("\nAll good. Start the app with:  uvicorn app.main:app --port 8000" if not problems else
          f"\n{problems} problem(s) found. Fix them and run this again.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
