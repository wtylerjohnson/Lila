"""Alert channels for the Review Gate.

The gate (agents/review.py) takes any alert_fn(packet, md_path). This module provides
the desktop/push-notification channel (macOS `display notification`) with a graceful
fallback to the terminal banner when notifications aren't available.

Swappable: to add Slack/email later, write another alert_fn and pass it to
request_approval(strategy, alert_fn=...).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable

from agents.review import ReviewPacket, _default_alert


def _osascript(title: str, message: str, runner: Callable[..., object] = subprocess.run) -> bool:
    """Fire a macOS desktop notification. Returns True on success."""
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    # Escape double quotes for the AppleScript string literals.
    t = title.replace('"', '\\"')
    m = message.replace('"', '\\"')
    script = f'display notification "{m}" with title "{t}" sound name "Submarine"'
    try:
        runner(["osascript", "-e", script], check=True, capture_output=True)
        return True
    except Exception:  # noqa: BLE001 — never let an alert failure break the pipeline
        return False


def desktop_alert(
    packet: ReviewPacket,
    md_path: str,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    """Desktop notification + always also print the banner (so the CLI shows next steps)."""
    _osascript(
        title=f"LILA: review {packet.client_name}",
        message="Analyst Layer pending your approval.",
        runner=runner,
    )
    _default_alert(packet, md_path)  # banner carries the approve/reject commands
