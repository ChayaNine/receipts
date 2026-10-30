"""The Receipts agent: extract cited claims -> find & read each source -> judge -> escalate -> report.

Every step streams an event to the browser so the user watches the document light up claim by claim,
and the "Agent trace" panel shows which model or tool did what.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from . import prompts
from .config import Settings
from .documents import DOI_RE, URL_RE, chunk_document, citation_hints, locate, split_sentences
from .grounding import find_quote, normalize, select_passages
from .llm import ROLE_LABEL, Emit, TokenFactory, Usage
from .tavily import Tavily

VERDICTS = ("supported", "partial", "misrepresented", "contradicted", "not_found", "unverifiable")
RISKY = {"misrepresented", "contradicted", "not_found"}
PIPELINE_VERSION = "1.0"


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return url


def _title_from_markdown(content: str, url: str) -> str:
    m = re.search(r"^#\s+(.+)$", content or "", re.M)
    if m:
        return m.group(1).strip()[:140]
    return _host(url)


def _clamp(x: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.5) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return default


def _verdict(v: Any, allowed=VERDICTS, default: str = "unverifiable") -> str:
    v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"partially_supported": "partial", "fabricated": "not_found", "notfound": "not_found",
               "unsupported": "misrepresented", "refuted": "contradicted"}
    v = aliases.get(v, v)
    return v if v in allowed else default


def _fmt_results(results: list[dict], chars: int = 600) -> str:
    rows = []
    for i, r in enumerate(results):
        rows.append(f"[{i}] {r.get('title', '')}\nURL: {r.get('url', '')}\nText: {(r.get('content') or '')[:chars]}")
    return "\n\n".join(rows) or "(no results)"


class CheckRun:
    def __init__(self, settings: Settings, llm: TokenFactory, tavily: Tavily, emit: Emit) -> None:
        self.s = settings
        self.llm = llm
        self.tavily = tavily
        self._emit = emit
        self.usage = Usage()
        self.sem = asyncio.Semaphore(max(1, settings.concurrency))
        self.models: dict[str, str] = {}

    async def emit(self, event: dict) -> None:
        await self._emit(event)

    async def status(self, claim_id: str, status: str, detail: str = "") -> None:
        await self.emit({"type": "claim_status", "id": claim_id, "status": status, "detail": detail})

    async def ask(self, role: str, system: str, user: str, *, action: str, claim_id: str | None = None,
                  max_tokens: int = 2048) -> dict:
        reply = await self.llm.chat(
            role,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, emit=self.emit, action=action, claim_id=claim_id, usage=self.usage,
        )
        if isinstance(reply, list):
            reply = {"items": reply}
        return reply if isinstance(reply, dict) else {}

    # ------------------------------------------------------------------ run
    async def execute(self, text: str, title: str = "Untitled document") -> dict:
        started = time.perf_counter()
        self.models = await self.llm.resolve_models()
        await self.emit({"type": "models", "models": {r: {"id": m, "label": ROLE_LABEL[r]} for r, m in self.models.items()}})
        await self.emit({"type": "stage", "stage": "extract",
                         "message": "Nemotron 3 Nano is finding every claim that cites a source"})
        claims = await self.extract_claims(text)
        await self.emit({"type": "claims", "claims": claims})
        if not claims:
            report = {"headline": "No cited claims found",
                      "summary": "Receipts looks for sentences that rely on a specific source (a link, case, paper, "
                                 "statute or reference). This document doesn't appear to contain any.",
                      "risk": "low", "actions": []}
            await self.emit({"type": "summary", **report, "score": None, "counts": {}})
            await self.emit({"type": "done", "stats": self.stats(started)})
            return report

        await self.emit({"type": "stage", "stage": "verify",
                         "message": f"Checking {len(claims)} claims against their sources"})
        results = await asyncio.gather(*(self.check_claim(c) for c in claims), return_exceptions=True)
        final = []
        for claim, res in zip(claims, results):
            if isinstance(res, Exception):
                res = {"id": claim["id"], "verdict": "unverifiable", "confidence": 0.0,
                       "explanation": f"Check failed: {str(res)[:160]}", "evidence": "", "evidence_verified": False,
                       "judged_by": [], "source": None}
                await self.emit({"type": "claim_result", **res})
            final.append((claim, res))

        await self.emit({"type": "stage", "stage": "report", "message": "Nemotron 3 Ultra is writing the report"})
        report = await self.summarize(title, final)
        await self.emit({"type": "summary", **report})
        await self.emit({"type": "done", "stats": self.stats(started)})
        return report

    def stats(self, started: float) -> dict:
        return {"seconds": round(time.perf_counter() - started, 1), "usage": self.usage.summary(),
                "tavily_credits": round(self.tavily.credits, 2), "models": self.models}

    # ------------------------------------------------------------ extraction
    async def extract_claims(self, text: str) -> list[dict]:
        hints = citation_hints(text)
        hint_str = json.dumps({k: v for k, v in hints.items() if k != "reference_list" and v})[:2000]
        chunks = chunk_document(text)

        async def one(offset: int, chunk: str) -> list[tuple[int, dict]]:
            system = prompts.EXTRACT_SYSTEM.replace("MAX_CLAIMS", str(self.s.max_claims))
            user = prompts.EXTRACT_USER.format(hints=hint_str or "none", references=hints["reference_list"] or "none",
                                               chunk=chunk)
            try:
                reply = await self.ask("fast", system, user, action="extract cited claims", max_tokens=4096)
            except Exception:
                return []
            items = reply.get("claims") or reply.get("items") or []
            return [(offset, it) for it in items if isinstance(it, dict) and it.get("claim")]

        found = []
        for batch in await asyncio.gather(*(one(o, c) for o, c in chunks)):
            found.extend(batch)
        if not found:
            found = [(0, c) for c in self._fallback_claims(text, hints)]

        claims, seen = [], set()
        for offset, it in found:
            claim_text = str(it.get("claim", "")).strip()
            key = normalize(claim_text)[:160]
            if not key or key in seen:
                continue
            seen.add(key)
            span = locate(claim_text, text, hint_start=offset)
            url = it.get("url") or None
            if not url:
                m = URL_RE.search(f"{claim_text} {it.get('citation', '')}")
                url = m.group(0).rstrip(".,;:") if m else None
            if not url:
                d = DOI_RE.search(str(it.get("citation", "")))
                url = f"https://doi.org/{d.group(0).rstrip('.,;:')}" if d else None
            claims.append({
                "claim": text[span[0]:span[1]] if span else claim_text,
                "start": span[0] if span else None,
                "end": span[1] if span else None,
                "assertion": str(it.get("assertion") or claim_text)[:400],
                "citation": str(it.get("citation") or "")[:600],
                "kind": str(it.get("kind") or "other").lower(),
                "url": url,
                "quoted_text": (str(it["quoted_text"]).strip() if it.get("quoted_text") else None),
                "search_query": str(it.get("search_query") or it.get("citation") or claim_text)[:300],
            })
        claims.sort(key=lambda c: (c["start"] is None, c["start"] or 0))
        claims = claims[: self.s.max_claims]
        for i, c in enumerate(claims, 1):
            c["id"] = f"c{i}"
        return claims

    @staticmethod
    def _fallback_claims(text: str, hints: dict) -> list[dict]:
        """If the extractor fails, still check every sentence that contains a URL or a case citation."""
        out = []
        for s, e in split_sentences(text):
            sentence = text[s:e].strip()
            if not re.search(r"https?://\S+|\bv\.\s|\b10\.\d{4,9}/", sentence):
                continue
            url = URL_RE.search(sentence)
            out.append({"claim": sentence, "assertion": sentence, "citation": sentence,
                        "kind": "url" if url else "case", "url": url.group(0).rstrip(".,;:") if url else None,
                        "search_query": sentence[:200]})
        return out

    # --------------------------------------------------------------- sources
    async def resolve_source(self, c: dict) -> dict:
        """Find and read the cited source. Returns {"found": bool, ...}."""
        cid = c["id"]
        relevance_query = f"{c['assertion']} {c.get('quoted_text') or ''}".strip()
        if c.get("url"):
            await self.status(cid, "reading", f"Reading {_host(c['url'])}")
            page = await self.tavily.extract(c["url"], query=relevance_query, emit=self.emit, claim_id=cid)
            if page:
                return {"found": True, "url": c["url"], "title": _title_from_markdown(page["raw_content"], c["url"]),
                        "passages": page["raw_content"][:14000], "secondary": False, "how": "extract"}
            await self.status(cid, "searching", "Link didn't load, searching for the source instead")

        queries = [c["search_query"]]
        if c["citation"] and normalize(c["citation"]) != normalize(c["search_query"]):
            queries.append(c["citation"][:250])
        all_results: list[dict] = []
        signals = []
        for q in queries[:2]:
            await self.status(cid, "searching", f"Searching: {q[:80]}")
            try:
                results = await self.tavily.search(q, emit=self.emit, claim_id=cid,
                                                   topic="news" if c["kind"] == "news" else "general")
            except Exception:
                results = []
            all_results.extend(results)
            if not results:
                continue
            match = await self.ask("fast", prompts.MATCH_SYSTEM,
                                   prompts.MATCH_USER.format(citation=c["citation"] or c["claim"], kind=c["kind"],
                                                             results=_fmt_results(results)),
                                   action="match citation to search results", claim_id=cid, max_tokens=1024)
            if match.get("fabrication_signal"):
                signals.append(str(match["fabrication_signal"]))
            idx = match.get("match")
            if isinstance(idx, int) and 0 <= idx < len(results) and _clamp(match.get("confidence")) >= 0.5:
                hit = results[idx]
                await self.status(cid, "reading", f"Reading {_host(hit.get('url', ''))}")
                page = None
                try:
                    page = await self.tavily.extract(hit["url"], query=relevance_query, emit=self.emit, claim_id=cid)
                except Exception:
                    page = None
                passages = page["raw_content"] if page else (hit.get("content") or "")
                return {"found": True, "url": hit.get("url"), "title": hit.get("title") or _host(hit.get("url", "")),
                        "passages": passages[:14000], "secondary": bool(match.get("secondary")), "how": "search"}
        return {"found": False, "results": all_results[:8], "signals": signals}

    # ---------------------------------------------------------------- judge
    async def check_claim(self, c: dict) -> dict:
        cid = c["id"]
        async with self.sem:
            await self.status(cid, "searching", "Looking for the source")
            src = await self.resolve_source(c)

            if not src["found"]:
                return await self._adjudicate_missing(c, src)

            quote_note = ""
            if c.get("quoted_text"):
                q = find_quote(c["quoted_text"], src["passages"])
                quote_note = (f"AUTOMATED QUOTE CHECK: the document's quotation \"{c['quoted_text']}\" "
                              + ("WAS found verbatim in the source." if q["found"] else
                                 f"was NOT found verbatim in the source passages (closest text: \"{q['match']}\")."))
            await self.status(cid, "judging", "Nemotron 3 Super is comparing the claim with the source")
            passages = select_passages(src["passages"], f"{c['claim']} {c['assertion']}")
            j = await self.ask("judge", prompts.JUDGE_SYSTEM, prompts.JUDGE_USER.format(
                claim=c["claim"], assertion=c["assertion"], citation=c["citation"], source_title=src["title"],
                source_url=src["url"], quote_note=quote_note, passages=passages,
                secondary_note=" (secondary source that discusses the cited work)" if src.get("secondary") else "",
            ), action="judge claim against source", claim_id=cid, max_tokens=6000)

            verdict = _verdict(j.get("verdict"), allowed=VERDICTS[:4] + ("unverifiable",))
            confidence = _clamp(j.get("confidence"))
            evidence = str(j.get("evidence") or "").strip()
            grounded = find_quote(evidence, src["passages"]) if evidence else {"found": False}
            judged_by = ["judge"]
            result = {
                "id": cid, "verdict": verdict, "confidence": confidence, "evidence": evidence,
                "evidence_verified": bool(grounded.get("found")),
                "explanation": str(j.get("explanation") or "")[:400],
                "corrected_claim": j.get("corrected_claim") or None,
                "severity": str(j.get("severity") or ("high" if verdict in RISKY else "low")),
                "source": {"url": src["url"], "title": src["title"], "secondary": src.get("secondary", False)},
                "quote_check": quote_note or None,
            }
            if not result["evidence_verified"]:
                result["confidence"] = max(0.0, confidence - 0.25)

            if verdict in RISKY or result["confidence"] < self.s.escalate_below or not result["evidence_verified"]:
                await self.status(cid, "escalating", "Nemotron 3 Ultra is double-checking this one")
                first = f"{verdict} (confidence {confidence:.2f}). {result['explanation']}"
                u = await self.ask("reason", prompts.ESCALATE_SYSTEM, prompts.ESCALATE_USER.format(
                    claim=c["claim"], assertion=c["assertion"], citation=c["citation"],
                    first_model=ROLE_LABEL["judge"], first=first,
                    evidence_block=f"{quote_note}\nSOURCE: {src['title']} — {src['url']}\nSOURCE PASSAGES:\n<<<\n{passages}\n>>>",
                ), action="second opinion on risky verdict", claim_id=cid, max_tokens=10000)
                judged_by.append("reason")
                new_verdict = _verdict(u.get("verdict"), default=verdict)
                new_evidence = str(u.get("evidence") or "").strip() or evidence
                new_grounded = find_quote(new_evidence, src["passages"]) if new_evidence else {"found": False}
                result.update({
                    "verdict": new_verdict,
                    "confidence": _clamp(u.get("confidence"), default=confidence),
                    "evidence": new_evidence,
                    "evidence_verified": bool(new_grounded.get("found")),
                    "explanation": str(u.get("explanation") or result["explanation"])[:400],
                    "corrected_claim": u.get("corrected_claim") or result["corrected_claim"],
                    "severity": str(u.get("severity") or result["severity"]),
                    "overturned": new_verdict != verdict,
                    "first_verdict": verdict,
                })
                if not result["evidence_verified"] and result["evidence"]:
                    result["confidence"] = max(0.0, result["confidence"] - 0.15)
            if result["verdict"] == "supported":
                result["corrected_claim"] = None
            result["judged_by"] = judged_by
            await self.emit({"type": "claim_result", **result})
            return result

    async def _adjudicate_missing(self, c: dict, src: dict) -> dict:
        cid = c["id"]
        await self.status(cid, "escalating", "Source not found. Nemotron 3 Ultra is deciding if it exists")
        signals = "\n".join(f"- {s}" for s in src.get("signals", [])) or "- none"
        block = (f"No search result matched the citation.\nSignals noticed while matching:\n{signals}\n\n"
                 f"SEARCH RESULTS:\n{_fmt_results(src.get('results', []), chars=450)}")
        u = await self.ask("reason", prompts.ESCALATE_SYSTEM, prompts.ESCALATE_USER.format(
            claim=c["claim"], assertion=c["assertion"], citation=c["citation"], first_model="search agent",
            first="not_found — two web searches found no copy of this source.", evidence_block=block,
        ), action="decide whether the source exists", claim_id=cid, max_tokens=10000)
        verdict = _verdict(u.get("verdict"), allowed=("not_found", "unverifiable"), default="not_found")
        evidence = str(u.get("evidence") or "").strip()
        haystack = " ".join(f"{r.get('title', '')} {r.get('content', '')}" for r in src.get("results", []))
        top = (src.get("results") or [None])[0]
        result = {
            "id": cid, "verdict": verdict, "confidence": _clamp(u.get("confidence"), default=0.6),
            "evidence": evidence, "evidence_verified": bool(evidence and find_quote(evidence, haystack)["found"]),
            "explanation": str(u.get("explanation") or "No copy of this source could be found.")[:400],
            "corrected_claim": u.get("corrected_claim") or None, "severity": "high" if verdict == "not_found" else "medium",
            "source": ({"url": top.get("url"), "title": top.get("title"), "secondary": True, "note": "closest search result"}
                       if top else None),
            "judged_by": ["fast", "reason"], "searched": [r.get("url") for r in src.get("results", [])][:5],
        }
        await self.emit({"type": "claim_result", **result})
        return result

    # --------------------------------------------------------------- report
    async def summarize(self, title: str, final: list[tuple[dict, dict]]) -> dict:
        counts = {v: 0 for v in VERDICTS}
        for _, r in final:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        checkable = sum(counts.values()) - counts["unverifiable"]
        score = round(100 * (counts["supported"] + 0.5 * counts["partial"]) / checkable) if checkable else None
        rows = "\n".join(
            f"- [{r['verdict']}] \"{c['claim'][:180]}\" — cites: {c['citation'][:120]} — {r.get('explanation', '')[:160]}"
            for c, r in final)
        try:
            s = await self.ask("reason", prompts.SUMMARY_SYSTEM, prompts.SUMMARY_USER.format(title=title, rows=rows),
                               action="write the report summary", max_tokens=6000)
        except Exception:
            s = {}
        bad = counts["not_found"] + counts["misrepresented"] + counts["contradicted"]
        fallback_headline = (f"{bad} of {len(final)} citations need attention" if bad else "Citations hold up")
        return {
            "headline": str(s.get("headline") or fallback_headline)[:120],
            "summary": str(s.get("summary") or "")[:800],
            "risk": s.get("risk") if s.get("risk") in ("low", "medium", "high") else ("high" if bad else "low"),
            "actions": [str(a)[:160] for a in (s.get("actions") or [])][:3],
            "score": score, "counts": counts,
        }


async def find_replacement_source(settings: Settings, llm: TokenFactory, tavily: Tavily, claim: str) -> dict:
    """'Find a real source' button: search the open web for a source that genuinely supports the claim."""
    usage = Usage()
    results = await tavily.search(claim, depth="advanced", max_results=6)
    if not results:
        return {"found": False}
    reply = await llm.chat("judge", [
        {"role": "system", "content": prompts.FIND_SOURCE_SYSTEM},
        {"role": "user", "content": prompts.FIND_SOURCE_USER.format(claim=claim, results=_fmt_results(results, 900))},
    ], max_tokens=2000, usage=usage)
    idx = reply.get("index") if isinstance(reply, dict) else None
    if not isinstance(idx, int) or not (0 <= idx < len(results)):
        return {"found": False}
    hit = results[idx]
    evidence = str(reply.get("evidence") or "")
    return {
        "found": True, "url": hit.get("url"), "title": hit.get("title"),
        "evidence": evidence, "evidence_verified": find_quote(evidence, hit.get("content") or "")["found"],
        "suggested_citation": reply.get("suggested_citation"), "explanation": reply.get("explanation"),
        "model": ROLE_LABEL["judge"],
    }
