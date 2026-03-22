"""This file handles the views logic for the up part of the project."""

import os

from flask import Blueprint, jsonify
from sqlalchemy import text

from hello.extensions import db

# This blueprint groups related routes for this part of the app.
up = Blueprint("up", __name__, template_folder="templates", url_prefix="/up")


# This function handles the index work for this file.
@up.get("/")
def index():
    return ""


# This function handles the databases work for this file.
@up.get("/databases")
def databases():
    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return ""


# This function handles the build metadata work for this file.
@up.get("/build")
def build():
    build_sha = os.getenv("BUILD_SHA", "").strip()
    return jsonify(
        {
            "build_sha": build_sha,
            "build_short_sha": build_sha[:7] if build_sha else "local",
        }
    )
