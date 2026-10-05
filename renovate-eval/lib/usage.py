"""Claude subscription usage gate for automated evaluations.

Claude Code reports plan utilization in the ``rate_limit_event`` that
``claude -p --output-format stream-json --verbose`` emits after an API
response. ``rate_limit_info.unifiedWindows`` carries the 5-hour and 7-day
windows as fractions from 0 to 1. The field is not part of the documented
SDK schema, so every unexpected shape fails closed.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from typing import Any

Run = Callable[..., subprocess.CompletedProcess]

PROBE_COMMAND = [
    "claude",
    "-p",
    "--model",
    "haiku",
    "--output-format",
    "stream-json",
    "--verbose",
    "--max-turns",
    "1",
    "--no-session-persistence",
    "Reply OK",
]
WINDOWS = ("five_hour", "seven_day")


def parse_rate_limit_event(stream: str) -> dict[str, Any] | None:
    """Return the last rate_limit_info object in stream-json output."""
    info = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "rate_limit_event":
            candidate = event.get("rate_limit_info")
            if isinstance(candidate, dict):
                info = candidate
    return info


def usage_decision(info: dict[str, Any] | None, threshold: float) -> dict[str, Any]:
    """Decide whether a new evaluation may start.

    Evaluations are allowed only when every window is readable and below the
    threshold. The threshold is the used fraction at which evaluations stop.
    """
    result: dict[str, Any] = {"allowed": False, "threshold": threshold, "windows": {}}
    if info is None:
        result["reason"] = "Claude Code emitted no rate_limit_event"
        return result
    if info.get("status") == "rejected":
        result["reason"] = "Claude Code reports the rate limit as rejected"
        return result
    windows = info.get("unifiedWindows")
    if not isinstance(windows, dict):
        result["reason"] = "rate_limit_event has no unifiedWindows"
        return result
    for name in WINDOWS:
        window = windows.get(name)
        utilization = window.get("utilization") if isinstance(window, dict) else None
        # Values above 1 mean the window is over its limit and close the gate;
        # NaN, infinities and negatives are unreadable.
        if (
            isinstance(utilization, bool)
            or not isinstance(utilization, (int, float))
            or not math.isfinite(utilization)
            or utilization < 0
        ):
            result["reason"] = f"{name} utilization is unavailable"
            return result
        result["windows"][name] = utilization
    over = [name for name, used in result["windows"].items() if used >= threshold]
    if over:
        result["reason"] = "usage at or above threshold: " + ", ".join(
            f"{name} {result['windows'][name]:.0%}" for name in over
        )
        return result
    result["allowed"] = True
    result["reason"] = "usage below threshold"
    return result


def check_usage(
    threshold: float, *, run: Run | None = None, timeout: int = 120
) -> dict[str, Any]:
    """Run a minimal Claude Code request and decide from its usage report."""
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be greater than 0 and at most 1")
    run = run or subprocess.run
    try:
        completed = run(
            PROBE_COMMAND,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "allowed": False,
            "threshold": threshold,
            "windows": {},
            "reason": f"usage probe failed: {exc}",
        }
    decision = usage_decision(parse_rate_limit_event(completed.stdout or ""), threshold)
    if completed.returncode != 0 and decision["allowed"]:
        # A failed probe cannot vouch for its own report; keep a more specific
        # closed reason, such as a rejected limit, when there already is one.
        return {
            **decision,
            "allowed": False,
            "reason": f"usage probe failed with exit code {completed.returncode}",
        }
    return decision
