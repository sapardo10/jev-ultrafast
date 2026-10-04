import pytest

from jev_ultrafast import android, browser, mcp_server
from jev_ultrafast._harness import daemon_name
from jev_ultrafast.android import AndroidError

DEVICES = "List of devices attached\nemulator-5554\tdevice\nemulator-5556\toffline\nR58\tunauthorized\n\n"


def test_parse_devices():
    assert android.parse_devices(DEVICES) == {
        "emulator-5554": "device", "emulator-5556": "offline", "R58": "unauthorized",
    }


@pytest.mark.parametrize(
    "serial, message",
    [
        ("emulator-9", "not found"),
        ("emulator-5556", "offline"),
        ("R58", "unauthorized"),
    ],
)
def test_check_device_errors_are_specific(serial, message):
    with pytest.raises(AndroidError, match=message):
        android.check_device(serial, android.parse_devices(DEVICES))


def test_check_device_ok():
    android.check_device("emulator-5554", android.parse_devices(DEVICES))


def test_missing_adb(monkeypatch):
    monkeypatch.setenv("JEV_ADB", "")
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(android.shutil, "which", lambda name: None)
    with pytest.raises(AndroidError, match="adb not found"):
        android.find_adb()


def test_wait_boot_times_out(monkeypatch):
    monkeypatch.setattr(android, "adb", lambda *a, **k: "0")
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    with pytest.raises(AndroidError, match="not finished booting"):
        android.wait_boot("s", timeout=10, sleep=sleep, clock=clock)


def test_wait_boot_returns_when_booted(monkeypatch):
    monkeypatch.setattr(android, "adb", lambda *a, **k: "1\n")
    android.wait_boot("s", timeout=1)


class FakeDevice:
    """adb stand-in: tracks whether Chrome runs and whether the forward is alive."""

    def __init__(self, chrome=True, forward=True):
        self.chrome, self.forward, self.started = chrome, forward, 0

    def adb(self, serial, *args, **kwargs):
        if args[:1] == ("devices",):
            return "List of devices attached\ns\tdevice"
        if args[:3] == ("shell", "getprop", "sys.boot_completed"):
            return "1"
        if args[:1] == ("forward",):
            self.forward = True
            return ""
        if args[:2] == ("shell", "pidof"):
            return "123" if self.chrome else ""
        if args[:2] == ("shell", "am") and args[2] == "start":
            self.chrome = True
            self.started += 1
        return ""

    def websocket(self, port):
        return "ws://x" if self.forward and self.chrome else None


def test_ensure_android_starts_chrome_and_reforwards(monkeypatch):
    device = FakeDevice(chrome=False, forward=False)
    monkeypatch.setattr(android, "adb", device.adb)
    assert android.ensure_android("s", 9444, device.websocket) == "ws://x"
    assert device.started == 1


def test_ensure_android_does_not_restart_running_chrome(monkeypatch):
    device = FakeDevice(chrome=True, forward=False)
    monkeypatch.setattr(android, "adb", device.adb)
    assert android.ensure_android("s", 9444, device.websocket) == "ws://x"
    assert device.started == 0


def test_ensure_android_reports_dead_devtools(monkeypatch):
    device = FakeDevice()
    monkeypatch.setattr(android, "adb", device.adb)
    with pytest.raises(AndroidError, match="DevTools socket did not answer"):
        android.ensure_android("s", 9444, lambda port: None, wait=0)


def test_ensure_android_reports_chrome_that_will_not_start(monkeypatch):
    device = FakeDevice(chrome=False)

    def adb(serial, *args, **kwargs):
        return "" if args[:3] == ("shell", "am", "start") else device.adb(serial, *args)

    monkeypatch.setattr(android, "adb", adb)
    with pytest.raises(AndroidError, match="not running"):
        android.ensure_android("s", 9444, lambda port: None, wait=0)


def test_diagnose_names_what_died(monkeypatch):
    device = FakeDevice(chrome=False)
    monkeypatch.setattr(android, "adb", device.adb)
    assert "no longer running" in android.diagnose("s")
    device.chrome = True
    assert android.diagnose("s") is None
    assert "went away" in android.diagnose("gone")


def test_daemon_name_isolates_android():
    assert daemon_name({}) == "jev-mcp"
    assert daemon_name({"BU_NAME": "mine"}) == "mine"
    assert daemon_name({"JEV_ANDROID_SERIAL": "e", "JEV_CDP_PORT": "9555"}) == "jev-android-9555"
    assert daemon_name({"JEV_ANDROID_SERIAL": "e"}) == "jev-android-9444"


def test_ensure_chrome_android_branch_leaves_desktop_alone(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_server, "EXTERNAL_CDP_WS", None)
    monkeypatch.setenv("JEV_ANDROID_SERIAL", "emulator-5584")
    monkeypatch.delenv("JEV_CDP_PORT", raising=False)
    monkeypatch.setattr(
        "jev_ultrafast.android.ensure_android",
        lambda serial, port, probe, boot_timeout: seen.update(serial=serial, port=port) or "ws://dev",
    )
    mcp_server.ensure_chrome()
    assert seen == {"serial": "emulator-5584", "port": 9444}  # never the desktop 9333
    assert mcp_server.os.environ["BU_CDP_WS"] == "ws://dev"


def test_explain_error(monkeypatch):
    monkeypatch.delenv("JEV_ANDROID_SERIAL", raising=False)
    assert "connection was lost" in mcp_server.explain_error(RuntimeError("no close frame received or sent"))
    assert "stopped answering" in mcp_server.explain_error(RuntimeError("Runtime.evaluate timed out after 5s"))
    assert mcp_server.explain_error(ValueError("bad goal")) is None
    assert mcp_server.explain_error(AndroidError("device offline")) is None
    monkeypatch.setenv("JEV_ANDROID_SERIAL", "s")
    monkeypatch.setattr(mcp_server, "diagnose", lambda serial: "Chrome gone")
    assert mcp_server.explain_error(RuntimeError("no close frame")) == "Chrome gone"


def test_mobile_user_agent_and_helpers():
    assert browser.is_mobile_ua("Mozilla/5.0 (Linux; Android 10; K) Chrome/134 Mobile Safari/537.36")
    assert browser.is_mobile_ua("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)")
    assert not browser.is_mobile_ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/134")
    assert not browser.is_mobile_ua(None)
    assert browser.screenshot_scale(2.625) == pytest.approx(0.381, abs=1e-3)
    assert browser.screenshot_scale(0) == 1 and browser.screenshot_scale(None) == 1
    assert browser.select_all_modifier(True) == 2


class Recorder:
    def __init__(self, target):
        self.calls, self.target = [], target

    def __call__(self, method, session_id=None, **params):
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": self.target}}
        return {}


def click(mobile, monkeypatch):
    recorder = Recorder({"x": 10.5, "y": 20.5})
    monkeypatch.setattr(browser, "cdp", recorder)
    action = {"id": "a1", "kind": "click", "node": 3}
    browser.browser_operation({"operation": "act", "session": "s", "action": action, "mobile": mobile})
    return [method for method, _ in recorder.calls], recorder.calls


def test_mobile_click_uses_touch_not_mouse(monkeypatch):
    methods, calls = click(True, monkeypatch)
    assert "Input.dispatchTouchEvent" in methods and "Input.dispatchMouseEvent" not in methods
    assert [p["type"] for m, p in calls if m == "Input.dispatchTouchEvent"] == ["touchStart", "touchEnd"]


def test_desktop_click_still_uses_mouse(monkeypatch):
    methods, _ = click(False, monkeypatch)
    assert "Input.dispatchMouseEvent" in methods and "Input.dispatchTouchEvent" not in methods


def test_mobile_scroll_is_a_touch_gesture(monkeypatch):
    recorder = Recorder({"x": 200, "y": 400})
    monkeypatch.setattr(browser, "cdp", recorder)
    action = {"id": "scroll_down", "kind": "scroll", "delta": 300}
    browser.browser_operation({"operation": "act", "session": "s", "action": action, "mobile": True})
    method, params = recorder.calls[-1]
    assert method == "Input.synthesizeScrollGesture"
    assert params["yDistance"] == -300 and params["gestureSourceType"] == "touch"


def test_mobile_observe_clips_to_visual_viewport(monkeypatch):
    calls = []

    def fake(method, session_id=None, **params):
        calls.append((method, params))
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"pageX": 0, "pageY": 50, "clientWidth": 411, "clientHeight": 500}}
        if method == "Page.captureScreenshot":
            return {"data": "AAAA"}
        value = 2.625 if "devicePixelRatio" in params.get("expression", "") else {"url": "u", "text": "", "actions": [],
                                                                                     "scroll": 0, "marker": "m"}
        return {"result": {"value": value}}

    monkeypatch.setattr(browser, "cdp", fake)
    info = browser.browser_operation({"operation": "observe", "session": "s", "screenshot": True, "mobile": True})
    clip = dict(calls)["Page.captureScreenshot"]["clip"]
    assert clip["height"] == 500 and clip["y"] == 50 and clip["scale"] == pytest.approx(0.381, abs=1e-3)
    assert info["screenshot"] == "AAAA"
