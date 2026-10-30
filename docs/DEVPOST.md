# Devpost submission text

Copy each section into the matching Devpost field. Replace everything in `<angle brackets>`,
and update any number marked **[from your run]** with what your live run actually shows.

---

## Project name
Receipts

## Tagline (Elevator pitch)
Paste a document and see, sentence by sentence, whether its sources actually say what it claims.

## Track
Best Apps and Agents

## About the project

### Inspiration
AI writing tools produce fluent text with confident footnotes, and the footnotes are where they fail quietly. In the first quarter of 2026 alone, US courts imposed at least $145,000 in sanctions for AI-fabricated citations, including a six-figure penalty in the Sixth Circuit. A federal court threw out an expert's report because it cited academic articles that don't exist. GPTZero found hallucinated references in 50 ICLR 2026 submissions that three to five peer reviewers had each missed.

The citations that worried us most weren't the invented ones. They were real sources cited for something they never said: the wrong number, the reversed holding, the quote with one word changed. Existing reference checkers mostly ask whether a reference exists or was retracted. Nobody answers the question a careful reader actually has: **does the source say that?**

### What it does
Receipts reads a document (pasted text, a link, a PDF or a Word file) and marks every sentence that relies on a source, the way a spell-checker marks a typo:

- **Supported**, **Partly supported**, **Misrepresented**, **Contradicted**, **Not found** (possibly invented) or **Can't verify** (paywalled or offline).
- Click a sentence to see the exact passage from the source, whether that quote was verified word-for-word, a rewrite the source does support, and which model made the call.
- **Apply to document** swaps in the corrected sentence; **Find a source that supports this** searches the web for a real replacement; **Export report** produces a standalone HTML report to attach to a filing or a review.
- A receipt in the sidebar "prints" a line per citation as verdicts arrive, totals how much of the document holds up, and stamps it. A reviewer's note from Nemotron 3 Ultra summarizes what to fix first.

It's built for lawyers and paralegals before filing, editors and fact-checkers before publishing, peer reviewers and teachers reading submissions, and students checking their own AI-assisted drafts. No account needed.

### How we built it
Receipts is an agent with three NVIDIA Nemotron models on **Nebius Token Factory**, each with one job, plus **Tavily** for finding and reading sources:

1. **Nemotron 3 Nano** extracts every cited claim: the exact sentence, the citation (resolving `[3]` or `(Smith, 2020)` against the bibliography), any quoted words, and a search query. It also decides which search result *is* the cited source rather than an article about it. These are many short structured calls, so it runs with thinking turned off.
2. **Tavily** reads the source. For links, **Tavily Extract with a `query`** returns only the passages most relevant to the claim being checked. For cases, papers and books, **Tavily Search** tries two formulations before anything is called "Not found".
3. **Nemotron 3 Super** judges each claim against those passages and must copy the exact sentences its verdict rests on.
4. **A grounding check** confirms those sentences really appear in the source. Ungrounded evidence lowers confidence. The document's own quotations are also checked word-for-word.
5. **Nemotron 3 Ultra** gives a second opinion on anything damaging (Not found, Contradicted, Misrepresented), low-confidence or ungrounded, can overturn Super, decides whether an unfindable source exists, and writes the reviewer's note. Ultra only sees those claims, which keeps the cost down. **[from your run]: Ultra reviewed X of Y claims; a check of the legal memo took Z seconds and about $N in tokens.**

Everything streams to the browser over Server-Sent Events, so you watch the document light up claim by claim. The **How it was checked** tab lists every model and Tavily call with timings and tokens.

The backend is Python (FastAPI + httpx) talking to Token Factory's OpenAI-compatible API; the front end is a single page of plain HTML, CSS and JavaScript with no build step. The demo is hosted on <Hugging Face Spaces / Nebius Serverless Endpoints>. The three sample documents replay recorded live runs (labelled on screen), so the demo keeps working even if API credits run out; pasted documents always run live.

### Where Token Factory accelerated our workflow
- One endpoint and one API key for three Nemotron sizes, so the Nano/Super/Ultra split was tuned by changing environment variables, not code.
- JSON mode for structured replies, with automatic fallback for any model that rejects it.
- `GET /v1/models` lets the app resolve each configured model ID against the account at startup, and `scripts/doctor.py` sends one request to each model so setup problems show up in seconds.

### Challenges we ran into
- **Making the checker trustworthy.** A citation checker that hallucinates is worse than none. Forcing the judge to quote its evidence and then verifying the quote against the source text was the single most important design decision.
- **"Not found" vs. "Can't verify".** Calling an honest author's citation fabricated is a serious harm. Damaging verdicts always get an Ultra second opinion, and Ultra is told a false accusation costs as much as a missed fabrication.
- **Mapping model output back to the page.** Models paraphrase even when told to copy, so claims are located in the document with exact, normalized and fuzzy matching.
- **Citation-aware sentence splitting.** "Mata v. Avianca, Inc., 678 F. Supp. 3d 443 (S.D.N.Y. 2023)" has six periods and is one sentence.

### Accomplishments that we're proud of
- It catches subtle misrepresentations, like a BLEU score swapped between two language pairs of the same paper, not just invented references.
- Every verdict shows the evidence, says whether that evidence was verified in the source, and names the model that decided.
- Fully keyboard accessible (Tab, Enter, J/K, Esc), screen-reader announcements for each verdict, never color alone, reduced-motion and dark-mode support, works on a phone.
- A test suite that runs the real Token Factory and Tavily clients against simulated servers.

### What we learned
<Write 2–4 sentences in your own words after your live runs. Examples of things worth saying: how Nano, Super and Ultra differed in practice; what surprised you about Tavily Extract's reranking; a failure case you found and how you handled it.>

### What's next for Receipts
- A Word and Google Docs add-in so checks happen where people write.
- Court-record and scholarly-database connectors (CourtListener, Crossref, OpenAlex) to reduce "Not found" false positives for obscure sources.
- Pinpoint checking against the exact cited page.
- A batch mode on Nebius Serverless Jobs for journals and conferences screening thousands of submissions.

### Limitations (published on purpose)
Receipts gives evidence, not a ruling. Paywalled and print-only sources usually come back as "Can't verify". A real but obscure unpublished opinion that only exists on Westlaw or Lexis can come back "Not found". Badly formatted citations can look like fabrications. Two runs can differ on borderline "Partly supported" cases. Only English has been tested. Full list in the README.

## Built with
nebius-token-factory, nvidia-nemotron, nemotron-3-nano, nemotron-3-super, nemotron-3-ultra, tavily, python, fastapi, httpx, javascript, html, css, server-sent-events, hugging-face-spaces

## Try it out (links)
- Demo: <your demo URL>
- Code: <your GitHub repo URL>

## Testing instructions for judges
1. Open <your demo URL>. No login needed.
2. Click **AI-drafted legal memo**. It replays a recorded live run instantly; click **Run it live again** in the banner to run it live.
3. Click any red or crimson sentence to see the source passage and the models that decided.
4. Open **How it was checked** to see every model and Tavily call.
5. Paste any text with citations (or a link to an article) and click **Check citations** for a live run. Live runs are limited to 12 per visitor per hour to protect credits.

## Video
<your YouTube URL>

## Is this an existing project?
No. Receipts was built from scratch during the submission period.

## Feedback on Nebius Token Factory, AI Cloud and NVIDIA tools
See docs/FEEDBACK.md. Paste it after you've edited it with your own experience.
