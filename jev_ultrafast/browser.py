"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from ._harness import cdp, ensure_daemon

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# Resolve once the DOM has been quiet for `quiet` ms, or after `limit` ms. Async content (price grids,
# graphs, panels) renders after readyState is complete, so "loaded" is not "ready".
SETTLE = """((quiet,limit)=>new Promise(resolve=>{
  let timer; const done=()=>{observer.disconnect();clearTimeout(timer);resolve()};
  const arm=()=>{clearTimeout(timer);timer=setTimeout(done,quiet)};
  const observer=new MutationObserver(arm);
  observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
  arm(); setTimeout(done,limit);
}))"""


MOBILE_SETTLE = (1500, 10000)  # quiet ms, limit ms


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


def open_browser(url):
    """CDP Chrome (desktop or Android) by default; iOS Safari over WebDriver when JEV_IOS_UDID is set."""
    if os.environ.get("JEV_IOS_UDID"):
        from .webdriver import WebDriverBrowser

        return WebDriverBrowser(url)
    return Browser(url)


def is_mobile_ua(user_agent):
    """Phone/tablet browsers get real viewport metrics and touch input, never the desktop override."""
    return bool(re.search(r"Android|iPhone|iPad|Mobile", user_agent or ""))


class Browser:
    def __init__(self, url):
        ensure_daemon()
        self.mobile = is_mobile_ua(cdp("Browser.getVersion").get("userAgent"))
        # A phone only paints its foreground tab: screenshots and touch input on a background tab hang.
        self.target = cdp("Target.createTarget", url="about:blank", background=not self.mobile)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        if not self.mobile:
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)
        # A phone renders several times slower than a desktop: wait for a longer quiet window before observing.
        self.settle(*MOBILE_SETTLE) if self.mobile else self.settle()

    def settle(self, quiet=500, limit=6000):
        """Wait for async content to stop changing; never fatal, the observe loop handles the rest."""
        try:
            self.call(
                "Runtime.evaluate",
                expression=f"{SETTLE}({int(quiet)},{int(limit)})",
                awaitPromise=True,
                returnByValue=True,
            )
        except RuntimeError:
            pass

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false, quietTimer;
                      const finish=()=>{stopped=true;observer.disconnect();resolve()};
                      // Esri-style panels animate in after the click; wait for the DOM to go quiet.
                      const quiet=()=>{clearTimeout(quietTimer);if(!autocomplete)quietTimer=setTimeout(finish,300)};
                      const observer=new MutationObserver(quiet);
                      observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
                      quiet();
                      setTimeout(finish,autocomplete ? 1200 : 1500);
                      const ready=()=>{
                        if (stopped || !autocomplete) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot, "mobile": self.mobile}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

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
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation(
            {"operation": "act", "session": self.session, "action": action, "text": text, "mobile": self.mobile}
        )
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def screenshot_scale(dpr):
    """clip.scale that yields a CSS-pixel-sized image on a device with this pixel ratio."""
    try:
        return round(1 / float(dpr), 4) if float(dpr) > 0 else 1
    except (TypeError, ValueError):
        return 1


def select_all_modifier(mobile):
    """Ctrl on a phone (Android has no Cmd), Cmd on a Mac desktop."""
    return 4 if sys.platform == "darwin" and not mobile else 2


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]
    mobile = bool(request.get("mobile"))

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            at = action.get("at")
            if mobile:
                at = at or evaluate("({x:Math.round(innerWidth/2),y:Math.round(innerHeight/2)})")
                call(
                    "Input.synthesizeScrollGesture",
                    x=at["x"], y=at["y"], yDistance=-action["delta"], gestureSourceType="touch", speed=1500,
                )
            else:
                at = at or {"x": 550, "y": 650}
                wheel = {"x": at["x"], "y": at["y"], "deltaX": 0, "deltaY": action["delta"]}
                call("Input.dispatchMouseEvent", type="mouseWheel", **wheel)
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              // Phones: the on-screen keyboard shrinks the visual viewport, so bring the target into it first
              // and report touch coordinates in visual-viewport space.
              const vv=action.mobile ? window.visualViewport : null;
              const W=vv ? vv.width : innerWidth, H=vv ? vv.height : innerHeight;
              const inView=b=>b.top>=0 && b.bottom<=H && b.left>=0 && b.right<=W;
              if (vv && !inView(e.getBoundingClientRect()))
                e.scrollIntoView({block:'center',inline:'center'});
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=W || y>=H) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return vv ? {x:(x-vv.offsetLeft)*vv.scale,y:(y-vv.offsetTop)*vv.scale} : {x,y};
            })(""" + json.dumps({**action, "mobile": mobile}) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                if mobile:
                    call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
                    call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
                else:
                    for event in ("mousePressed", "mouseReleased"):
                        call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=select_all_modifier(mobile),
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=select_all_modifier(mobile),
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        shot = {"format": "jpeg", "quality": 72}
        if mobile:
            # Capture the visual viewport (what the user sees, keyboard included) in CSS pixels, so image
            # coordinates equal touch coordinates: Chrome multiplies clip.scale by the device pixel ratio.
            metrics = call("Page.getLayoutMetrics")["cssVisualViewport"]
            shot["clip"] = {
                "x": metrics["pageX"], "y": metrics["pageY"],
                "width": metrics["clientWidth"], "height": metrics["clientHeight"],
                "scale": screenshot_scale(evaluate("window.devicePixelRatio")),
            }
        info["screenshot"] = call("Page.captureScreenshot", **shot)["data"]
    return info
