"""Run the three sample documents LIVE once and save each run to data/cache/.

The hosted demo then replays these real runs instantly (clearly labelled "Replay of a live run"),
so judges always see results even if API credits run out. Commit the files in data/cache/.

    python scripts/record_samples.py            # record samples that have no recording yet
    python scripts/record_samples.py --force    # re-record all three
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.documents import clean_text  # noqa: E402
from app.llm import TokenFactory  # noqa: E402
from app.main import _cache_key, _cache_path  # noqa: E402
from app.pipeline import CheckRun  # noqa: E402
from app.samples import SAMPLES  # noqa: E402
from app.tavily import Tavily  # noqa: E402


async def record(sample: dict, llm: TokenFactory) -> None:
    text = clean_text(sample["text"])
    path = _cache_path(_cache_key(text))
    if path.exists() and "--force" not in sys.argv:
        print(f"skip  {sample['id']} (already recorded: {path.name})")
        return
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)
        if event["type"] == "claim_result":
            print(f"      {event['id']}: {event['verdict']} ({event['confidence']:.2f}) {event.get('explanation', '')[:90]}")

    print(f"run   {sample['id']} ...")
    await CheckRun(settings, llm, Tavily(settings, client=llm.client), emit).execute(text, title=sample["title"])
    if not any(e["type"] == "done" for e in events):
        print(f"FAIL  {sample['id']}: run did not finish; nothing saved")
        return
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                "events": events}, ensure_ascii=False), encoding="utf-8")
    verdicts = Counter(e["verdict"] for e in events if e["type"] == "claim_result")
    done = next(e for e in events if e["type"] == "done")
    print(f"saved {sample['id']} -> {path}  {dict(verdicts)}  {done['stats']['seconds']}s, "
          f"~${done['stats']['usage']['total']['usd']:.3f} tokens, {done['stats']['tavily_credits']} Tavily credits")


async def main() -> None:
    if settings.mock or not settings.live_ready:
        raise SystemExit("Set NEBIUS_API_KEY and TAVILY_API_KEY (and not RECEIPTS_MOCK) before recording.")
    llm = TokenFactory(settings)
    for sample in SAMPLES:
        await record(sample, llm)
    print("\nReview the verdicts above. If one looks wrong, note it in docs/LIMITATIONS.md or re-run with --force.")


if __name__ == "__main__":
    asyncio.run(main())
