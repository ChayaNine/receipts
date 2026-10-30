"""Grounding checks: Receipts never shows an evidence quote that is not actually in the source."""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")
_STOP = set(
    "a an the and or of to in on for with by at from as is are was were be been being that this these those it its "
    "which who whom whose what when where why how not no but if then than so such into over under about after before "
    "between during through per via vs v also can could may might must should would will shall do does did has have had "
    "their there they them he she his her we our you your i".split()
)


def tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    text = re.sub(r"[\*_`#>\[\]\(\)]", " ", text)  # markdown noise from extracted pages
    text = re.sub(r"[^\w\s'\-.,;:%$]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_chunks(source: str, size: int = 900) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\[\.\.\.\]", source or "") if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(p) > size * 1.6:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                if len(buf) + len(s) > size and buf:
                    chunks.append(buf)
                    buf = ""
                buf = f"{buf} {s}".strip()
            continue
        if len(buf) + len(p) > size and buf:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def select_passages(source: str, query: str, k: int = 6, max_chars: int = 9000) -> str:
    """BM25-style ranking of source chunks against the claim; returns top chunks in reading order."""
    chunks = split_chunks(source)
    if not chunks:
        return ""
    if sum(len(c) for c in chunks) <= max_chars:
        return "\n\n[...]\n\n".join(chunks)
    q = set(tokens(query))
    docs = [Counter(tokens(c)) for c in chunks]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / n or 1
    df = Counter(t for d in docs for t in d)
    scores = []
    for i, d in enumerate(docs):
        length = sum(d.values()) or 1
        score = 0.0
        for t in q:
            if t not in d:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            tf = d[t]
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
        scores.append((score, i))
    picked, total = [], 0
    for score, i in sorted(scores, reverse=True):
        if len(picked) >= k or total + len(chunks[i]) > max_chars:
            continue
        picked.append(i)
        total += len(chunks[i])
    picked.sort()
    return "\n\n[...]\n\n".join(chunks[i] for i in picked)


def find_quote(quote: str, source: str, threshold: float = 0.86) -> dict:
    """Is `quote` (near-)verbatim in `source`? Returns {"found", "score", "match"}."""
    q = normalize(quote).strip(" .\"'")
    s = normalize(source)
    if not q or not s:
        return {"found": False, "score": 0.0, "match": None}
    if q in s:
        return {"found": True, "score": 1.0, "match": quote}
    q_words = q.split()
    s_words = s.split()
    if len(q_words) < 3:
        return {"found": False, "score": 0.0, "match": None}
    # Anchor on the rarest words of the quote, then compare windows of the same length.
    window = len(q_words)
    anchors = sorted(set(q_words), key=lambda w: (s_words.count(w) or 10**6, -len(w)))[:4]
    positions = set()
    for anchor in anchors:
        if anchor not in q_words:
            continue
        offset = q_words.index(anchor)
        for i, w in enumerate(s_words):
            if w == anchor:
                positions.add(max(0, i - offset))
    best, best_match = 0.0, None
    for p in list(positions)[:400]:
        for delta in (-2, -1, 0, 1, 2):
            start = max(0, p + delta)
            cand = s_words[start : start + window]
            if not cand:
                continue
            ratio = difflib.SequenceMatcher(None, " ".join(cand), q).ratio()
            if ratio > best:
                best, best_match = ratio, " ".join(cand)
    return {"found": best >= threshold, "score": round(best, 3), "match": best_match}
