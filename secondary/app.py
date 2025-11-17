import os
import time
import logging
from typing import List
from flask import Flask, request, jsonify

app = Flask(__name__)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("secondary")

PORT = int(os.environ.get("PORT", "8001"))
DELAY_MS = int(os.environ.get("DELAY_MS", "0"))

MESSAGES: List[str] = []

@app.get("/messages")
def list_messages():
    return jsonify({"messages": MESSAGES})

@app.post("/replicate")
def replicate():
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, str):
        return jsonify({"error": "Expected JSON with string field 'msg'"}), 400

    if DELAY_MS > 0:
        logger.info("Simulating delay %d ms", DELAY_MS)
        time.sleep(DELAY_MS / 1000.0)

    MESSAGES.append(msg)
    idx = len(MESSAGES) - 1
    logger.info("Replicated idx=%s msg=%s", idx, msg)
    return jsonify({"status": "ok", "idx": idx})

@app.get("/health")
def health():
    return jsonify({"status": "ok", "count": len(MESSAGES), "delay_ms": DELAY_MS})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
