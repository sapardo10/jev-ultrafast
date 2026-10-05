"""Native Android task matrix: each task runs N times on an emulator and is verified through adb, not by jev's DONE.

    ANDROID_ADB_SERVER_PORT=5038 JEV_ANDROID_SERIAL=emulator-5580 DECISION_BACKEND=ollama \
      uv run python scripts/native_matrix.py --runs 3 [--only settings_dark_on]

Public system apps and your own debug APKs only; nothing here logs in, buys, or calls a paid API.
Needs a running Ollama (nimble) and a booted emulator that you are free to modify.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

SERIAL = os.environ.get("JEV_ANDROID_SERIAL", "emulator-5580")
HOME = Path.home()
PAUSACTIVE_APK = HOME / "projects/pausactive/build/app/outputs/flutter-apk/app-debug.apk"
PROBE_APK = Path(__file__).resolve().parents[1] / "examples/flutter_probe/build/app/outputs/flutter-apk/app-debug.apk"


def sh(*args):
    done = subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=60)
    return (done.stdout + done.stderr).strip()


def screen_text():
    return sh("exec-out", "uiautomator", "dump", "/dev/tty")


def stop_settings():
    sh("shell", "am", "force-stop", "com.android.settings")


def clear_app(package):
    sh("shell", "pm", "clear", package)


TASKS = {
    "settings_dark_on": dict(
        package="com.android.settings", goal="Turn on dark theme",
        reset=lambda: (sh("shell", "cmd", "uimode", "night", "no"), stop_settings()),
        verify=lambda: "Night mode: yes" in sh("shell", "cmd", "uimode", "night"),
    ),
    "settings_wifi_off": dict(
        package="com.android.settings", goal="Turn off Wi-Fi",
        reset=lambda: (sh("shell", "svc", "wifi", "enable"), time.sleep(3), stop_settings()),
        verify=lambda: sh("shell", "settings", "get", "global", "wifi_on") == "0",
    ),
    "settings_airplane_on": dict(
        package="com.android.settings", goal="Turn on airplane mode",
        reset=lambda: (sh("shell", "cmd", "connectivity", "airplane-mode", "disable"), stop_settings()),
        verify=lambda: sh("shell", "settings", "get", "global", "airplane_mode_on") == "1",
    ),
    "settings_timeout_30m": dict(
        package="com.android.settings", goal="Set the screen timeout to 30 minutes",
        reset=lambda: (sh("shell", "settings", "put", "system", "screen_off_timeout", "60000"), stop_settings()),
        verify=lambda: sh("shell", "settings", "get", "system", "screen_off_timeout") == "1800000",
    ),
    "contacts_add": dict(
        package="com.google.android.contacts",
        goal="Create a new contact named Jose Nunez with phone number 5551234 and save it",
        reset=lambda: (sh("shell", "pm", "clear", "com.google.android.contacts"),
                       sh("shell", "pm", "clear", "com.android.providers.contacts")),
        verify=lambda: "5551234" in "".join(
            c for c in sh("shell", "content", "query", "--uri", "content://com.android.contacts/data",
                          "--projection", "data1") if c.isdigit() or c == "\n"
        ),
    ),
    "flutter_onboarding": dict(
        package="com.iziapps.pausactive.pausactive", apk=PAUSACTIVE_APK,
        goal="Go through the welcome screens by tapping Next until the Dashboard screen is shown",
        reset=lambda: clear_app("com.iziapps.pausactive.pausactive"),
        verify=lambda: 'content-desc="Dashboard"' in screen_text(),
    ),
    "flutter_hamburger": dict(
        package="com.jev.jev_probe", apk=PROBE_APK,
        goal="Open the navigation menu, then open Settings",
        reset=lambda: clear_app("com.jev.jev_probe"),
        verify=lambda: "Settings opened" in screen_text(),
    ),
}


def run_task(name, task, runs):
    from jev_ultrafast import mcp_server

    results = []
    if task.get("apk") and task["package"] not in sh("shell", "pm", "list", "packages", task["package"]):
        print(sh("install", "-r", str(task["apk"])))
    for run in range(1, runs + 1):
        sh("shell", "input", "keyevent", "KEYCODE_HOME")
        task["reset"]()
        os.environ["JEV_ANDROID_APP"] = task["package"]
        started = time.time()
        try:
            result = json.loads(mcp_server._browser_task("", task["goal"], 20, False, False))
        except Exception as error:  # a crash is a failed run, not a matrix abort
            result = {"status": "crash", "error": repr(error), "actions": []}
        seconds = round(time.time() - started, 1)
        verified = bool(task["verify"]())
        results.append(dict(task=name, run=run, status=result.get("status"), steps=len(result.get("actions", [])),
                            seconds=seconds, verified=verified, error=result.get("error", "")[:100]))
        print(json.dumps(results[-1]), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", action="append")
    args = parser.parse_args()
    os.environ.update(JEV_ANDROID_SERIAL=SERIAL, DECISION_BACKEND=os.environ.get("DECISION_BACKEND", "ollama"))
    rows = []
    for name, task in TASKS.items():
        if args.only and name not in args.only:
            continue
        rows += run_task(name, task, args.runs)
    print("\n| task | pass | steps (avg) | seconds (avg) | failures |\n|---|---|---|---|---|")
    for name in dict.fromkeys(r["task"] for r in rows):
        mine = [r for r in rows if r["task"] == name]
        passed = sum(r["verified"] for r in mine)
        fails = "; ".join(sorted({f"{r['status']} {r['error']}".strip() for r in mine if not r["verified"]}))
        print(f"| {name} | {passed}/{len(mine)} | {sum(r['steps'] for r in mine) / len(mine):.1f} | "
              f"{sum(r['seconds'] for r in mine) / len(mine):.0f} | {fails} |")


if __name__ == "__main__":
    main()
