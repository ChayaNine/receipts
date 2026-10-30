"""Turn uploads into plain text, spot citation-like strings, and map claims back to character offsets."""
from __future__ import annotations

import difflib
import io
import re
import unicodedata

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
CASE_RE = re.compile(
    r"\b([A-Z][A-Za-z.'&\-]+(?:\s+[A-Z][A-Za-z.'&\-]+){0,6})\s+v\.\s+"
    r"([A-Z][A-Za-z.'&,\-]+(?:\s+[A-Za-z.'&,\-]+){0,8}?),?\s+(\d{1,4})\s+([A-Z][A-Za-z0-9.\s]{1,18}?)\s+(\d{1,5})"
)
REF_HEADING_RE = re.compile(r"^\s*(references|bibliography|works cited|sources|citations|notes)\s*:?\s*$", re.I | re.M)


def parse_upload(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_url(text: str) -> bool:
    t = text.strip()
    return bool(re.fullmatch(r"https?://\S+", t))


def citation_hints(text: str) -> dict:
    """Cheap, deterministic signals passed to the extractor model (and used if it fails)."""
    urls = sorted({u.rstrip(".,;:") for u in URL_RE.findall(text)})
    dois = sorted({d.rstrip(".,;:") for d in DOI_RE.findall(text)})
    cases = sorted({m.group(0).strip() for m in CASE_RE.finditer(text)})
    references = ""
    heading = None
    for heading in REF_HEADING_RE.finditer(text):
        pass
    if heading is not None:
        references = text[heading.end():].strip()[:8000]
    return {"urls": urls, "dois": dois, "cases": cases, "reference_list": references}


_ABBREV = {"v", "vs", "al", "co", "ltd", "inc", "corp", "f", "supp", "cir", "u.s", "r", "p", "civ", "fed", "no",
           "dr", "mr", "ms", "mrs", "st", "e.g", "i.e", "etc", "jr", "sr", "app", "s.d.n.y", "d", "n.d", "ed", "eds",
           "vol", "pp", "fig", "approx", "dept", "univ", "j", "rev", "proc", "conf", "int'l"}


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans that don't break on citations like 'Mata v. Avianca', '678 F. Supp. 3d' or URLs."""
    spans, start = [], 0
    for m in re.finditer(r"[.!?][\"'”)]*(?=\s+[\"'“(]?[A-Z0-9])|\n\s*\n", text):
        end = m.end()
        if m.group(0).startswith("."):
            word = re.search(r"([\w.'’]+)\.$", text[max(0, m.start() - 12) : m.start() + 1])
            if word and word.group(1).lower().rstrip(".") in _ABBREV:
                continue
            if re.search(r"\b[A-Z]\.$", text[max(0, m.start() - 2) : m.start() + 1]):  # initials: "J. R."
                continue
        piece = text[start:end]
        if piece.strip():
            lead = len(piece) - len(piece.lstrip())
            spans.append((start + lead, end))
        start = end
    tail = text[start:]
    if tail.strip():
        spans.append((start + len(tail) - len(tail.lstrip()), len(text.rstrip())))
    return spans


def split_paragraphs(text: str) -> list[tuple[int, str]]:
    """Return (start_offset, paragraph) pairs, keeping exact offsets into `text`."""
    out = []
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        para = m.group(0)
        if para.strip():
            out.append((m.start(), para))
    return out


def chunk_document(text: str, max_chars: int = 7000) -> list[tuple[int, str]]:
    chunks: list[tuple[int, str]] = []
    current_start, current = None, ""
    for start, para in split_paragraphs(text):
        if current and len(current) + len(para) > max_chars:
            chunks.append((current_start, current))
            current_start, current = None, ""
        if current_start is None:
            current_start = start
            current = para
        else:
            current = text[current_start : start + len(para)]
    if current:
        chunks.append((current_start or 0, current))
    return chunks


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase, unify quotes/dashes, collapse whitespace; keep a map back to original offsets."""
    out, mapping = [], []
    prev_space = False
    for i, ch in enumerate(text.translate(_QUOTES)):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            mapping.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            mapping.append(i)
            prev_space = False
    return "".join(out), mapping


def locate(span: str, text: str, hint_start: int = 0) -> tuple[int, int] | None:
    """Find where an extracted claim lives in the document (exact, normalized, then fuzzy)."""
    span = (span or "").strip()
    if not span:
        return None
    idx = text.find(span, max(0, hint_start - 50))
    if idx == -1:
        idx = text.find(span)
    if idx != -1:
        return idx, idx + len(span)

    norm_text, mapping = _norm_with_map(text)
    norm_span, _ = _norm_with_map(span)
    idx = norm_text.find(norm_span)
    if idx != -1:
        end = idx + len(norm_span) - 1
        return mapping[idx], mapping[end] + 1

    # Fuzzy: best-matching sentence (or pair of sentences) in the document.
    sentences = split_sentences(text)
    best, best_ratio = None, 0.0
    for i, (s, e) in enumerate(sentences):
        for j in (i, min(i + 1, len(sentences) - 1)):
            cand = text[s : sentences[j][1]]
            ratio = difflib.SequenceMatcher(None, cand.lower(), span.lower()).ratio()
            if ratio > best_ratio:
                best, best_ratio = (s, sentences[j][1]), ratio
    if best and best_ratio >= 0.6:
        s, e = best
        while s < e and text[s].isspace():
            s += 1
        return s, e
    return None
