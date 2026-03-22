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
