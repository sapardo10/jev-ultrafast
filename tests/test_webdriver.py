import json
import urllib.error

import pytest

from jev_ultrafast import browser, mcp_server, webdriver
from jev_ultrafast.webdriver import ELEMENT_KEY, WebDriver, WebDriverBrowser, WebDriverError


class FakeDriver:
    """Records WebDriver traffic; returns canned execute results in order."""

    def __init__(self, results=()):
        self.calls, self.results = [], list(results)

    def call(self, method, path, body=None, timeout=60):
        self.calls.append((method, path, body))
        return None

    def execute(self, script, *args):
        self.calls.append(("EXEC", script[:30], args))
        return self.results.pop(0) if self.results else None

    def execute_async(self, script, *args, timeout=30):
        return None


def make(driver):
    page = WebDriverBrowser.__new__(WebDriverBrowser)
    page.driver, page.after_input = driver, None
    page.fresh = lambda *a, **k: True
    return page


def test_open_browser_picks_webdriver_only_for_ios(monkeypatch):
    built = []
    monkeypatch.setattr(webdriver, "WebDriverBrowser", lambda url: built.append(url) or "ios")
    monkeypatch.setattr(browser, "Browser", lambda url: "cdp")
    monkeypatch.delenv("JEV_IOS_UDID", raising=False)
    assert browser.open_browser("u") == "cdp"
    monkeypatch.setenv("JEV_IOS_UDID", "U")
    assert browser.open_browser("u") == "ios" and built == ["u"]


def test_ensure_ios_requires_udid_and_booted_simulator(monkeypatch):
    with pytest.raises(WebDriverError, match="JEV_IOS_UDID"):
        webdriver.ensure_ios({})
    monkeypatch.setattr(webdriver, "booted", lambda udid: False)
    with pytest.raises(WebDriverError, match="not booted"):
        webdriver.ensure_ios({"JEV_IOS_UDID": "U"})


def test_ensure_ios_reports_appium_down(monkeypatch):
    monkeypatch.setattr(webdriver, "booted", lambda udid: True)

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(webdriver.urllib.request, "urlopen", refuse)
    with pytest.raises(WebDriverError, match="Appium running"):
        webdriver.ensure_ios({"JEV_IOS_UDID": "U", "JEV_WEBDRIVER_URL": "http://127.0.0.1:1"})


def test_dead_session_is_a_clear_error(monkeypatch):
    class Gone(urllib.error.HTTPError):
        def read(self, *a):
            return json.dumps({"value": {"error": "invalid session id", "message": "x"}}).encode()

    def boom(*args, **kwargs):
        raise Gone("u", 404, "nf", {}, None)

    monkeypatch.setattr(webdriver.urllib.request, "urlopen", boom)
    with pytest.raises(WebDriverError, match="session is gone"):
        WebDriver("http://x", "s").call("GET", "/url")


def test_click_resolves_an_observed_node_then_clicks_that_element():
    driver = FakeDriver(results=[{ELEMENT_KEY: "el-1"}])
    make(driver).act({"id": "e1", "kind": "click", "node": 7}, {})
    assert ("POST", "/element/el-1/click", None) in driver.calls
    assert driver.calls[0][2][0]["node"] == 7  # selects by observed node id, never a selector


def test_fill_clears_then_types():
    driver = FakeDriver(results=[{ELEMENT_KEY: "el-2"}])
    make(driver).act({"id": "e2", "kind": "fill", "node": 3}, {}, text="BOG")
    paths = [c[1] for c in driver.calls if c[0] == "POST"]
    assert paths == ["/element/el-2/click", "/element/el-2/clear", "/element/el-2/value"]


def test_covered_target_is_stale_not_clicked():
    driver = FakeDriver(results=[None])
    with pytest.raises(browser.StalePage):
        make(driver).act({"id": "e1", "kind": "click", "node": 7}, {})
    assert not [c for c in driver.calls if c[0] == "POST"]


def test_scroll_runs_in_page():
    driver = FakeDriver()
    make(driver).act({"id": "scroll_down", "kind": "scroll", "delta": 400}, {})
    assert driver.calls[0][2][0] == 400


def test_ensure_chrome_ios_branch_does_not_start_chrome(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_server, "EXTERNAL_CDP_WS", None)
    monkeypatch.setenv("JEV_IOS_UDID", "U")
    monkeypatch.setattr(webdriver, "ensure_ios", lambda: seen.append(1))
    mcp_server.ensure_chrome()
    assert seen == [1]


def test_fill_survives_a_non_editable_click_target():
    class Picky(FakeDriver):
        def call(self, method, path, body=None, timeout=60):
            super().call(method, path, body, timeout)
            if path.endswith("/clear"):
                raise WebDriverError("WebDriver invalid element state: Element must be user-editable to clear")

    driver = Picky(results=[{ELEMENT_KEY: "el-3"}])
    make(driver).act({"id": "e3", "kind": "fill", "node": 3}, {}, text="MAD")
    assert driver.calls[-1][1] == "/element/el-3/value"


def test_explain_error_names_a_dead_simulator(monkeypatch):
    monkeypatch.setenv("JEV_IOS_UDID", "U")
    monkeypatch.setattr(webdriver, "booted", lambda udid: False)
    error = WebDriverError("WebDriver unknown error: Remote debugger is not connected")
    assert "shut down mid-task" in mcp_server.explain_error(error)
    monkeypatch.setattr(webdriver, "booted", lambda udid: True)
    assert "Safari closed" in mcp_server.explain_error(error)
