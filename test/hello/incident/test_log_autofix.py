"""Tests for log-triggered auto-remediation glue."""

from hello.incident.log_autofix import FaultLogAutoFixHandler, parse_fault_log


class TestLogAutoFix:
    def test_parse_fault_log_success(self):
        event = parse_fault_log(
            "FAULT_DB_TIMEOUT route=/test-fault/db-timeout "
            "reason=db_timeout_or_pool_exhaustion latency=5.01"
        )

        assert event is not None
        assert event.error_code == "FAULT_DB_TIMEOUT"
        assert event.route == "/test-fault/db-timeout"
        assert event.reason == "db_timeout_or_pool_exhaustion"
        assert event.latency == "5.01"

    def test_parse_fault_log_ignores_non_fault(self):
        assert parse_fault_log("hello world") is None

    def test_handler_dedupes_same_fault_event(self, app, monkeypatch):
        from hello.incident import log_autofix

        handler = FaultLogAutoFixHandler(app, dedupe_window_seconds=60.0)

        seen = []

        def fake_process(event):
            seen.append(event)

        class ImmediateThread:
            def __init__(self, target, args, daemon):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        monkeypatch.setattr(log_autofix.threading, "Thread", ImmediateThread)
        monkeypatch.setattr(handler, "_process", fake_process)

        logger = app.logger
        logger.addHandler(handler)
        try:
            logger.error(
                "FAULT_SQL_INJECTION_TEST route=/test-fault/run "
                "reason=invalid_sql_executed"
            )
            logger.error(
                "FAULT_SQL_INJECTION_TEST route=/test-fault/run "
                "reason=invalid_sql_executed"
            )
        finally:
            logger.removeHandler(handler)

        assert len(seen) == 1
