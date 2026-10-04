"""Offline contracts for MCP server preflight errors. No browser, no Ollama."""

import json
import os

import pytest

from jev_ultrafast import mcp_server


def test_ensure_model_reports_dead_ollama(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    with pytest.raises(mcp_server.JevError, match="ollama serve"):
        mcp_server.ensure_model()


def test_ensure_model_reports_missing_model(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"models": [{"name": "other:latest"}]}).encode()

    monkeypatch.setenv("DECISION_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "nimble")
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(mcp_server.JevError, match="not installed"):
        mcp_server.ensure_model()


def test_stale_profile_lock_is_cleared(tmp_path):
    (tmp_path / "SingletonLock").symlink_to("host-99999999")
    assert mcp_server.profile_locked(tmp_path) is False
    assert not (tmp_path / "SingletonLock").is_symlink()


def test_live_profile_lock_is_reported(tmp_path):
    (tmp_path / "SingletonLock").symlink_to(f"host-{os.getpid()}")
    assert mcp_server.profile_locked(tmp_path) is True


def test_ensure_chrome_refreshes_stale_websocket(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXTERNAL_CDP_WS", None)
    monkeypatch.setenv("BU_CDP_WS", "ws://stale")
    monkeypatch.setattr(mcp_server, "cdp_websocket", lambda *_a, **_k: "ws://fresh")
    mcp_server.ensure_chrome()
    assert os.environ["BU_CDP_WS"] == "ws://fresh"


def test_ensure_chrome_reports_locked_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "EXTERNAL_CDP_WS", None)
    monkeypatch.setattr(mcp_server, "cdp_websocket", lambda *_a, **_k: None)
    monkeypatch.setenv("JEV_CHROME_PROFILE", str(tmp_path))
    monkeypatch.setenv("JEV_CHROME", "/bin/true")
    (tmp_path / "SingletonLock").symlink_to(f"host-{os.getpid()}")
    with pytest.raises(mcp_server.JevError, match="in use"):
        mcp_server.ensure_chrome()


def test_tool_returns_structured_error(monkeypatch):
    def boom(*_a):
        raise mcp_server.JevError("Ollama is not reachable")

    monkeypatch.setattr(mcp_server, "_browser_task", boom)
    out = json.loads(mcp_server.browser_task("https://x.test", "g"))
    assert out["status"] == "error" and "Ollama is not reachable" in out["error"]


class FakeAgent:
    def __init__(self, *_a, **_k):
        self.page = {"url": "u", "title": "t", "text": "x" * 9000, "screenshot": None}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def snapshot(self):
        return {"status": "done", "page": self.page, "elapsed_ms": 1, "history": []}

    def run(self):
        return iter(())


def run_fake(monkeypatch, **kwargs):
    from jev_ultrafast import agent

    monkeypatch.setattr(agent, "Agent", FakeAgent)
    for name in ("load_env", "ensure_model", "ensure_chrome"):
        monkeypatch.setattr(mcp_server, name, lambda: None)
    return json.loads(mcp_server.browser_task("https://x.test", "read the price", **kwargs))


def test_return_text_exposes_capped_page_text(monkeypatch):
    out = run_fake(monkeypatch, return_text=True)
    assert out["status"] == "done" and len(out["page_text"]) == mcp_server.MAX_PAGE_TEXT


def test_page_text_is_omitted_by_default(monkeypatch):
    assert "page_text" not in run_fake(monkeypatch)


def test_browser_waits_for_async_content_after_load(monkeypatch):
    # Regression: Google Flights' date grid showed placeholder prices because only readyState was awaited.
    from jev_ultrafast import browser

    calls = []

    def fake_cdp(method, **params):
        calls.append((method, params.get("expression", "")))
        return {"targetId": "t", "sessionId": "s", "result": {"value": "complete"}}

    monkeypatch.setattr(browser, "cdp", fake_cdp)
    monkeypatch.setattr(browser, "ensure_daemon", lambda: None)
    browser.Browser("https://x.test")
    settle = [c for c in calls if "MutationObserver" in c[1]]
    assert settle and calls.index(settle[0]) > max(i for i, c in enumerate(calls) if c[1] == "document.readyState")
