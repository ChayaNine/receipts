"""Simulated Token Factory + Tavily, used ONLY when RECEIPTS_MOCK=1.

It lets you develop the UI and run the test suite without API keys. The UI shows a clear
"Simulated" banner in this mode, and simulated runs are never written to the replay cache.
The canned "source" passages below are short stand-ins written for testing, not real page text.
"""
from __future__ import annotations

import asyncio
import random
import re

from .documents import split_sentences
from .grounding import normalize
from .llm import ROLE_LABEL, Usage

SOURCES = {
    "mata": ("Mata v. Avianca, Inc. — Opinion and Order on Sanctions", "https://law.justia.com/cases/federal/district-courts/new-york/nysdce/1:2022cv01461/575368/54/",
             "The Court imposes a penalty of $5,000 jointly and severally on the respondent attorneys and their law firm. "
             "The attorneys submitted non-existent judicial opinions with fake quotes and citations created by the artificial intelligence tool ChatGPT."),
    "rule_11": ("Rule 11. Signing Pleadings, Motions, and Other Papers", "https://www.law.cornell.edu/rules/frcp/rule_11",
                "By presenting to the court a pleading, written motion, or other paper an attorney certifies that "
                "the claims, defenses, and other legal contentions are warranted by existing law or by a nonfrivolous argument for extending, modifying, or reversing existing law or for establishing new law."),
    "marbury": ("Marbury v. Madison, 5 U.S. 137 (1803)", "https://supreme.justia.com/cases/federal/us/5/137/",
                "It is emphatically the province and duty of the Judicial Department to say what the law is. "
                "A law repugnant to the Constitution is void, and courts as well as other departments are bound by that instrument."),
    "arxiv": ("Attention Is All You Need", "https://arxiv.org/abs/1706.03762",
              "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. "
              "Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation task. "
              "On the WMT 2014 English-to-French translation task, our model establishes a new single-model state-of-the-art BLEU score of 41.8."),
    "gptzero": ("GPTZero finds 100 new hallucinations in NeurIPS 2025 accepted papers", "https://gptzero.me/news/neurips/",
                "Our analysis of 4841 papers accepted by NeurIPS 2025 shows there are at least 100 with confirmed hallucinations."),
    "wall": ("Great Wall of China — Wikipedia", "https://en.wikipedia.org/wiki/Great_Wall_of_China",
             "A common claim is that the Great Wall is visible from the Moon. This is a myth: the wall is not visible to the naked eye from the Moon."),
    "rasch": ("About sleep's role in memory — Physiological Reviews", "https://journals.physiology.org/doi/full/10.1152/physrev.00032.2012",
              "This review covers a large body of research showing that sleep supports the consolidation of newly encoded memories, an active process involving reactivation during sleep."),
    "okano": ("Sleep quality, duration, and consistency are associated with better academic performance in college students", "https://doi.org/10.1038/s41539-019-0055-z",
              "Better sleep quality, longer sleep duration and greater sleep consistency were associated with better academic performance, measured by wearable activity trackers."),
}

FAKE = {
    "varghese": "Several results describe Varghese v. China Southern Airlines as a non-existent case invented by ChatGPT and cited in Mata v. Avianca.",
    "smith and zhao": None,
    "henderson": None,
}

JUDGE = {
    "mata": ("supported", 0.93, 0, "The sanctions order imposes a $5,000 penalty jointly on the two attorneys and their firm for submitting fake opinions produced by ChatGPT.", None),
    "rule_11": ("supported", 0.9, 0, "Rule 11(b)(2) says signing certifies legal contentions are warranted by existing law or a nonfrivolous argument to change it.", None),
    "marbury": ("contradicted", 0.95, 0, "Marbury says the opposite: a law repugnant to the Constitution is void, and courts may say so. It established judicial review.",
                "In Marbury v. Madison, 5 U.S. 137 (1803), the Supreme Court held that federal courts may strike down acts of Congress that conflict with the Constitution."),
    "arxiv_quote": ("supported", 0.96, 0, "The abstract uses exactly this wording to describe the Transformer.", None),
    "arxiv_bleu": ("misrepresented", 0.92, 1, "The paper reports 28.4 BLEU for English-to-German. 41.8 was its English-to-French score.",
                   "The paper reports that the Transformer scored 28.4 BLEU on the WMT 2014 English-to-German translation task (and 41.8 on English-to-French)."),
    "gptzero": ("supported", 0.9, 0, "GPTZero says it found at least 100 confirmed hallucinations across NeurIPS 2025 accepted papers.", None),
    "wall": ("contradicted", 0.94, 1, "The article calls visibility from the Moon a myth: the wall cannot be seen there with the naked eye.",
             "Despite a popular myth, the Great Wall of China is not visible from the Moon with the naked eye."),
    "rasch": ("supported", 0.88, 0, "The review describes sleep as actively supporting the consolidation of newly encoded memories.", None),
    "okano": ("contradicted", 0.93, 0, "The study found better sleep quality, duration and consistency were associated with better grades, not no relationship.",
              "Okano et al. (2019) found that better sleep quality, duration and consistency were associated with better academic performance."),
}


def _key(text: str) -> str | None:
    t = text.lower()
    for k, needle in [("mata", "mata v"), ("varghese", "varghese"), ("rule_11", "rule of civil procedure 11"),
                      ("marbury", "marbury"), ("arxiv_bleu", "41.8"), ("arxiv_quote", "solely on attention"),
                      ("gptzero", "gptzero"), ("smith and zhao", "smith and zhao"), ("wall", "great wall"),
                      ("rasch", "rasch"), ("henderson", "henderson"), ("okano", "okano")]:
        if needle in t:
            return k
    return None


def _source_key(k: str | None) -> str | None:
    return {"arxiv_bleu": "arxiv", "arxiv_quote": "arxiv"}.get(k, k)


class MockTokenFactory:
    def __init__(self, settings) -> None:
        self.s = settings

    async def resolve_models(self) -> dict[str, str]:
        return {"fast": self.s.model_fast, "judge": self.s.model_judge, "reason": self.s.model_reason}

    async def list_models(self) -> list[str]:
        return list((await self.resolve_models()).values())

    async def chat(self, role, messages, *, json_mode=True, max_tokens=2048, temperature=0.1, emit=None,
                   action="", claim_id=None, usage: Usage | None = None):
        delay = {"fast": 0.5, "judge": 1.1, "reason": 1.8}[role] * random.uniform(0.6, 1.3)
        await asyncio.sleep(delay)
        user = messages[-1]["content"]
        reply = self._reply(action, user)
        tin, tout = len(user) // 4, {"fast": 220, "judge": 480, "reason": 1350}[role]
        if usage is not None:
            usage.add(role, tin, tout, delay * 1000)
        if emit:
            await emit({"type": "trace", "actor": role, "label": ROLE_LABEL[role], "model": self.s.model_for(role),
                        "action": action, "claim_id": claim_id, "ms": round(delay * 1000),
                        "tokens_in": tin, "tokens_out": tout})
        return reply

    def _reply(self, action: str, user: str):
        if action.startswith("extract"):
            return {"claims": self._extract(user)}
        if action.startswith("match"):
            k = _key(user.split("Search results:")[0])
            if k in FAKE:
                return {"match": None, "secondary": False, "confidence": 0.1, "reason": "No result is this source.",
                        "fabrication_signal": FAKE[k]}
            return {"match": 0, "secondary": False, "confidence": 0.9, "reason": "Title and venue match."}
        if action.startswith("judge") or action.startswith("second opinion"):
            claim = re.search(r"CLAIM[^:]*:\s*(.+)", user).group(1)
            k = _key(claim)
            verdict, conf, ev_idx, expl, fix = JUDGE.get(k, ("supported", 0.8, 0, "The source states this directly.", None))
            src = SOURCES.get(_source_key(k) or "", (None, None, ""))[2]
            sentences = re.split(r"(?<=[.!?])\s+", src) if src else [""]
            evidence = sentences[min(ev_idx, len(sentences) - 1)]
            if action.startswith("second opinion"):
                conf = min(0.98, conf + 0.03)
                return {"verdict": verdict, "confidence": conf, "agrees_with_first": True, "evidence": evidence,
                        "explanation": expl, "corrected_claim": fix, "severity": "high" if fix else "low"}
            return {"verdict": verdict, "confidence": conf, "evidence": evidence, "explanation": expl,
                    "corrected_claim": fix, "severity": "high" if fix else "low"}
        if action.startswith("decide"):
            k = _key(user.split("FIRST VERDICT")[0])
            expl = {
                "varghese": "This case does not exist. Searches only find coverage of Mata v. Avianca, where it was exposed as invented by ChatGPT.",
                "smith and zhao": "No paper with this title or authors appears in NeurIPS proceedings or any index searched.",
                "henderson": "Neither the article nor the journal 'Journal of Educational Sleep Research' can be found.",
            }.get(k, "No copy of this source could be found.")
            return {"verdict": "not_found", "confidence": 0.9, "agrees_with_first": True, "evidence": "",
                    "explanation": expl, "corrected_claim": None, "severity": "high"}
        if action.startswith("write the report"):
            bad = len(re.findall(r"\[(not_found|misrepresented|contradicted)\]", user))
            total = len(re.findall(r"^- \[", user, re.M))
            return {"headline": f"{bad} of {total} citations would not survive scrutiny" if bad else "Citations hold up",
                    "summary": "Some citations point to sources that do not exist, and others say something different "
                               "from what the document claims. Fix the flagged sentences before sharing this document.",
                    "risk": "high" if bad else "low",
                    "actions": ["Remove or replace the citations marked Not found",
                                "Correct the sentences marked Contradicted or Misrepresented",
                                "Re-run Receipts after editing"]}
        return {}

    @staticmethod
    def _extract(user: str) -> list[dict]:
        chunk = user.split("<<<", 1)[-1].rsplit(">>>", 1)[0]
        refs = user.split("Reference list (if any):", 1)[-1].split("Document section:", 1)[0]
        body = re.split(r"\n\s*References\s*\n", chunk)[0]
        out = []
        for start, end in split_sentences(body):
            s = body[start:end].strip()
            k = _key(s)
            if not k or (k == "rule_11" and "11(b)" not in s):
                continue
            url = re.search(r"https?://[^\s)]+", s)
            citation = s
            for line in refs.splitlines():
                author = line.split(",")[0].strip()
                if author and len(author) > 3 and author.lower() in s.lower():
                    citation = line.strip()
                    url = url or re.search(r"https?://\S+", line)
            if k in ("varghese", "mata", "marbury"):
                citation = re.search(r"[A-Z][\w.,' ]+ v\. [^()]+\(\S+ ?\S* ?\d{4}\)", s)
                citation = citation.group(0) if citation else s
            quoted = re.search(r"\"([^\"]{12,})\"", s)
            out.append({"claim": s, "assertion": s, "citation": citation[:300], "kind": "url" if url else "case",
                        "url": url.group(0).rstrip(".,") if url else None,
                        "quoted_text": quoted.group(1) if quoted and k == "arxiv_quote" else None,
                        "search_query": citation[:200]})
        return out


class MockTavily:
    def __init__(self, settings) -> None:
        self.s = settings
        self.credits = 0.0

    async def _trace(self, emit, label, action, claim_id, ms, **extra):
        if emit:
            await emit({"type": "trace", "actor": "tavily", "label": label, "action": action, "claim_id": claim_id,
                        "ms": ms, **extra})

    async def search(self, query, *, depth="advanced", max_results=6, include_domains=None, topic="general",
                     emit=None, claim_id=None):
        delay = random.uniform(0.4, 0.9)
        await asyncio.sleep(delay)
        self.credits += 2 if depth == "advanced" else 1
        k = _source_key(_key(query))
        if k in FAKE or k is None:
            results = [{"title": "Mata v. Avianca: lawyers sanctioned for fake ChatGPT citations",
                        "url": "https://example.org/simulated-news", "content": FAKE.get(k) or "Unrelated result."}]
        else:
            title, url, content = SOURCES[k]
            results = [{"title": title, "url": url, "content": content}]
        await self._trace(emit, "Tavily Search", f"search: {query[:90]}", claim_id, round(delay * 1000),
                          results=len(results), credits=2)
        return results

    async def extract(self, url, *, query=None, chunks_per_source=5, depth="basic", emit=None, claim_id=None):
        delay = random.uniform(0.5, 1.1)
        await asyncio.sleep(delay)
        self.credits += 0.2
        match = next((v for v in SOURCES.values() if normalize(v[1]) == normalize(url)), None)
        await self._trace(emit, "Tavily Extract", ("extract (reranked to claim): " if query else "extract: ") + url[:90],
                          claim_id, round(delay * 1000), ok=bool(match), credits=0.2)
        if not match:
            return {"url": url, "raw_content": f"# Simulated page\n\nThis simulated page supports the statement: {query or ''}"}
        return {"url": url, "raw_content": f"# {match[0]}\n\n{match[2]}"}
