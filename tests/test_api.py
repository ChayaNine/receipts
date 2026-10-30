"""HTTP API tests: streaming in simulated mode, replay of recorded runs, and keyless behaviour."""
import importlib
import json

from fastapi.testclient import TestClient


def load_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_DELAY_SECONDS", "0")
    for key in ("RECEIPTS_MOCK", "NEBIUS_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.setenv(key, env.get(key, ""))
    import app.config
    import app.main
    importlib.reload(app.config)
    return importlib.reload(app.main)


def events_from(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_simulated_check_streams_to_done(monkeypatch, tmp_path):
    main = load_app(monkeypatch, tmp_path, RECEIPTS_MOCK="1")
    client = TestClient(main.app)
    cfg = client.get("/api/config").json()
    assert cfg["mode"] == "simulated" and len(cfg["samples"]) == 3
    resp = client.post("/api/check", json={"sample_id": "student-essay"})
    events = events_from(resp)
    types = [e["type"] for e in events]
    assert types[0] == "meta" and events[0]["mode"] == "simulated"
    assert "claims" in types and types[-1] == "done"
    assert not list(tmp_path.iterdir()), "simulated runs must never be cached as replays"


def test_keyless_server_replays_recorded_run(monkeypatch, tmp_path):
    main = load_app(monkeypatch, tmp_path)
    client = TestClient(main.app)
    sample = client.get("/api/samples/legal-memo").json()
    key = main._cache_key(sample["text"])
    recorded = [{"type": "claims", "claims": []}, {"type": "summary", "headline": "x", "score": None, "counts": {}},
                {"type": "done", "stats": {}}]
    (tmp_path / f"{key}.json").write_text(json.dumps({"recorded_at": "2026-10-29T10:00+00:00", "events": recorded}))
    assert client.get("/api/config").json()["samples"][0]["cached"] is True
    events = events_from(client.post("/api/check", json={"sample_id": "legal-memo"}))
    assert events[0]["mode"] == "replay" and events[0]["recorded_at"].startswith("2026-10-29")
    assert [e["type"] for e in events[1:]] == ["claims", "summary", "done"]


def test_keyless_server_explains_it_cannot_run_live(monkeypatch, tmp_path):
    main = load_app(monkeypatch, tmp_path)
    client = TestClient(main.app)
    events = events_from(client.post("/api/check", json={"text": "A long enough pasted paragraph that cites Smith v. Jones, 1 U.S. 1 (1990)."}))
    assert events[-1]["type"] == "error" and "API keys" in events[-1]["message"]
    assert client.post("/api/find-source", json={"claim": "x"}).status_code == 503


def test_rejects_too_short_input(monkeypatch, tmp_path):
    main = load_app(monkeypatch, tmp_path, RECEIPTS_MOCK="1")
    events = events_from(TestClient(main.app).post("/api/check", json={"text": "too short"}))
    assert events[-1]["type"] == "error"
