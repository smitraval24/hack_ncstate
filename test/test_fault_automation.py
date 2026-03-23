"""This file keeps tests for the test part of the project so new changes stay safe."""

import importlib
import json
import os
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

fault_router_lambda = importlib.import_module("fault_router_lambda_function")
github_tool_lambda = importlib.import_module("GithubTool_lambda_function")


# This function runs the incident dedupe key ignores latency for same fault route and reason work used in this file.
def test_incident_dedupe_key_ignores_latency_for_same_fault_route_and_reason():
    first = fault_router_lambda.build_incident(
        {
            "id": "evt-1",
            "timestamp": 1_700_000_000_000,
            "message": (
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=external_timeout latency=0.01"
            ),
        },
        "/ecs/cream-task",
        "ecs/app/1",
    )
    second = fault_router_lambda.build_incident(
        {
            "id": "evt-2",
            "timestamp": 1_700_000_005_000,
            "message": (
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=external_timeout latency=3.42"
            ),
        },
        "/ecs/cream-task",
        "ecs/app/1",
    )

    assert fault_router_lambda.incident_dedupe_key(first) == (
        "FAULT_EXTERNAL_API_LATENCY|/test-fault/external-api|external_timeout"
    )
    assert fault_router_lambda.incident_dedupe_key(first) == (
        fault_router_lambda.incident_dedupe_key(second)
    )


# This function runs the incident dedupe key keeps distinct reasons separate work used in this file.
def test_incident_dedupe_key_keeps_distinct_reasons_separate():
    timeout_incident = fault_router_lambda.build_incident(
        {
            "id": "evt-1",
            "timestamp": 1_700_000_000_000,
            "message": (
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=external_timeout latency=0.01"
            ),
        },
        "/ecs/cream-task",
        "ecs/app/1",
    )
    wrong_data_incident = fault_router_lambda.build_incident(
        {
            "id": "evt-2",
            "timestamp": 1_700_000_005_000,
            "message": (
                "FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api "
                "reason=wrong_data latency=0.50"
            ),
        },
        "/ecs/cream-task",
        "ecs/app/1",
    )

    assert fault_router_lambda.incident_dedupe_key(timeout_incident) != (
        fault_router_lambda.incident_dedupe_key(wrong_data_incident)
    )


# This function runs the push github fix skips commit when content is unchanged work used in this file.
def test_push_github_fix_skips_commit_when_content_is_unchanged(monkeypatch):
    os.environ["GITHUB_OWNER"] = "example"
    os.environ["GITHUB_REPO"] = "repo"

    unchanged_content = "print('already fixed')\n"

    def fake_gh_request(method, path, body=None):
        assert method == "GET"
        assert body is None
        return {
            "sha": "abc123",
            "content": github_tool_lambda.base64.b64encode(
                unchanged_content.encode("utf-8")
            ).decode("utf-8"),
        }

    monkeypatch.setattr(github_tool_lambda, "gh_request", fake_gh_request)

    response = github_tool_lambda.lambda_handler(
        {
            "actionGroup": "GitHubActions",
            "function": "push_github_fix",
            "allowed_file_path": "hello/page/views_sql.py",
            "parameters": [
                {"name": "file_path", "value": "hello/page/views_sql.py"},
                {"name": "file_content", "value": unchanged_content},
                {"name": "commit_message", "value": "No-op fix"},
            ],
        },
        None,
    )

    body = json.loads(
        response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    )

    assert body["ok"] is True
    assert body["no_change"] is True
    assert body["commit_sha"] is None


# This function runs the read github file rejects fault template files work used in this file.
def test_read_github_file_rejects_fault_template_file():
    os.environ["GITHUB_OWNER"] = "example"
    os.environ["GITHUB_REPO"] = "repo"

    response = github_tool_lambda.lambda_handler(
        {
            "actionGroup": "GitHubActions",
            "function": "read_github_file",
            "parameters": [
                {
                    "name": "file_path",
                    "value": "hello/page/_faulty_views_template.py",
                },
            ],
        },
        None,
    )

    body = json.loads(
        response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    )

    assert body["ok"] is False
    assert "forbidden" in body["error"].lower()


# This function runs the push github fix rejects unexpected files work used in this file.
def test_push_github_fix_rejects_unexpected_file():
    os.environ["GITHUB_OWNER"] = "example"
    os.environ["GITHUB_REPO"] = "repo"

    response = github_tool_lambda.lambda_handler(
        {
            "actionGroup": "GitHubActions",
            "function": "push_github_fix",
            "parameters": [
                {"name": "file_path", "value": "README.md"},
                {"name": "file_content", "value": "updated"},
                {"name": "commit_message", "value": "Should fail"},
            ],
        },
        None,
    )

    body = json.loads(
        response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    )

    assert body["ok"] is False
    assert "only" in body["error"].lower()
    assert "views_" in body["error"].lower()


def test_push_github_fix_rejects_other_allowed_file_when_invocation_is_scoped():
    os.environ["GITHUB_OWNER"] = "example"
    os.environ["GITHUB_REPO"] = "repo"

    response = github_tool_lambda.lambda_handler(
        {
            "actionGroup": "GitHubActions",
            "function": "push_github_fix",
            "allowed_file_path": "hello/page/views_sql.py",
            "parameters": [
                {"name": "file_path", "value": "hello/page/views_db.py"},
                {"name": "file_content", "value": "updated"},
                {"name": "commit_message", "value": "Should fail"},
            ],
        },
        None,
    )

    body = json.loads(
        response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    )

    assert body["ok"] is False
    assert "may only access hello/page/views_sql.py" in body["error"]


def test_build_github_tool_event_scopes_lambda_invocation_to_target_file():
    event = fault_router_lambda.build_github_tool_event(
        tool_name="read_github_file",
        tool_input={"file_path": "/hello/page/views_sql.py"},
        target_file="hello/page/views_sql.py",
        fault_code="FAULT_SQL_INJECTION_TEST",
    )

    assert event["allowed_file_path"] == "hello/page/views_sql.py"
    assert event["fault_code"] == "FAULT_SQL_INJECTION_TEST"
    assert event["parameters"] == [
        {"name": "file_path", "value": "hello/page/views_sql.py"}
    ]


def test_build_github_tool_event_rejects_off_target_fault_file():
    with pytest.raises(ValueError, match="may only access hello/page/views_sql.py"):
        fault_router_lambda.build_github_tool_event(
            tool_name="push_github_fix",
            tool_input={
                "file_path": "hello/page/views_db.py",
                "file_content": "updated",
                "commit_message": "[FAULT:FAULT_SQL_INJECTION_TEST] wrong file",
            },
            target_file="hello/page/views_sql.py",
            fault_code="FAULT_SQL_INJECTION_TEST",
        )


# This function runs the solution context load work used in this file.
def test_load_solution_context_returns_expected_fault_notes():
    sql_notes = fault_router_lambda.load_solution_context("FAULT_SQL_INJECTION_TEST")
    api_notes = fault_router_lambda.load_solution_context("FAULT_EXTERNAL_API_LATENCY")
    db_notes = fault_router_lambda.load_solution_context("FAULT_DB_TIMEOUT")

    assert "Target File: hello/page/views_sql.py" in sql_notes
    assert "parameter binding" in sql_notes
    assert "Target File: hello/page/views_api.py" in api_notes
    assert "retry loop" in api_notes
    assert "Target File: hello/page/views_db.py" in db_notes
    assert "statement_timeout" in db_notes


# This function runs the claude prompt includes packaged solution guidance work used in this file.
def test_build_claude_prompt_includes_known_good_solution():
    incident = {
        "fault_code": "FAULT_SQL_INJECTION_TEST",
        "raw_message": (
            "FAULT_SQL_INJECTION_TEST route=/test-fault/run "
            "reason=invalid_sql_executed"
        ),
    }
    analysis = {"summary": "Use the known-good SQL remediation."}

    prompt = fault_router_lambda.build_claude_prompt(
        incident=incident,
        analysis=analysis,
        target_file="hello/page/views_sql.py",
        target_function="test_fault_run",
        forbidden_for_this_fault=(
            "hello/page/_faulty_views_template.py",
            "hello/page/views.py",
        ),
        fix_hint="Use safe parameter binding.",
        solution_context=fault_router_lambda.load_solution_context(
            "FAULT_SQL_INJECTION_TEST"
        ),
    )

    assert "KNOWN_GOOD_SOLUTION:" in prompt
    assert "parameter binding" in prompt
    assert "Compare the current implementation to the packaged solution notes" in prompt
