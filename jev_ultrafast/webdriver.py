"""iOS Safari through W3C WebDriver (Appium XCUITest), same Browser interface as the CDP path.

Safari on iOS does not speak CDP. The DOM observation (snapshot.js) and the typed action space are unchanged;
only the transport differs: observe = execute script + screenshot, act = element click/keys on elements that
jev's own observation numbered, never model-written selectors.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .browser import MARKER, MOBILE_SETTLE, READ_STATE, SETTLE, StalePage, fingerprint

ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
SESSION_DIR = Path.home() / ".cache" / "jev"


class WebDriverError(RuntimeError):
    """The WebDriver server, the simulator, or Safari on it is not in a usable state."""


class WebDriver:
    def __init__(self, base, session=None):
        self.base, self.session = base.rstrip("/"), session

    def request(self, method, path, body=None, timeout=60):
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response).get("value")
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get("value", {})
            except Exception:
                detail = {}
            message = detail.get("message") or str(error)
            kind = detail.get("error", "")
            if kind in {"invalid session id", "no such window"}:
                raise WebDriverError(
                    f"WebDriver session is gone ({kind}); Safari was closed or the simulator shut down."
                ) from None
            raise WebDriverError(f"WebDriver {kind or error.code}: {message[:300]}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise WebDriverError(
                f"WebDriver server {self.base} did not answer ({type(error).__name__}); "
                "is Appium running and the simulator booted?"
            ) from None

    def call(self, method, path, body=None, timeout=60):
        return self.request(method, f"/session/{self.session}{path}", body, timeout)

    def execute(self, script, *args):
        return self.call("POST", "/execute/sync", {"script": script, "args": list(args)})

    def execute_async(self, script, *args, timeout=30):
        return self.call("POST", "/execute/async", {"script": script, "args": list(args)}, timeout=timeout)


def booted(udid):
    out = subprocess.run(["xcrun", "simctl", "list", "devices", "booted"], capture_output=True, text=True).stdout
    return udid in out


def capabilities(udid, env):
    return {
        "platformName": "iOS",
        "appium:automationName": "XCUITest",
        "appium:udid": udid,
        "appium:browserName": "Safari",
        "appium:newCommandTimeout": 900,
        "appium:wdaLaunchTimeout": 600000,
        "appium:safariIgnoreFraudWarning": True,
        **json.loads(env.get("JEV_WEBDRIVER_CAPS") or "{}"),
    }


def ensure_ios(env=os.environ, create_timeout=600):
    """Return a live WebDriver for JEV_IOS_UDID on Appium at JEV_WEBDRIVER_URL, reusing a cached session."""
    udid, base = env.get("JEV_IOS_UDID"), env.get("JEV_WEBDRIVER_URL", "http://127.0.0.1:4723")
    if not udid:
        raise WebDriverError("Set JEV_IOS_UDID to the simulator to drive (`xcrun simctl list devices booted`).")
    if not booted(udid):
        raise WebDriverError(f"Simulator {udid} is not booted; run `xcrun simctl boot {udid}`.")
    driver = WebDriver(base)
    driver.request("GET", "/status", timeout=5)  # fails fast with a clear message when Appium is down
    cache = SESSION_DIR / f"ios-{udid}.session"
    if cache.exists():
        driver.session = cache.read_text().strip()
        try:
            driver.call("GET", "/url", timeout=10)
            return driver
        except WebDriverError:
            driver.session = None
    try:
        created = driver.request(
            "POST",
            "/session",
            {"capabilities": {"alwaysMatch": capabilities(udid, env)}},
            timeout=create_timeout,
        )
    except WebDriverError as error:
        raise WebDriverError(f"Could not start a Safari session on {udid}: {error}") from None
    driver.session = created["sessionId"]
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(driver.session)
    return driver


TARGET = """(action => {
  const e=window.__jevFast?.nodes.get(action.node);
  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
  if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
  const vv=window.visualViewport, W=vv?vv.width:innerWidth, H=vv?vv.height:innerHeight;
  let r=e.getBoundingClientRect();
  if (r.top<0 || r.bottom>H || r.left<0 || r.right>W) { e.scrollIntoView({block:'center',inline:'center'}); }
  r=e.getBoundingClientRect(); const x=r.x+r.width/2, y=r.y+r.height/2;
  if (!r.width || !r.height || x<0 || y<0 || x>=W || y>=H) return null;
  if (!e.contains(document.elementFromPoint(x,y))) return null;
  if (action.kind==='select') {
    if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
        !o.disabled && !o.closest('optgroup[disabled]'))) return null;
    e.value=action.value;
    e.dispatchEvent(new Event('input',{bubbles:true}));
    e.dispatchEvent(new Event('change',{bubbles:true}));
  }
  return e;
})(arguments[0])"""

SCROLL = """((delta,at)=>{
  let e=at ? document.elementFromPoint(at.x,at.y) : null;
  while (e && e!==document.body && !(e.scrollHeight>e.clientHeight+2 &&
         ['auto','scroll'].includes(getComputedStyle(e).overflowY))) e=e.parentElement;
  if (e && e!==document.body) e.scrollBy(0,delta); else window.scrollBy(0,delta);
})(arguments[0],arguments[1])"""


class WebDriverBrowser:
    """Drop-in for browser.Browser over WebDriver."""

    mobile = True

    def __init__(self, url, driver=None):
        self.driver = driver or ensure_ios()
        self.after_input = None
        self.driver.call("POST", "/url", {"url": url}, timeout=60)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.1)
        self.settle(*MOBILE_SETTLE)

    def settle(self, quiet=500, limit=6000):
        try:
            self.driver.execute_async(f"{SETTLE}({int(quiet)},{int(limit)}).then(arguments[arguments.length-1])")
        except WebDriverError:
            pass

    def evaluate(self, expression):
        try:
            return self.driver.execute(f"return ({expression});")
        except WebDriverError as error:
            if "session is gone" in str(error) or "did not answer" in str(error):
                raise
            raise StalePage("Document changed during evaluation") from None

    def observe(self, screenshot=True):
        if self.after_input:
            self.after_input = None
            self.settle(quiet=300, limit=1500)
        for attempt in range(10):
            try:
                info = self.driver.execute(f"return ({READ_STATE});")
                break
            except WebDriverError as error:
                if "session is gone" in str(error) or "did not answer" in str(error) or attempt == 9:
                    raise
                time.sleep(0.1)
        if info is None:
            raise StalePage("Document is navigating")
        info["fingerprint"] = fingerprint(info)
        if screenshot:
            info["screenshot"] = self.driver.call("GET", "/screenshot")
        return info

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        kind = action["kind"]
        if kind == "wait":
            time.sleep(0.1)
            return {"executed": action["id"]}
        if kind == "scroll":
            self.driver.execute(SCROLL, action["delta"], action.get("at"))
        else:
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            element = self.driver.execute(f"return {TARGET};", action)
            if not element:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                ref = element[ELEMENT_KEY]
                self.driver.call("POST", f"/element/{ref}/click")
                if kind == "fill":
                    try:
                        self.driver.call("POST", f"/element/{ref}/clear")
                    except WebDriverError as error:
                        # Comboboxes are often a div that opens a real input after the click; typing still works.
                        if "user-editable" not in str(error):
                            raise
                    self.driver.call("POST", f"/element/{ref}/value", {"text": text, "value": list(text)})
        self.after_input = action
        return {"executed": action["id"]}

    def close(self):
        # The Safari session is shared across calls (creating one builds WebDriverAgent, ~1 min); keep it.
        pass
