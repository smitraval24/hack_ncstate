"""This file keeps tests for the test part of the project so new changes stay safe."""

import pytest

from config import settings
from hello.app import create_app
from hello.extensions import db as _db


# This function handles the app work for this file.
@pytest.fixture(scope="session")
def app():
    """
    Setup our flask test app, this only gets executed once.

    :return: Flask app
    """
    db_uri = settings.SQLALCHEMY_DATABASE_URI

    if "?" in db_uri:
        db_uri = db_uri.replace("?", "_test?")
    else:
        db_uri = f"{db_uri}_test"

    params = {
        "DEBUG": False,
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": db_uri,
    }

    _app = create_app(settings_override=params)

    # Establish an application context before running the tests.
    ctx = _app.app_context()
    ctx.push()

    yield _app

    ctx.pop()


# This function handles the client work for this file.
@pytest.fixture(scope="function")
def client(app):
    """
    Setup an app client, this gets executed for each test function.

    :param app: Pytest fixture
    :return: Flask app client
    """
    yield app.test_client()


# This function handles the db work for this file.
@pytest.fixture(scope="session")
def db(app):
    """
    Setup our database, this only gets executed once per session.

    :param app: Pytest fixture
    :return: SQLAlchemy database session
    """
    _db.drop_all()
    _db.create_all()

    return _db


# This function handles the session work for this file.
@pytest.fixture(scope="function")
def session(db):
    """
    Allow very fast tests by using rollbacks and nested sessions. This does
    require that your database supports SQL savepoints, and Postgres does.

    Read more about this at:
    http://stackoverflow.com/a/26624146

    :param db: Pytest fixture
    :return: None
    """
    db.session.begin_nested()

    yield db.session

    db.session.rollback()
