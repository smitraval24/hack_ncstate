"""This file handles the views logic for the up part of the project."""

from flask import Blueprint
from sqlalchemy import text

from hello.extensions import db
from hello.initializers import redis

# This blueprint groups related routes for this part of the app.
up = Blueprint("up", __name__, template_folder="templates", url_prefix="/up")


# This function handles the index work for this file.
@up.get("/")
def index():
    return ""


# This function handles the databases work for this file.
@up.get("/databases")
def databases():
    redis.ping()
    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return ""
