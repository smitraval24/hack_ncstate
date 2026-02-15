import logging

import click
import watchtower
import boto3
from celery import Celery, Task
from flask import Flask
from werkzeug.debug import DebuggedApplication
from werkzeug.middleware.proxy_fix import ProxyFix

from hello.extensions import db, debug_toolbar, flask_static_digest
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
    register_cli(app)
    configure_cloudwatch_logging(app)

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


def configure_cloudwatch_logging(app):
    """
    Attach an AWS CloudWatch Logs handler to the Flask app logger
    and the root Python logger so that ERROR-level (and above) logs
    are shipped to CloudWatch automatically.

    Controlled by the CLOUDWATCH_ENABLED env-var / config flag.
    When disabled no AWS calls are made, keeping local dev simple.

    :param app: Flask application instance
    :return: None
    """
    if not app.config.get("CLOUDWATCH_ENABLED"):
        app.logger.debug("CloudWatch logging is disabled")
        return

    region = app.config.get("AWS_REGION", "us-east-1")
    log_group = app.config.get("CLOUDWATCH_LOG_GROUP", "hello-app")
    log_stream = app.config.get("CLOUDWATCH_LOG_STREAM", "error-logs")
    log_level_name = app.config.get("CLOUDWATCH_LOG_LEVEL", "ERROR")
    log_level = getattr(logging, log_level_name.upper(), logging.ERROR)

    boto3_client = boto3.client("logs", region_name=region)

    cw_handler = watchtower.CloudWatchLogHandler(
        log_group_name=log_group,
        log_stream_name=log_stream,
        boto3_client=boto3_client,
        send_interval=10,
        create_log_group=True,
        create_log_stream=True,
    )

    cw_handler.setLevel(log_level)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    )
    cw_handler.setFormatter(formatter)

    # Attach to the Flask app logger.
    app.logger.addHandler(cw_handler)

    # Also attach to the root logger so that library / module loggers
    # (e.g. incident.analyzer, incident.rag_service) are captured too.
    logging.getLogger().addHandler(cw_handler)

    app.logger.info(
        "CloudWatch logging enabled → group=%s stream=%s level=%s",
        log_group,
        log_stream,
        log_level_name,
    )


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
