# Demo video script (target 2:40, hard limit 3:00)

Judges said they want the "aha" within 30 seconds and the required tools called out **out loud**.
Record the screen at 1440×900 or larger, browser zoom 110%, in a **live** run (not simulated).
Do one rehearsal, then record in one or two takes. Free tools: OBS Studio, or the macOS/Windows screen recorder.
No background music unless it is royalty-free and you have the right to use it.

**Say only what your screen shows.** Live model output can differ from this script (a verdict, a confidence, which claims went to Ultra). Adjust the narration to match your recorded run.

Before recording: open your deployed demo, click **Run it live again** on the legal memo once so the run you film is live, and close every other tab.

---

### 0:00–0:20 · The problem (on screen: the Receipts start page)

> "Lawyers are being sanctioned for citing cases that don't exist. US courts handed out at least a hundred and forty-five thousand dollars in sanctions for AI-fabricated citations in the first three months of this year, and fake references are slipping past peer reviewers at top AI conferences.
> The worst ones aren't even fake. They're real sources, cited for something they never said.
> This is Receipts. It checks whether your sources actually say what you say they say."

### 0:20–0:55 · The aha (click "AI-drafted legal memo")

Let the sentences light up. Don't talk over the first two seconds.

> "I'll check an AI-drafted legal memo. NVIDIA Nemotron 3 Nano, running on Nebius Token Factory, pulls out every sentence that cites a source. Tavily finds and reads each source.
> Mata v. Avianca: supported. But Varghese v. China Southern Airlines" (click the crimson sentence) "doesn't exist. It's one of the cases ChatGPT invented in the Mata case itself. Nemotron 3 Ultra looked at the search results and concluded there's no such case.
> And Marbury v. Madison is real, but the memo says it held the exact opposite of what it held."

### 0:55–1:35 · The subtle catch (New check → "Research blog post")

> "Now something a human reviewer would miss. This blog post cites the Transformer paper correctly, and then says it scored 41.8 BLEU on English-to-German."
> (open the Misrepresented card) "Receipts quotes the paper: 28.4 on English-to-German. 41.8 was English-to-French. And this green line matters: Receipts verified that quote appears word-for-word in the source, so the checker can't hallucinate its own evidence."
> (click **Apply to document**) "One click applies a rewrite the source actually supports."

### 1:35–2:10 · How it works (click "How it was checked")

> "Every model has one job. Nemotron 3 Nano does the fast extraction and matching. Nemotron 3 Super judges each claim against the source passages. Nemotron 3 Ultra only sees the risky or uncertain ones, gives a second opinion, and can overturn Super, and it writes the reviewer's note. All three run on Nebius Token Factory through one OpenAI-compatible API.
> Tavily Extract returns only the passages relevant to each claim, which keeps every check fast and cheap." (read the totals line: time and cost from your run)

### 2:10–2:30 · Your own document

Paste a paragraph or drop a PDF, let it start, then click **Export report**.

> "Paste text, a link, or drop a PDF or Word file. Export a report to attach to a filing or a review. It's fully keyboard accessible: J and K move between findings."

### 2:30–2:45 · Close

> "Receipts gives evidence, not a verdict you have to trust blindly. Every flag links to the source. Built with NVIDIA Nemotron on Nebius Token Factory and Tavily. Check your receipts before someone else does."

---

**Checklist for the upload**
- Under 3:00. Public (not unlisted) on YouTube.
- Title: `Receipts — checking AI citations with NVIDIA Nemotron on Nebius Token Factory`
- Description: one sentence, plus your demo URL and GitHub URL.
- Captions: turn on YouTube auto-captions and fix the model names (helps accessibility and judges watching muted).
