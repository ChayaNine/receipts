"""Runs the REAL Token Factory + Tavily clients against simulated HTTP servers (respx).

This checks request shapes, auth headers, model-ID resolution, optional-parameter fallbacks,
reasoning-text stripping and the full pipeline, without needing network access or API keys.
"""
import asyncio
import json

import httpx
import pytest
import respx

from app import prompts
from app.config import Settings
from app.documents import clean_text
from app.llm import TokenFactory
from app.mock import SOURCES, MockTokenFactory, _key, _source_key
from app.pipeline import CheckRun
from app.samples import SAMPLES_BY_ID
from app.tavily import Tavily

BASE = "https://api.tokenfactory.nebius.com/v1"


def make_settings() -> Settings:
    s = Settings()
    s.nebius_api_key, s.tavily_api_key, s.mock = "nb-test", "tvly-test", False
    s.nebius_base_url = BASE
    return s


class FakeServers:
    def __init__(self):
        self.chat_calls = []
        self.tavily_calls = []
        self.rejected_json_mode = False
        self._mock_brain = MockTokenFactory(make_settings())

    def models(self, request):
        assert request.headers["authorization"] == "Bearer nb-test"
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "nvidia/nvidia-nemotron-3-nano-30b-a3b"},           # different casing than configured
            {"id": "nvidia/nemotron-3-super-120b-a12b"},
            {"id": "nvidia/Nemotron-3-Ultra-550B-A55B-fp8"},           # only the family matches
            {"id": "meta-llama/Llama-3.3-70B-Instruct"},
        ]})

    def chat(self, request):
        assert request.headers["authorization"] == "Bearer nb-test"
        body = json.loads(request.content)
        self.chat_calls.append(body)
        # Simulate a model that rejects JSON mode once, to exercise the fallback.
        if "super" in body["model"] and "response_format" in body and not self.rejected_json_mode:
            self.rejected_json_mode = True
            return httpx.Response(400, json={"error": "response_format is not supported for this model"})
        system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        if system == prompts.EXTRACT_SYSTEM.replace("MAX_CLAIMS", "30"):
            action = "extract"
        elif system == prompts.MATCH_SYSTEM:
            action = "match"
        elif system == prompts.JUDGE_SYSTEM:
            action = "judge"
        elif system == prompts.ESCALATE_SYSTEM:
            action = "decide" if "FIRST VERDICT (search agent)" in user else "second opinion"
        elif system == prompts.SUMMARY_SYSTEM:
            action = "write the report"
        else:
            raise AssertionError("unexpected prompt")
        reply = self._mock_brain._reply(action, user)
        content = json.dumps(reply)
        if action in ("judge", "second opinion"):
            content = "<think>Comparing the claim with the passages carefully.</think>\n```json\n" + content + "\n```"
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": 300},
        })

    def search(self, request):
        assert request.headers["authorization"] == "Bearer tvly-test"
        body = json.loads(request.content)
        self.tavily_calls.append(("search", body))
        assert body["search_depth"] in ("basic", "advanced") and body["query"]
        k = _source_key(_key(body["query"]))
        if k in SOURCES:
            title, url, content = SOURCES[k]
            results = [{"title": title, "url": url, "content": content, "score": 0.9}]
        else:
            results = [{"title": "Unrelated page", "url": "https://example.org/x", "content": "Nothing relevant.", "score": 0.1}]
        return httpx.Response(200, json={"query": body["query"], "results": results, "images": [], "answer": None,
                                         "response_time": 0.4, "usage": {"credits": 2}})

    def extract(self, request):
        assert request.headers["authorization"] == "Bearer tvly-test"
        body = json.loads(request.content)
        self.tavily_calls.append(("extract", body))
        url = body["urls"][0]
        hit = next((v for v in SOURCES.values() if v[1] == url), None)
        if not hit:
            return httpx.Response(200, json={"results": [], "failed_results": [{"url": url, "error": "blocked"}]})
        return httpx.Response(200, json={"results": [{"url": url, "raw_content": f"# {hit[0]}\n\n{hit[2]}"}],
                                         "failed_results": [], "usage": {"credits": 1}})


@pytest.fixture
def servers():
    fake = FakeServers()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models").mock(side_effect=fake.models)
        router.post(f"{BASE}/chat/completions").mock(side_effect=fake.chat)
        router.post("https://api.tavily.com/search").mock(side_effect=fake.search)
        router.post("https://api.tavily.com/extract").mock(side_effect=fake.extract)
        yield fake


def run_sample(sample_id: str):
    s = make_settings()
    events = []

    async def emit(e):
        events.append(e)

    async def go():
        llm = TokenFactory(s)
        await CheckRun(s, llm, Tavily(s, client=llm.client), emit).execute(clean_text(SAMPLES_BY_ID[sample_id]["text"]), "t")
    asyncio.run(go())
    return events


def test_model_ids_resolve_against_account(servers):
    events = run_sample("research-blog")
    models = next(e for e in events if e["type"] == "models")["models"]
    assert models["fast"]["id"] == "nvidia/nvidia-nemotron-3-nano-30b-a3b"
    assert models["judge"]["id"] == "nvidia/nemotron-3-super-120b-a12b"
    assert models["reason"]["id"] == "nvidia/Nemotron-3-Ultra-550B-A55B-fp8"


def test_full_pipeline_over_http(servers):
    events = run_sample("research-blog")
    results = {e["id"]: e for e in events if e["type"] == "claim_result"}
    claims = next(e for e in events if e["type"] == "claims")["claims"]
    verdicts = [results[c["id"]]["verdict"] for c in claims]
    assert verdicts == ["supported", "misrepresented", "supported", "not_found", "contradicted"]

    bleu = results[claims[1]["id"]]
    assert bleu["evidence_verified"] and "28.4 BLEU" in bleu["evidence"]
    assert bleu["judged_by"] == ["judge", "reason"]  # risky verdict escalated to Ultra
    assert "28.4" in bleu["corrected_claim"]
    assert results[claims[0]["id"]]["judged_by"] == ["judge"]  # confident + grounded: no escalation

    # JSON-mode fallback happened once and the request was retried without response_format.
    assert servers.rejected_json_mode
    # Fast model is asked not to think; judge replies with <think> blocks that get stripped.
    assert all(c.get("chat_template_kwargs") == {"enable_thinking": False}
               for c in servers.chat_calls if "nano" in c["model"])
    # Tavily Extract is called with the claim as a rerank query.
    extracts = [b for kind, b in servers.tavily_calls if kind == "extract"]
    assert extracts and all(b.get("query") and b.get("chunks_per_source") == 5 for b in extracts)

    summary = next(e for e in events if e["type"] == "summary")
    assert summary["score"] == 40 and summary["risk"] == "high"
    done = next(e for e in events if e["type"] == "done")
    assert done["stats"]["usage"]["by_role"]["judge"]["calls"] >= 4
    assert done["stats"]["tavily_credits"] > 0


def test_legal_memo_flags_invented_case(servers):
    events = run_sample("legal-memo")
    results = [e for e in events if e["type"] == "claim_result"]
    by_verdict = {r["verdict"] for r in results}
    assert "not_found" in by_verdict and "contradicted" in by_verdict
    fake = next(r for r in results if r["verdict"] == "not_found")
    assert fake["judged_by"] == ["fast", "reason"]


def test_unreadable_link_falls_back_to_search(servers):
    s = make_settings()
    events = []

    async def emit(e):
        events.append(e)

    text = ("Background reading matters for this report. Per the GPTZero investigation, at least 100 hallucinated "
            "citations appeared in NeurIPS 2025 papers (https://gptzero.example/broken-link).")

    async def go():
        llm = TokenFactory(s)
        await CheckRun(s, llm, Tavily(s, client=llm.client), emit).execute(text, "t")
    asyncio.run(go())
    kinds = [k for k, _ in servers.tavily_calls]
    assert kinds[:2] == ["extract", "search"]
    result = next(e for e in events if e["type"] == "claim_result")
    assert result["verdict"] == "supported" and result["source"]["url"].startswith("https://gptzero.me")


def test_retries_when_reasoning_exhausts_the_token_budget():
    s = make_settings()
    calls = []

    def chat(request):
        body = json.loads(request.content)
        calls.append(body["max_tokens"])
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "<think>long reasoning..."},
                                                          "finish_reason": "length"}], "usage": {}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 5}})

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={"data": []}))
        router.post(f"{BASE}/chat/completions").mock(side_effect=chat)
        reply = asyncio.run(TokenFactory(s).chat("judge", [{"role": "user", "content": "hi"}], max_tokens=3000))
    assert reply == {"ok": True} and calls == [3000, 6000]
