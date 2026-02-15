"""Tests for deterministic agent workflow planning."""

import json
from pathlib import Path

from hello.incident.agent_workflow import (
    approve_plan,
    build_agent_plan,
    execute_approved_action,
)
from hello.incident.models import Incident


class TestAgentWorkflow:
    def test_build_agent_plan_uses_known_fault_playbook(self, app, session):
        inc = Incident(
            error_code="FAULT_DB_TIMEOUT",
            symptoms="high latency and queue pool timeout",
            breadcrumbs=json.dumps(["pg_sleep_executed", "queue_pool_limit"]),
            rag_response=json.dumps(
                {
                    "content": "Root cause likely queuepool exhaustion from pg_sleep",
                    "retrieved_memories": [{"id": "mem1"}],
                    "retrieved_files": ["kb_db_timeout_001.txt"],
                }
            ),
        )
        session.add(inc)
        session.flush()

        plan = build_agent_plan(inc)

        assert plan["decision"] == "ready_for_approval"
        assert plan["requires_approval"] is True
        assert plan["selected_action"]["action_id"] == "fix_fault_db_timeout"
        assert plan["selected_action"]["script_path"].endswith("fix_fault_db_timeout.sh")
        assert plan["confidence"] > 0.7

    def test_build_agent_plan_falls_back_to_manual(self, app, session):
        inc = Incident(
            error_code="UNKNOWN",
            symptoms="unknown incident",
            rag_response=json.dumps({"content": "not enough context"}),
        )
        session.add(inc)
        session.flush()

        plan = build_agent_plan(inc)

        assert plan["decision"] == "manual_triage_required"
        assert plan["selected_action"]["action_id"] == "manual_triage"
        assert plan["confidence"] == 0.0

    def test_approve_plan_requires_explicit_approval(self):
        plan = {
            "selected_action": {
                "action_id": "fix_fault_sql_injection",
                "script_path": "scripts/remediation/fix_fault_sql_injection.sh",
                "verification_hint": "verify endpoint",
            }
        }

        denied = approve_plan(plan, approved=False)
        assert denied["status"] == "approval_required"

        approved = approve_plan(plan, approved=True)
        assert approved["status"] == "approved_for_pipeline"

    def test_execute_approved_action_runs_known_script(self):
        approved_payload = {
            "status": "approved_for_pipeline",
            "execution": {
                "action_id": "fix_fault_sql_injection",
                "script_path": "scripts/remediation/fix_fault_sql_injection.sh",
                "verification_hint": "verify endpoint",
            },
        }
        project_root = Path(__file__).resolve().parents[3]

        result = execute_approved_action(
            approved_payload,
            project_root=project_root,
        )

        assert result["status"] == "executed"
        assert result["execution_result"]["return_code"] == 0

    def test_execute_approved_action_blocks_unknown_script(self):
        approved_payload = {
            "status": "approved_for_pipeline",
            "execution": {
                "action_id": "unknown",
                "script_path": "scripts/remediation/not_allowed.sh",
            },
        }
        project_root = Path(__file__).resolve().parents[3]

        result = execute_approved_action(
            approved_payload,
            project_root=project_root,
        )

        assert result["status"] == "blocked"
