from flask import url_for

from lib.test import ViewTestMixin


class TestPage(ViewTestMixin):
    def test_home_page(self):
        """Home page should respond with a success 200."""
        response = self.client.get(url_for("page.home"))

        assert response.status_code == 200

    def test_fault_button_emits_structured_fault_log(
        self, monkeypatch, caplog
    ):
        from hello.page import views as page_views

        monkeypatch.setattr(page_views, "ENABLE_FAULT_INJECTION", True)

        def raise_sql_error(*_args, **_kwargs):
            raise RuntimeError("invalid sql")

        monkeypatch.setattr(
            page_views.db.session,
            "execute",
            raise_sql_error,
        )

        caplog.set_level("ERROR")

        response = self.client.post("/test-fault/run")

        assert response.status_code == 500
        assert b"Fault triggered" in response.data
        assert any(
            "FAULT_SQL_INJECTION_TEST route=/test-fault/run" in rec.message
            for rec in caplog.records
        )
