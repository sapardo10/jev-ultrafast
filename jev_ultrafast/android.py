"""Attach jev to Chrome running on an Android device or emulator, over adb + CDP.

Every failure raises AndroidError with the exact next step, so a calling agent never has to guess.
"""

import os
import shutil
import subprocess
import time

CHROME_PACKAGE = os.environ.get("JEV_ANDROID_CHROME", "com.android.chrome")
CHROME_ACTIVITY = "com.google.android.apps.chrome.Main"
DEVTOOLS_SOCKET = "chrome_devtools_remote"
CHROME_FLAGS = "_ --disable-fre --no-first-run --no-default-browser-check"
ADB_TIMEOUT = 15


class AndroidError(RuntimeError):
    """The Android device, adb, or Chrome on it is not in a usable state."""


def find_adb():
    adb = os.environ.get("JEV_ADB") or shutil.which("adb")
    if not adb:
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
        candidate = os.path.join(sdk, "platform-tools", "adb")
        adb = candidate if sdk and os.path.exists(candidate) else None
    if not adb:
        raise AndroidError(
            "adb not found. Install it (`brew install android-platform-tools`) or set JEV_ADB / ANDROID_HOME."
        )
    return adb


def adb(serial, *args, timeout=ADB_TIMEOUT, check=True):
    command = [find_adb(), *(["-s", serial] if serial else []), *args]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AndroidError(
            f"adb {' '.join(args[:2])} timed out after {timeout}s; the device is hung or adb is wedged "
            "(try `adb kill-server`)."
        ) from None
    if check and done.returncode != 0:
        raise AndroidError(f"adb {' '.join(args[:2])} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def parse_devices(output):
    """`adb devices` output -> {serial: state}."""
    states = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    return states


def check_device(serial, states=None):
    """Raise a precise AndroidError unless `serial` is a ready device."""
    if states is None:
        states = parse_devices(adb(None, "devices"))
    state = states.get(serial)
    if state is None:
        have = ", ".join(states) or "none"
        raise AndroidError(
            f"Android device '{serial}' not found (connected: {have}). Start the emulator or fix JEV_ANDROID_SERIAL."
        )
    if state == "offline":
        raise AndroidError(f"Android device '{serial}' is offline; wait for boot or run `adb reconnect offline`.")
    if state == "unauthorized":
        raise AndroidError(f"Android device '{serial}' is unauthorized; accept the USB debugging prompt on the device.")
    if state != "device":
        raise AndroidError(f"Android device '{serial}' is in state '{state}', expected 'device'.")


def wait_boot(serial, timeout=120, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        booted = adb(serial, "shell", "getprop", "sys.boot_completed", check=False)
        if booted.strip() == "1":
            return
        if clock() >= deadline:
            raise AndroidError(
                f"Android device '{serial}' has not finished booting after {timeout}s "
                "(sys.boot_completed != 1); wait for the emulator or raise JEV_ANDROID_BOOT_TIMEOUT."
            )
        sleep(2)


def chrome_running(serial):
    return bool(adb(serial, "shell", "pidof", CHROME_PACKAGE, check=False))


def start_chrome(serial):
    """Launch Chrome on the device with first-run screens disabled. Does not wipe its profile."""
    adb(serial, "shell", f"echo '{CHROME_FLAGS}' > /data/local/tmp/chrome-command-line", check=False)
    adb(serial, "shell", "chmod 755 /data/local/tmp/chrome-command-line", check=False)
    adb(serial, "shell", "am", "set-debug-app", "--persistent", CHROME_PACKAGE, check=False)
    adb(
        serial, "shell", "am", "start", "-n", f"{CHROME_PACKAGE}/{CHROME_ACTIVITY}",
        "-a", "android.intent.action.VIEW", "-d", "about:blank",
    )


def forward_port(serial, port):
    adb(serial, "forward", f"tcp:{port}", f"localabstract:{DEVTOOLS_SOCKET}")


def ensure_android(serial, port, websocket_for, boot_timeout=120, wait=20):
    """Return the DevTools websocket URL of Chrome on `serial`, forwarded to local `port`.

    websocket_for(port) -> URL or None (injected so the caller owns the HTTP probe).
    """
    check_device(serial)
    wait_boot(serial, boot_timeout)
    websocket = websocket_for(port)
    if websocket:
        return websocket
    forward_port(serial, port)  # a dead forward and a missing forward look the same; re-create it
    websocket = websocket_for(port)
    if websocket:
        return websocket
    if not chrome_running(serial):
        start_chrome(serial)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        forward_port(serial, port)
        websocket = websocket_for(port)
        if websocket:
            return websocket
        time.sleep(0.5)
    if not chrome_running(serial):
        raise AndroidError(
            f"Chrome ({CHROME_PACKAGE}) is not running on '{serial}' and could not be started."
        )
    raise AndroidError(
        f"Chrome is running on '{serial}' but its DevTools socket did not answer on localhost:{port} "
        f"within {wait}s (forward dead or USB debugging off)."
    )


def diagnose(serial, app=None):
    """After a mid-run failure: say what actually died (device, Chrome/app, or neither). None when all is well."""
    try:
        check_device(serial)
        if app:
            if not adb(serial, "shell", "pidof", app, check=False):
                return f"App {app} is no longer running on '{serial}'; it was closed or crashed mid-task."
        elif not chrome_running(serial):
            return f"Chrome ({CHROME_PACKAGE}) is no longer running on '{serial}'; it was closed or crashed mid-task."
    except AndroidError as error:
        return f"{error} The device went away mid-task."
    return None
