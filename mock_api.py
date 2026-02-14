from flask import Flask, jsonify
import random, time, os

app = Flask(__name__)


@app.route("/data")
def data():
    fault = os.getenv("API_FAULT_MODE", "")

    if "latency" in fault and random.random() < 0.6:
        time.sleep(random.uniform(2, 8))

    if "error" in fault and random.random() < 0.3:
        return jsonify({"error": "upstream failure"}), 500

    return jsonify({"value": 42})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
