"""Tests for CloudWatch log fetching behavior."""

from datetime import timedelta

import pytest
from botocore.exceptions import ClientError

from hello.aws import cloudwatch_logs


class _FallbackDeniedClient:
    def filter_log_events(self, **kwargs):
        return {"events": []}

    def describe_log_streams(self, **kwargs):
        return {"logStreams": [{"logStreamName": "latest"}]}

    def get_log_events(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "missing permission for fallback",
                }
            },
            "GetLogEvents",
        )


class _FallbackBrokenClient(_FallbackDeniedClient):
    def get_log_events(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "stream missing",
                }
            },
            "GetLogEvents",
        )


def test_fetch_recent_events_ignores_access_denied_in_fallback(monkeypatch):
    cloudwatch_logs._CACHE.clear()
    monkeypatch.setattr(
        cloudwatch_logs.boto3,
        "client",
        lambda *args, **kwargs: _FallbackDeniedClient(),
    )

    events = cloudwatch_logs.fetch_recent_events(
        log_groups=["/aws/lambda/FaultRouter"],
        lookback=timedelta(minutes=5),
        limit_per_group=20,
    )

    assert events == []


def test_fetch_recent_events_raises_for_non_access_denied_fallback(monkeypatch):
    cloudwatch_logs._CACHE.clear()
    monkeypatch.setattr(
        cloudwatch_logs.boto3,
        "client",
        lambda *args, **kwargs: _FallbackBrokenClient(),
    )

    with pytest.raises(RuntimeError, match="get_log_events fallback failed"):
        cloudwatch_logs.fetch_recent_events(
            log_groups=["/aws/lambda/FaultRouter"],
            lookback=timedelta(minutes=5),
            limit_per_group=20,
        )


def test_build_incidents_from_events_keeps_distinct_reasons_separate():
    events = [
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/ecs/cream-task",
            log_stream="web/1",
            timestamp_ms=1_700_000_000_000,
            message=(
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=external_timeout latency=3.40"
            ),
        ),
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/ecs/cream-task",
            log_stream="web/1",
            timestamp_ms=1_700_000_005_000,
            message=(
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=wrong_data latency=0.40"
            ),
        ),
    ]

    incidents = cloudwatch_logs.build_incidents_from_events(events)

    assert len(incidents) == 2
    assert {incident["symptoms"]["log_marker"] for incident in incidents} == {
        "external_timeout",
        "wrong_data",
    }


def test_build_fault_router_incidents_supports_claude_output_and_fault_code_payload():
    request_id = "123e4567-e89b-12d3-a456-426614174000"
    events = [
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/aws/lambda/FaultRouter",
            log_stream="2026/03/24/[$LATEST]abc",
            timestamp_ms=1_700_000_000_000,
            message=f"START RequestId: {request_id}",
        ),
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/aws/lambda/FaultRouter",
            log_stream="2026/03/24/[$LATEST]abc",
            timestamp_ms=1_700_000_001_000,
            message=(
                'BACKBOARD_ANALYSIS: {"fault_code": "FAULT_DB_TIMEOUT", '
                '"content": "The demo route exceeded the statement timeout."}'
            ),
        ),
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/aws/lambda/FaultRouter",
            log_stream="2026/03/24/[$LATEST]abc",
            timestamp_ms=1_700_000_002_000,
            message="CLAUDE_OUTPUT: Reduced the pg_sleep duration to 1 second.",
        ),
        cloudwatch_logs.CloudWatchLogEvent(
            log_group="/aws/lambda/FaultRouter",
            log_stream="2026/03/24/[$LATEST]abc",
            timestamp_ms=1_700_000_003_000,
            message=f"END RequestId: {request_id}",
        ),
    ]

    incidents = cloudwatch_logs.build_fault_router_incidents(events)

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["error_code"] == "FAULT_DB_TIMEOUT"
    assert incident["status"] == "resolved"
    assert incident["root_cause"]["source"] == "backboard"
    assert incident["root_cause"]["explanation"] == "The demo route exceeded the statement timeout."
    assert incident["remediation"]["action_type"] == "claude_autofix"
