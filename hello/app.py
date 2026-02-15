import click
from celery import Celery, Task
from flask import Flask
from werkzeug.debug import DebuggedApplication
from werkzeug.middleware.proxy_fix import ProxyFix

from hello.extensions import db, debug_toolbar, flask_static_digest
from hello.incident.log_autofix import register_fault_log_autofix
from hello.incident.views import incident_bp
from hello.page.views import page
from hello.up.views import up
from hello.developer.views import developer


def create_celery_app(app=None):
    """
    Create a new Celery app and tie together the Celery config to the app's
    config. Wrap all tasks in the context of the application.

    :param app: Flask app
    :return: Celery app
    """
    app = app or create_app()

    class FlaskTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery = Celery(app.import_name, task_cls=FlaskTask)
    celery.conf.update(app.config.get("CELERY_CONFIG", {}))
    celery.set_default()
    app.extensions["celery"] = celery

    return celery


def create_app(settings_override=None):
    """
    Create a Flask application using the app factory pattern.

    :param settings_override: Override settings
    :return: Flask app
    """
    app = Flask(__name__, static_folder="../public", static_url_path="")

    app.config.from_object("config.settings")

    if settings_override:
        app.config.update(settings_override)

    middleware(app)

    app.register_blueprint(up)
    app.register_blueprint(page)
    app.register_blueprint(developer)
    app.register_blueprint(incident_bp)

    extensions(app)
    register_fault_log_autofix(app)
    register_cli(app)

    return app


def register_cli(app):
    """Register custom Flask CLI commands."""

    @app.cli.command("seed-kb")
    def seed_kb_command():
        """Seed the Backboard RAG knowledge base with 15 example incidents."""
        from hello.incident.seed_knowledge_base import seed_knowledge_base

        click.echo("Uploading 15 knowledge-base entries to Backboard …")
        results = seed_knowledge_base()
        ok = sum(1 for r in results if r.get("document_id"))
        fail = len(results) - ok
        click.echo(f"Done: {ok} uploaded, {fail} failed.")
        for r in results:
            status = r.get("document_id") or "FAILED"
            click.echo(f"  {r['filename']}  →  {status}")


def extensions(app):
    """
    Register 0 or more extensions (mutates the app passed in).

    :param app: Flask application instance
    :return: None
    """
    debug_toolbar.init_app(app)
    db.init_app(app)
    flask_static_digest.init_app(app)

    return None


def middleware(app):
    """
    Register 0 or more middleware (mutates the app passed in).

    :param app: Flask application instance
    :return: None
    """
    # Enable the Flask interactive debugger in the brower for development.
    if app.debug:
        app.wsgi_app = DebuggedApplication(app.wsgi_app, evalex=True)

    # Set the real IP address into request.remote_addr when behind a proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app)

    return None


celery_app = create_celery_app()
