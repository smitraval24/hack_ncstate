"""This file handles the extensions logic for the hello part of the project."""

from flask_debugtoolbar import DebugToolbarExtension
from flask_sqlalchemy import SQLAlchemy
from flask_static_digest import FlaskStaticDigest

debug_toolbar = DebugToolbarExtension()
db = SQLAlchemy()
flask_static_digest = FlaskStaticDigest()
