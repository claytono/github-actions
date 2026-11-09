"""Tests for the Claude subscription usage gate."""

from __future__ import annotations

import json
import subprocess

import pytest
from lib.usage import PROBE_COMMAND, check_usage, parse_rate_limit_event, usage_decision

_ABSENT = object()


def _event(five_hour=0.07, seven_day=0.47, status="allowed", windows=True):
    info = {"status": status, "rateLimitType": "five_hour"}
    if windows:
        info["unifiedWindows"] = {}
        if five_hour is not _ABSENT:
            info["unifiedWindows"]["five_hour"] = {
                "utilization": five_hour,
                "resetsAt": 1,
            }
        if seven_day is not _ABSENT:
            info["unifiedWindows"]["seven_day"] = {
                "utilization": seven_day,
                "resetsAt": 2,
            }
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info})


def _stream(*events: str) -> str:
    return "\n".join(
        [json.dumps({"type": "system"}), "not json", *events, '{"type":"result"}']
    )


def test_parse_returns_last_rate_limit_event():
    stream = _stream(_event(five_hour=0.1), _event(five_hour=0.2))

    assert (
        parse_rate_limit_event(stream)["unifiedWindows"]["five_hour"]["utilization"]
        == 0.2
    )


def test_parse_ignores_malformed_rate_limit_info():
    stream = _stream(json.dumps({"type": "rate_limit_event", "rate_limit_info": []}))

    assert parse_rate_limit_event(stream) is None


def test_allows_when_both_windows_below_threshold():
    decision = usage_decision(parse_rate_limit_event(_stream(_event())), 0.8)

    assert decision["allowed"] is True
    assert decision["windows"] == {"five_hour": 0.07, "seven_day": 0.47}


@pytest.mark.parametrize(
    ("five_hour", "seven_day", "expected"),
    [
        (0.8, 0.1, "five_hour 80%"),
        (0.1, 0.95, "seven_day 95%"),
    ],
)
def test_denies_when_either_window_reaches_threshold(five_hour, seven_day, expected):
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(five_hour, seven_day))), 0.8
    )

    assert decision["allowed"] is False
    assert expected in decision["reason"]


def test_fails_closed_without_event():
    decision = usage_decision(None, 0.8)

    assert decision["allowed"] is False
    assert "no rate_limit_event" in decision["reason"]


def test_fails_closed_when_rejected():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(status="rejected"))), 0.8
    )

    assert decision["allowed"] is False
    assert "rejected" in decision["reason"]


def test_fails_closed_without_unified_windows():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(windows=False))), 0.8
    )

    assert decision["allowed"] is False
    assert "no unifiedWindows" in decision["reason"]


@pytest.mark.parametrize("value", [None, "0.1", True, float("nan"), float("inf"), -0.1])
def test_fails_closed_on_unusable_utilization(value):
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(seven_day=value))), 0.8
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "seven_day utilization is unavailable"


def test_allows_plan_without_five_hour_window():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(five_hour=_ABSENT))), 0.8
    )

    assert decision["allowed"] is True
    assert decision["windows"] == {"seven_day": 0.47}


def test_weekly_window_still_closes_gate_without_five_hour_window():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(five_hour=_ABSENT, seven_day=0.9))),
        0.8,
    )

    assert decision["allowed"] is False
    assert "seven_day 90%" in decision["reason"]


def test_fails_closed_without_seven_day_window():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(seven_day=_ABSENT))), 0.8
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "seven_day utilization is unavailable"


def test_fails_closed_with_empty_unified_windows():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(five_hour=_ABSENT, seven_day=_ABSENT))),
        0.8,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "seven_day utilization is unavailable"


@pytest.mark.parametrize("window", [None, {}, {"utilization": None}])
def test_present_but_unreadable_five_hour_window_fails_closed(window):
    info = json.loads(_event())["rate_limit_info"]
    info["unifiedWindows"]["five_hour"] = window

    decision = usage_decision(info, 0.8)

    assert decision["allowed"] is False
    assert decision["reason"] == "five_hour utilization is unavailable"


def test_check_usage_runs_probe_and_decides():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=_stream(_event()))

    decision = check_usage(0.8, run=run)

    assert decision["allowed"] is True
    assert calls[0][0] == PROBE_COMMAND
    assert "--bare" not in PROBE_COMMAND
    assert calls[0][1]["timeout"] == 120


def test_check_usage_reads_event_from_failed_probe():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout=_stream(_event(status="rejected"))
        )

    assert check_usage(0.8, run=run)["allowed"] is False


@pytest.mark.parametrize(
    "error", [OSError("missing"), subprocess.TimeoutExpired("claude", 120)]
)
def test_check_usage_fails_closed_when_probe_cannot_run(error):
    def run(command, **kwargs):
        raise error

    decision = check_usage(0.8, run=run)

    assert decision["allowed"] is False
    assert decision["reason"].startswith("usage probe failed")


def test_check_usage_handles_missing_stdout():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=None)

    assert check_usage(0.8, run=run)["allowed"] is False


@pytest.mark.parametrize("threshold", [0, -0.1, 1.5])
def test_check_usage_rejects_invalid_threshold(threshold):
    with pytest.raises(ValueError, match="threshold"):
        check_usage(threshold, run=lambda *a, **k: None)


def test_nan_from_stream_fails_closed():
    stream = _stream(_event()).replace('"utilization": 0.47', '"utilization": NaN')

    decision = usage_decision(parse_rate_limit_event(stream), 0.8)

    assert decision["allowed"] is False
    assert decision["reason"] == "seven_day utilization is unavailable"


def test_utilization_over_one_closes_gate_as_over_threshold():
    decision = usage_decision(
        parse_rate_limit_event(_stream(_event(five_hour=1.2))), 0.8
    )

    assert decision["allowed"] is False
    assert "five_hour 120%" in decision["reason"]


def test_nonzero_probe_exit_closes_an_otherwise_open_gate():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=_stream(_event()))

    decision = check_usage(0.8, run=run)

    assert decision["allowed"] is False
    assert decision["reason"] == "usage probe failed with exit code 1"
    assert decision["windows"] == {"five_hour": 0.07, "seven_day": 0.47}


def test_nonzero_probe_exit_keeps_a_more_specific_closed_reason():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout=_stream(_event(status="rejected"))
        )

    decision = check_usage(0.8, run=run)

    assert decision["allowed"] is False
    assert "rejected" in decision["reason"]
