"""HTTP layer: serves the single-page app and streams a check as Server-Sent Events."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .documents import clean_text, looks_like_url, parse_upload
from .pipeline import PIPELINE_VERSION, CheckRun, find_replacement_source
from .samples import SAMPLES, SAMPLES_BY_ID

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Receipts", version=PIPELINE_VERSION)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

if settings.mock:
    from .mock import MockTavily, MockTokenFactory

    def make_clients():
        return MockTokenFactory(settings), MockTavily(settings)
else:
    from .llm import TokenFactory
    from .tavily import Tavily

    _shared_llm = TokenFactory(settings)

    def make_clients():
        # Share the LLM client (and its model resolution); Tavily per run so credits are counted per run.
        return _shared_llm, Tavily(settings, client=_shared_llm.client)


_runs: dict[str, deque] = defaultdict(deque)
_all_runs: deque = deque()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _allow_live_run(ip: str) -> bool:
    """Protects the demo's API credits: a per-visitor hourly limit plus a server-wide daily cap."""
    window = _runs[ip]
    now = time.time()
    while window and now - window[0] > 3600:
        window.popleft()
    while _all_runs and now - _all_runs[0] > 86400:
        _all_runs.popleft()
    if len(window) >= settings.live_runs_per_hour or len(_all_runs) >= settings.live_runs_per_day:
        return False
    window.append(now)
    _all_runs.append(now)
    return True


def _cache_key(text: str) -> str:
    material = json.dumps([PIPELINE_VERSION, settings.model_fast, settings.model_judge, settings.model_reason, text])
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def _cache_path(key: str) -> Path:
    return settings.cache_dir / f"{key}.json"


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config():
    return {
        "mode": "simulated" if settings.mock else ("live" if settings.live_ready else "replay-only"),
        "models": {"fast": settings.model_fast, "judge": settings.model_judge, "reason": settings.model_reason},
        "samples": [{"id": s["id"], "title": s["title"], "subtitle": s["subtitle"],
                     "cached": _cache_path(_cache_key(clean_text(s["text"]))).exists()} for s in SAMPLES],
        "max_chars": settings.max_chars,
    }


@app.get("/api/samples/{sample_id}")
async def sample(sample_id: str):
    s = SAMPLES_BY_ID.get(sample_id)
    if not s:
        return JSONResponse({"error": "unknown sample"}, status_code=404)
    return {"id": s["id"], "title": s["title"], "text": clean_text(s["text"])}


@app.get("/api/health")
async def health():
    out = {"ok": True, "mode": "simulated" if settings.mock else "live",
           "nebius_key": bool(settings.nebius_api_key), "tavily_key": bool(settings.tavily_api_key)}
    if not settings.mock and settings.nebius_api_key:
        try:
            llm, _ = make_clients()
            out["models"] = await llm.resolve_models()
        except Exception as exc:  # pragma: no cover - network
            out["ok"], out["error"] = False, str(exc)[:200]
    return out


async def _read_input(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            data = await upload.read()
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("File is larger than 8 MB.")
            return {"text": parse_upload(upload.filename, data), "title": upload.filename,
                    "fresh": form.get("fresh") == "true"}
        return {"text": str(form.get("text") or ""), "fresh": form.get("fresh") == "true"}
    body = await request.json()
    if body.get("sample_id") in SAMPLES_BY_ID:
        s = SAMPLES_BY_ID[body["sample_id"]]
        return {"text": s["text"], "title": s["title"], "fresh": bool(body.get("fresh"))}
    return {"text": str(body.get("text") or ""), "title": body.get("title"), "fresh": bool(body.get("fresh"))}


def _guess_title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:90] if 0 < len(first) <= 90 else "Pasted text"


@app.post("/api/check")
async def check(request: Request):
    try:
        payload = await _read_input(request)
    except Exception as exc:
        return JSONResponse({"error": f"Could not read that input: {exc}"}, status_code=400)

    ip = _client_ip(request)
    raw = payload["text"].strip()

    async def stream():
        llm, tavily = make_clients()
        text, title = raw, payload.get("title")
        if looks_like_url(raw):
            if not (settings.mock or settings.live_ready):
                yield _sse({"type": "error", "message": "Checking a URL needs live API keys on this server."})
                return
            yield _sse({"type": "stage", "stage": "fetch", "message": "Reading the page with Tavily Extract"})
            try:
                page = await tavily.extract(raw)
            except Exception as exc:
                page = None
                yield _sse({"type": "error", "message": f"Couldn't read that page: {str(exc)[:160]}"})
                return
            if not page:
                yield _sse({"type": "error", "message": "Tavily couldn't read that page. Paste its text instead."})
                return
            text = page["raw_content"]
            title = title or raw
        text = clean_text(text)
        if len(text) < 40:
            yield _sse({"type": "error", "message": "Paste a little more text (at least a few sentences)."})
            return
        truncated = len(text) > settings.max_chars
        text = text[: settings.max_chars]
        title = title or _guess_title(text)

        key = _cache_key(text)
        path = _cache_path(key)
        if path.exists() and not payload.get("fresh") and not settings.mock:
            record = json.loads(path.read_text(encoding="utf-8"))
            yield _sse({"type": "meta", "mode": "replay", "recorded_at": record["recorded_at"], "title": title,
                        "text": text, "live_available": settings.live_ready})
            for event in record["events"]:
                delay = settings.replay_delay if event["type"] in ("claim_result", "claims", "summary") else settings.replay_delay / 4
                await asyncio.sleep(delay)
                yield _sse(event)
            return

        if not (settings.mock or settings.live_ready):
            yield _sse({"type": "meta", "mode": "replay-only", "title": title, "text": text})
            yield _sse({"type": "error", "message": "This demo server has no API keys, so only the recorded samples can run."})
            return
        if not settings.mock and not _allow_live_run(ip):
            yield _sse({"type": "meta", "mode": "live", "title": title, "text": text})
            yield _sse({"type": "error", "message": "You've reached this demo's hourly limit for live checks. "
                                                   "The sample documents still replay instantly."})
            return

        yield _sse({"type": "meta", "mode": "simulated" if settings.mock else "live", "title": title, "text": text,
                    "truncated": truncated})
        queue: asyncio.Queue = asyncio.Queue()
        recorded: list[dict] = []

        async def emit(event: dict) -> None:
            recorded.append(event)
            await queue.put(event)

        async def worker():
            try:
                await CheckRun(settings, llm, tavily, emit).execute(text, title=title)
            except Exception as exc:
                await emit({"type": "error", "message": f"Check stopped: {str(exc)[:240]}"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)
        finally:
            if not task.done():
                task.cancel()
        finished = any(e["type"] == "done" for e in recorded)
        if finished and not settings.mock:
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                        "events": recorded}, ensure_ascii=False), encoding="utf-8")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/find-source")
async def find_source(request: Request):
    body = await request.json()
    claim = str(body.get("claim") or "").strip()[:600]
    if not claim:
        return JSONResponse({"error": "missing claim"}, status_code=400)
    if not (settings.mock or settings.live_ready):
        return JSONResponse({"error": "This demo server has no API keys for live searches."}, status_code=503)
    if not settings.mock and not _allow_live_run(_client_ip(request)):
        return JSONResponse({"error": "Hourly limit reached for live searches."}, status_code=429)
    llm, tavily = make_clients()
    if settings.mock:
        await asyncio.sleep(1.2)
        return {"found": True, "url": "https://example.org/simulated-source", "title": "Simulated source",
                "evidence": "Simulated evidence sentence.", "evidence_verified": True,
                "suggested_citation": "Simulated Source (2024).", "explanation": "Simulated mode.",
                "model": "Nemotron 3 Super"}
    try:
        return await find_replacement_source(settings, llm, tavily, claim)
    except Exception as exc:  # pragma: no cover - network
        return JSONResponse({"error": str(exc)[:200]}, status_code=502)
