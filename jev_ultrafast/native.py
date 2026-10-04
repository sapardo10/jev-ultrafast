"""Native Android app as a Browser: observe = `uiautomator dump`, act = `adb shell input`.

Same interface as Browser (observe, fresh, act, close) and the same page dict, so the agent loop and the
decision models are unchanged. Selected by JEV_ANDROID_APP=<package> (+ JEV_ANDROID_SERIAL). Model output
never becomes coordinates or shell text: targets are observed ids resolved here to a bounds centre.
"""

import base64
import hashlib
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from .android import AndroidError, adb, check_device, find_adb, wait_boot
from .browser import StalePage

BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
IGNORED_PACKAGES = ("com.android.systemui",)  # status bar clock/battery would change every fingerprint
MAX_ACTIONS = 250
MAX_TEXT = 6000
DUMP_ATTEMPTS = 4
FOCUS = re.compile(r"\s([\w.]+)/([\w.$]+)\s+t\d+\}")


class NativeError(AndroidError):
    """The app, its accessibility tree, or an input could not be used."""


def is_launcher(activity):
    """Home screen in front means the app was left or killed, not that a dialog is covering it."""
    return "launcher" in activity.split("/", 1)[0].lower()


def parse_bounds(value):
    match = BOUNDS.fullmatch(value or "")
    return tuple(int(n) for n in match.groups()) if match else None


def extract_xml(output):
    """`uiautomator dump /dev/tty` prints the XML then a status line; keep only the document."""
    start, end = output.find("<?xml"), output.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise NativeError(f"uiautomator returned no hierarchy: {output.strip()[:200] or 'empty output'}")
    return output[start : end + len("</hierarchy>")]


def walk(node, out):
    for child in node:
        out.append(child)
        walk(child, out)
    return out


def descendant_text(node, limit=3):
    """First few labelled descendants: how a clickable row without text of its own is named."""
    parts = []
    for child in walk(node, []):
        label = (child.get("text") or child.get("content-desc") or "").strip()
        if label and label not in parts and not child.get("password") == "true":
            parts.append(label)
        if len(parts) >= limit:
            break
    return " — ".join(parts)


def name_of(node):
    candidates = (node.get("text"), node.get("content-desc"), node.get("hint"))
    own = next((v.strip() for v in candidates if v and v.strip()), "")
    return " ".join((own or descendant_text(node)).split())  # Flutter tabs: "Settings\nTab 2 of 2" is one label


def role_of(node):
    cls = (node.get("class") or "").rsplit(".", 1)[-1]
    if node.get("password") == "true":
        return None
    if "EditText" in cls or "AutoCompleteTextView" in cls:
        return "textbox"
    if node.get("checkable") == "true" or cls in {"Switch", "SwitchCompat", "CheckBox", "RadioButton", "ToggleButton"}:
        return "switch" if "Switch" in cls or "Toggle" in cls else "checkbox"
    if cls == "Button" or cls == "ImageButton":
        return "button"
    if node.get("clickable") == "true" or node.get("long-clickable") == "true":
        return "button"
    return None


def checked_state(node):
    """A row is checked when it, or a checkable child (the Switch inside a Settings row), is."""
    for candidate in [node, *walk(node, [])]:
        if candidate.get("checkable") == "true":
            return "true" if candidate.get("checked") == "true" else "false"
    return None


def parse_screen(xml_text):
    """XML -> (elements, scrollables, text lines, (width, height)). Pure; the offline tests cover it."""
    root = ET.fromstring(xml_text)
    nodes = walk(root, [])
    # The display is the first (window root) node's bounds; taking the max over all nodes would count off-screen rows.
    screen = next((b for b in (parse_bounds(n.get("bounds")) for n in nodes) if b), (0, 0, 0, 0))
    width, height = screen[2], screen[3]
    elements, scrollables, text = [], [], []
    seen_text = set()

    def visit(node, inside_element):
        pkg = node.get("package") or ""
        if pkg in IGNORED_PACKAGES:
            return
        bounds = parse_bounds(node.get("bounds"))
        on_screen = bool(
            bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1]
            and bounds[0] < width and bounds[1] < height and bounds[2] > 0 and bounds[3] > 0
            and node.get("visible-to-user", "true") != "false"
        )
        claimed = False
        if on_screen:
            if node.get("scrollable") == "true":
                scrollables.append({"bounds": bounds, "area": (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])})
            role = role_of(node)
            label = name_of(node) if role else ""
            centre = ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
            visible_centre = 0 <= centre[0] < width and 0 <= centre[1] < height
            if role and label and node.get("enabled", "true") == "true" and visible_centre:
                element = {
                    "role": role, "label": label[:120], "bounds": bounds, "centre": centre,
                    "kind": "fill" if role == "textbox" else "click",
                    "value": (node.get("text") or "") if role == "textbox" else "",
                    "id_hint": (node.get("resource-id") or "").rsplit("/", 1)[-1],
                    "package": pkg,
                }
                state = checked_state(node)
                if state is not None:
                    element["checked"] = state
                if node.get("selected") == "true":
                    element["selected"] = "true"
                elements.append(element)
                claimed = True
            elif not role:
                line = (node.get("text") or node.get("content-desc") or "").strip()
                if line and not inside_element and line not in seen_text and node.get("password") != "true":
                    seen_text.add(line)
                    text.append((bounds[1], bounds[0], line))
        for child in node:
            visit(child, inside_element or claimed)

    visit(root, False)
    text.sort()
    return elements, scrollables, [line for _, _, line in text], (width, height)


def hash_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def guard_of(element):
    return hash_of([element["role"], element["label"], element["bounds"], element.get("checked"), element["value"]])


def build_page(xml_text, activity, with_screenshot=None):
    """The same dict Browser.observe returns: url, title, text, actions e1..eN, marker, page_key, guards."""
    package = activity.split("/", 1)[0]
    elements, scrollables, lines, (width, height) = parse_screen(xml_text)
    omitted = max(0, len(elements) - MAX_ACTIONS)
    elements = elements[:MAX_ACTIONS]
    actions, guards = [], {}
    for index, element in enumerate(elements, 1):
        action = {
            "id": f"e{index}", "node": index, "kind": element["kind"], "role": element["role"],
            "label": element["label"],
            "value": element["value"], "rect": element["bounds"],
        }
        for key in ("checked", "selected"):
            if key in element:
                action[key] = element[key]
        actions.append(action)
        guards[str(index)] = guard_of(element)
    if scrollables:
        scroller = max(scrollables, key=lambda s: s["area"])
        left, top, right, bottom = scroller["bounds"]
        at = {"x": (left + right) // 2, "y": (top + bottom) // 2, "span": bottom - top}
        actions.append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down for more", "delta": 1, "at": at})
        actions.append({"id": "scroll_up", "kind": "scroll", "label": "Scroll up", "delta": -1, "at": at})
    actions.append({"id": "back", "kind": "back", "label": "Go back (system BACK button)"})
    actions.append({"id": "wait", "kind": "wait", "label": "Wait for the screen to update"})
    text = "\n".join(lines)[:MAX_TEXT]
    title = activity.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    semantics = [{k: v for k, v in a.items() if k != "rect"} for a in actions]
    page_key = hash_of([activity, [e["label"] for e in elements]])
    return {
        "url": activity, "title": title, "native": True, "package": package, "w": width, "h": height, "text": text,
        "scroll": {"y": 0, "height": height}, "actions": actions, "guards": guards, "page_key": page_key,
        "marker": hash_of([activity, text, semantics]), "omitted_actions": omitted,
    }


def adb_text(value):
    """Escape for `adb shell input text`: spaces become %s; everything outside plain ASCII is refused."""
    if not value.isascii() or not value.isprintable():
        raise NativeError(
            "`adb shell input text` types ASCII only; this value has other characters. "
            "Install an IME such as ADBKeyboard to type unicode."
        )
    return re.sub(r"([\\\"'`&|;<>()$*?!#~{}\[\]^])", r"\\\1", value.replace("%", "%%")).replace(" ", "%s")


class NativeScreen:
    """Browser-shaped view of one Android app, driven over adb."""

    def __init__(self, target, serial, package, start=True):
        self.serial, self.package, self.after_input = serial, package, None
        self.mobile = True
        self.sleep = time.sleep
        check_device(serial)
        wait_boot(serial)
        if start:
            self.launch(target)

    # -- device ---------------------------------------------------------------------------------
    def launch(self, target=""):
        """Bring the app forward. `target` may be an activity (`.MainActivity`) or empty for the launcher intent."""
        if target and target.startswith((".", self.package)):
            activity = target if "/" in target else f"{self.package}/{target}"
        else:
            activity = self.launcher_activity()
        adb(self.serial, "shell", "am", "start", "-n", activity)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.in_play(self.foreground()):
                return
            self.sleep(0.3)
        raise NativeError(
            f"App {self.package} did not reach the foreground on {self.serial} (not installed or crashed)."
        )

    def launcher_activity(self):
        out = adb(
            self.serial, "shell", "cmd", "package", "resolve-activity", "--brief",
            "-c", "android.intent.category.LAUNCHER", self.package, check=False,
        )
        component = out.splitlines()[-1].strip() if out.strip() else ""
        if "/" not in component:
            raise NativeError(
                f"{self.package} has no launcher activity on {self.serial} (not installed?). Install the app first."
            )
        return component

    def in_play(self, activity):
        """The app's own screen, or a system dialog over a live app. Not: nothing, the home screen, a dead app."""
        if activity.startswith(self.package + "/"):
            return True
        return bool(activity) and not is_launcher(activity) and self.alive()

    def alive(self):
        return bool(adb(self.serial, "shell", "pidof", self.package, check=False))

    def foreground(self):
        out = adb(self.serial, "shell", "dumpsys", "activity", "activities", check=False)
        for line in out.splitlines():
            if "topResumedActivity" in line or "mResumedActivity" in line:
                match = FOCUS.search(line)
                if match:
                    return f"{match.group(1)}/{match.group(2)}"
        return ""

    def dump(self):
        last = None
        for _ in range(DUMP_ATTEMPTS):  # reading is idempotent; "could not get idle state" is common while animating
            out = subprocess.run(
                [find_adb(), "-s", self.serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            try:
                return extract_xml(out)
            except NativeError as error:
                last = error
                self.sleep(0.4)
        raise last

    def screenshot(self):
        png = subprocess.run(
            [find_adb(), "-s", self.serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=30
        ).stdout
        return base64.b64encode(png).decode()

    def read(self):
        activity = self.foreground()
        if not self.in_play(activity):
            raise NativeError(
                f"{self.package} is not in the foreground (found {activity or 'none'}); "
                "the app closed, crashed, or another app took focus (is another adb client attached?)."
            )
        # A system dialog over a live app (runtime permission, share sheet) is a screen the goal may need to answer.
        return build_page(self.dump(), activity)

    # -- Browser interface ------------------------------------------------------------------------
    def observe(self, screenshot=True):
        if self.after_input:
            self.after_input = None
            self.sleep(0.5)  # execution is already logged; let the transition finish before reading
        page = None
        for attempt in range(3):
            page = self.read()
            self.sleep(0.15)
            again = self.read()
            if again["marker"] == page["marker"]:  # two identical reads: the screen has stopped animating
                page = again
                break
        page["fingerprint"] = hash_of({k: page[k] for k in ("url", "text", "actions")})
        if screenshot:
            page["screenshot"] = self.screenshot()
        return page

    def fresh(self, page, action=None):
        current = self.read()
        if action is not None and action["kind"] in {"click", "fill"}:
            node = str(action["node"])
            return current["page_key"] == page["page_key"] and current["guards"].get(node) == page["guards"].get(node)
        return current["marker"] == page["marker"]

    def act(self, action, page, text=None):
        kind = action["kind"]
        if kind == "fill" and text is not None:
            adb_text(text)  # refuse unsupported text before touching the device
        if not self.fresh(page, action):
            raise StalePage("Screen changed since this decision. Observe again.")
        if kind == "wait":
            self.sleep(0.3)
        elif kind == "back":
            self.shell("input", "keyevent", "4")
        elif kind == "scroll":
            at = action["at"]
            span = max(200, at["span"] // 3)
            start, end = at["y"] + span * action["delta"], at["y"] - span * action["delta"]
            self.shell("input", "swipe", str(at["x"]), str(start), str(at["x"]), str(end), "350")
        elif kind in {"click", "fill"}:
            x, y = self.centre(action)
            self.shell("input", "tap", str(x), str(y))
            if kind == "fill":
                current = action.get("value") or ""
                self.sleep(0.4)
                self.shell("input", "keyevent", "KEYCODE_MOVE_END", *(["KEYCODE_DEL"] * (len(current) + 2)))
                if text:
                    self.shell("input", "text", adb_text(text))
        else:
            raise NativeError(f"Unsupported action {kind}")
        self.after_input = action if kind != "wait" else None
        return {"executed": action["id"]}

    def centre(self, action):
        left, top, right, bottom = action["rect"]
        return (left + right) // 2, (top + bottom) // 2

    def shell(self, *args):
        adb(self.serial, "shell", *args)

    def close(self):
        pass  # the app and device belong to the user; nothing of ours to tear down
