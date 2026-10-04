"""MCP server: let any coding agent drive a real browser through this agent."""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .android import AndroidError, diagnose
from .webdriver import WebDriverError

REPO = Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
EXTERNAL_CDP_WS = os.environ.get("BU_CDP_WS")  # user-supplied; never overwritten
MAX_PAGE_TEXT = 4000
SCREENSHOT_DIR = Path.home() / ".cache" / "jev" / "screenshots"


def load_env():
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if value:
                os.environ.setdefault(key, value)


def cdp_websocket(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return json.load(response)["webSocketDebuggerUrl"]
    except Exception:
        return None


class JevError(RuntimeError):
    """A failure with an actionable message for the calling agent."""


def profile_locked(profile):
    """True when another live Chrome owns the profile (SingletonLock points at a running pid)."""
    lock = Path(profile) / "SingletonLock"
    if not lock.is_symlink():
        return False
    try:
        pid = int(os.readlink(lock).rsplit("-", 1)[1])
        os.kill(pid, 0)
    except (OSError, ValueError, IndexError):
        try:
            lock.unlink()  # stale lock from a crashed Chrome
        except OSError:
            pass
        return False
    return True


def ensure_android_chrome(serial):
    """Attach to Chrome on an Android device: JEV_ANDROID_SERIAL=emulator-5554 [JEV_CDP_PORT=9444]."""
    from .android import ensure_android

    port = int(os.environ.get("JEV_CDP_PORT", "9444"))
    websocket = ensure_android(
        serial, port, cdp_websocket, boot_timeout=int(os.environ.get("JEV_ANDROID_BOOT_TIMEOUT", "120"))
    )
    os.environ["BU_CDP_WS"] = websocket


def ensure_chrome():
    if EXTERNAL_CDP_WS:
        return
    if os.environ.get("JEV_IOS_UDID"):
        from .webdriver import ensure_ios

        ensure_ios()  # validates simulator + Appium + Safari session up front, with a clear error
        return
    serial = os.environ.get("JEV_ANDROID_SERIAL")
    if serial:
        return ensure_android_chrome(serial)
    port = int(os.environ.get("JEV_CDP_PORT", "9333"))
    # Re-resolve every call: a cached websocket URL goes stale when Chrome restarts.
    websocket = cdp_websocket(port, 1.0)
    if not websocket:
        binary = os.environ.get("JEV_CHROME") or next(
            (path for path in CHROME_CANDIDATES if Path(path).exists()), None
        )
        if not binary:
            raise JevError("Chrome not found; install Chrome or set JEV_CHROME.")
        profile = Path(os.environ.get("JEV_CHROME_PROFILE", str(Path.home() / ".cache" / "jev" / "chrome")))
        profile.mkdir(parents=True, exist_ok=True)
        if profile_locked(profile):
            raise JevError(
                f"Chrome profile {profile} is in use by a Chrome that has no debugging port {port}. "
                "Quit that Chrome or set JEV_CHROME_PROFILE to another directory."
            )
        arguments = [
            binary,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
        ]
        if os.environ.get("JEV_HEADED") != "1":
            arguments.append("--headless=new")
        subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            websocket = cdp_websocket(port)
            if websocket:
                break
            time.sleep(0.2)
    if not websocket:
        raise JevError(
            f"Chrome remote debugging did not start on port {port} within 20s (port busy or profile locked?)."
        )
    os.environ["BU_CDP_WS"] = websocket
    os.environ.setdefault("BU_NAME", "jev-mcp")


LOST_CONNECTION = ("no close frame", "timed out", "connection", "broken pipe", "target closed", "websocket")


def explain_error(error):
    """A browser/device that vanished mid-task surfaces as a raw websocket error; say what it means."""
    if isinstance(error, (JevError, AndroidError)):
        return None
    udid = os.environ.get("JEV_IOS_UDID")
    if udid and isinstance(error, WebDriverError):
        from .webdriver import booted

        if not booted(udid):
            return f"Simulator {udid} shut down mid-task; boot it with `xcrun simctl boot {udid}` and retry."
        if "remote debugger" in str(error).lower() or "session is gone" in str(error):
            return "Safari closed or crashed mid-task (WebDriver lost its page); retry the task."
        return None
    if not any(marker in f"{type(error).__name__} {error}".lower() for marker in LOST_CONNECTION):
        return None
    if os.environ.get("JEV_IOS_UDID"):
        from .webdriver import ensure_ios

        ensure_ios()  # validates simulator + Appium + Safari session up front, with a clear error
        return
    serial = os.environ.get("JEV_ANDROID_SERIAL")
    if serial:
        try:
            cause = diagnose(serial)
        except Exception:
            cause = None
        if cause:
            return cause
    if "timed out" in f"{error}".lower():
        return "The page or device stopped answering CDP calls (page never settled, or the device hung); retry."
    return "The browser connection was lost mid-task (browser closed or crashed); retry the task."


def ensure_model():
    """Fail fast with a clear message when the decision model is unreachable or missing."""
    if os.environ.get("DECISION_BACKEND") != "ollama":
        base = os.environ.get("LOCAL_LLM_BASE_URL")
        if base:
            try:
                urllib.request.urlopen(base.rstrip("/") + "/models", timeout=1.5)
            except Exception:
                os.environ.pop("LOCAL_LLM_BASE_URL", None)
        return
    base = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL") or "nimble"
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as response:
            names = [m["name"] for m in json.load(response)["models"]]
    except Exception as error:
        raise JevError(
            f"Ollama is not reachable at {base} ({type(error).__name__}); start it with `ollama serve`."
        ) from None
    if model not in names and f"{model}:latest" not in names:
        raise JevError(f"Ollama model '{model}' is not installed (have: {', '.join(names) or 'none'}).")


mcp = MCPServer(
    "jev",
    instructions=(
        "Drive a real browser through the jev agent. Prefer browser_task for tasks "
        "that require interacting with a website in multiple steps."
    ),
)


@mcp.tool()
def browser_task(
    url: str, goal: str, max_steps: int = 25, screenshot: bool = False, return_text: bool = False
) -> str:
    """Drive a real browser with a natural-language goal and return a JSON trace.

    Use this for tasks that need a browser: filling forms, searching, booking,
    logging in, clicking through multi-step flows. The agent observes the
    accessible elements, decides one action at a time, and stops when the goal
    is satisfied or it cannot progress. Use it in any project; browser and
    model start automatically. Args: url is the starting page, goal is the full
    natural-language task, max_steps caps the actions (default 25), screenshot
    saves a PNG of the final page and returns its path, return_text adds the final
    page's visible text as page_text. Use return_text when the goal is to find or read
    something (a price, a status): status "done" means the agent stopped acting, not
    that the answer is correct, so check page_text or the screenshot yourself."""
    try:
        return _browser_task(url, goal, max_steps, screenshot, return_text)
    except Exception as error:
        import traceback

        result = {
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc()[-1500:],
        }
        hint = explain_error(error)
        if hint:
            result["hint"] = hint
        return json.dumps(result, indent=1)


def _browser_task(url, goal, max_steps, screenshot, return_text=False):
    load_env()
    ensure_model()
    ensure_chrome()
    from .agent import Agent

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with Agent(url, goal, screenshots=screenshot) as agent:
        state = agent.snapshot()
        deadline = time.monotonic() + float(os.environ.get("JEV_TASK_TIMEOUT", "240"))
        timed_out = False
        for state in agent.run():
            if len(state["history"]) >= max_steps:
                break
            if time.monotonic() > deadline:
                timed_out = True
                break
        state = agent.snapshot()
        result = {
            "status": "timeout" if timed_out else state["status"],
            "url": state["page"]["url"],
            "title": state["page"]["title"],
            "elapsed_ms": state["elapsed_ms"],
            "actions": [
                {
                    "step": entry["step"],
                    "action": entry["action"],
                    "text": entry["text"],
                    "page_changed": entry["page_changed"],
                }
                for entry in state["history"]
            ],
        }
        if return_text:
            result["page_text"] = state["page"]["text"][:MAX_PAGE_TEXT]
        if screenshot and state["page"].get("screenshot"):
            import base64

            path = SCREENSHOT_DIR / f"{int(time.time() * 1000)}.jpg"
            path.write_bytes(base64.b64decode(state["page"]["screenshot"]))
            result["screenshot"] = str(path)
    return json.dumps(result, indent=1)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
