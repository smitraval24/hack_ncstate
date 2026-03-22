"""This file handles the mock api logic for the hack ncstate part of the project."""

import os
import random
import time

from flask import Flask, jsonify

app = Flask(__name__)


# This function handles the data work for this file.
@app.route("/data")
def data():
    fault = os.getenv("API_FAULT_MODE", "")
    fault_modes = {part.strip().lower() for part in fault.split(",") if part.strip()}

    has_latency = "latency" in fault_modes
    has_wrong_data = "wrong_data" in fault_modes or "error" in fault_modes
    roll = random.random()

    if has_latency and has_wrong_data:
        if roll < 0.6:
            time.sleep(random.uniform(3.4, 8.0))
        elif roll < 0.9:
            return jsonify({"value": "forty-two", "source": "corrupted"}), 200
    elif has_latency and roll < 0.6:
        time.sleep(random.uniform(3.4, 8.0))
    elif has_wrong_data and roll < 0.3:
        return jsonify({"value": "forty-two", "source": "corrupted"}), 200

    return jsonify({"value": 42})


# This function handles the health work for this file.
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
