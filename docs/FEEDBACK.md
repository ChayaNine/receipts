# Feedback on Nebius Token Factory, AI Cloud, NVIDIA Nemotron and Tavily

This is a draft. The points marked **Observed while building** are things we ran into while writing Receipts.
Sections marked **[FILL IN]** must come from your own experience: the organizers judge feedback on how complete,
specific and actionable it is (there are ten $100 prizes for it), and it has to be true.
Delete this paragraph before pasting.

---

## What we used
- **Nebius Token Factory**: OpenAI-compatible chat completions, `GET /v1/models`, JSON mode.
- **NVIDIA Nemotron 3 Nano 30B-A3B** for claim extraction and search-result matching (thinking off), **Nemotron 3 Super 120B-A12B** for judging claims against sources, **Nemotron 3 Ultra 550B-A55B** for second opinions and the final report.
- **Tavily** Search (advanced) and Extract (with `query` reranking).
- <Nebius Serverless Endpoints, if you deployed there>

## Onboarding: zero to first successful call  [FILL IN]
- How long from sign-up to the first 200 response: <minutes>
- Claiming the $25 credits with the hackathon code: <smooth / any friction>
- Creating the API key and finding the right model in the playground: <what was easy, what wasn't>

## Token Factory: what worked well
- **One API, three model sizes.** Swapping which Nemotron handles a role is an environment variable change. That made it practical to give each model one job and tune the split by cost and latency.
- **OpenAI compatibility.** Plain HTTP with a bearer token and the standard chat-completions body worked without a vendor SDK.
- **`GET /v1/models`** made it possible to verify at startup that every configured model exists on the account. We turned that into a `doctor` script that catches setup mistakes in seconds. [FILL IN: anything else that worked well for you, e.g. latency, uptime, the playground]

## Token Factory: what needs work (specific and actionable)
1. **Two base URLs, no guidance on which to use. (Observed while building.)** The Token Factory quickstart uses `https://api.tokenfactory.nebius.com/v1/`, while the Nemotron landing page and the Nemotron cookbook pages use `https://api.tokenfactory.us-central1.nebius.com/v1/`. Neither page explains whether keys are regional or which one a new user should pick. **Suggestion:** one canonical base URL in all examples, and a short "Regions" note saying when the regional URL is needed.
2. **Inconsistent model-ID casing. (Observed while building.)** The official Nemotron cookbook lists `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b` and `nvidia/Nemotron-3-Ultra-550b-a55b`: three different casing conventions in one table, and third-party listings differ again. It's not documented whether IDs are case-sensitive, so we built case-insensitive and family-name resolution against `/v1/models` to be safe. **Suggestion:** lowercase canonical IDs everywhere, accept aliases, and say in the API reference whether matching is case-sensitive. [FILL IN: did any ID fail for you? which one worked?]
3. **No documentation for controlling Nemotron reasoning. (Observed while building.)** We couldn't find a docs page on turning thinking on or off for Nemotron 3 (the docs index has pages for JSON mode and rate limits, but none for reasoning models). We guessed `chat_template_kwargs: {"enable_thinking": false}` and built a fallback in case it's rejected, plus stripping for inline `<think>` blocks. **Suggestion:** a "Reasoning models" page covering the toggle parameter per model family, where reasoning text is returned (`content` vs. a separate field), whether reasoning tokens are billed, and recommended `max_tokens` so answers aren't truncated by long reasoning. [FILL IN: what actually happened with the toggle on your account]
4. **JSON-mode support per model isn't discoverable. (Observed while building.)** The JSON mode page says to look for a "JSON mode" tag on the model card. That isn't available programmatically, so we handle rejection at runtime. **Suggestion:** expose `supported_features` (it appears in the rich `/v1/models` schema) consistently, including `json_object`, `json_schema` and `reasoning_toggle`.
5. **Quickstart examples don't use Nemotron.** For a Nemotron-focused hackathon the quickstart's example model is a DeepSeek model. A Nemotron example plus a `list models` call would shorten the path to a first working request.

## NVIDIA Nemotron 3 models  [FILL IN after your live runs]
For each model, one or two specific sentences:
- **Nano:** did it copy claim sentences verbatim as instructed? How often did it need the JSON repair retry? Latency per call?
- **Super:** quality of verdicts; did it quote evidence exactly? Typical latency and tokens per judgment?
- **Ultra:** did it overturn Super, and was it right when it did? Latency of long reasoning? Cost compared with Super?
- **Would you build with them again, and why?**

## Tavily
- **Worked well:** Extract's `query` + `chunks_per_source` reranking is exactly what claim checking needs. It returns the passages that matter, which kept judge prompts short. Search at advanced depth found court opinions and arXiv pages reliably for well-formed citations. [FILL IN: confirm from your runs]
- **Would help:** (a) a relevance score and character offsets for each reranked chunk, so apps can link to the exact passage; (b) an option to return the full page text and the top chunks in one call, because we sometimes need both (top chunks for the judge, full text to verify a quotation); (c) the docs note that `include_usage` can report 0 until five extractions, which makes per-request cost tracking confusing; a per-request figure from the start would be clearer.

## Nebius AI Cloud / Serverless  [FILL IN only if you used it]
- What you deployed, how long it took, and what was confusing (for example: endpoint authentication defaults, passing environment variables, CPU preset choice).

## Would we build with this stack again?
[FILL IN: yes/no and the single most important reason]
