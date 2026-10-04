"""Load browser-harness pinned to jev's own daemon, isolated from other tools."""

import os

os.environ.setdefault("BU_NAME", "jev-mcp")

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

__all__ = ["cdp", "ensure_daemon"]
