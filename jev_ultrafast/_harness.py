"""Load browser-harness pinned to jev's own daemon, isolated from other tools."""

import os


def daemon_name(env):
    """Android sessions get their own daemon (one per CDP port) so they never touch the desktop one."""
    if env.get("JEV_ANDROID_SERIAL"):
        return f"jev-android-{env.get('JEV_CDP_PORT', '9444')}"
    return env.get("BU_NAME") or "jev-mcp"


# browser-harness reads BU_NAME once at import, so this must run before it is imported.
os.environ["BU_NAME"] = daemon_name(os.environ)

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

__all__ = ["cdp", "daemon_name", "ensure_daemon"]
