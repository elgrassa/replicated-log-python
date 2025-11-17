import os
import time
import json
import logging
from typing import List
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("master")

MESSAGES: List[str] = []
SECONDARIES = [u.strip() for u in os.environ.get("SECONDARIES", "").split(",") if u.strip()]

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

@app.get("/messages")
def list_messages():
    return jsonify({"messages": MESSAGES})

@app.post("/messages")
def append_message():
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, str):
        return jsonify({"error": "Expected JSON with string field 'msg'"}), 400

    start = time.time()
    # Append locally
    MESSAGES.append(msg)
    idx = len(MESSAGES) - 1
    logger.info("Appended locally idx=%s msg=%s", idx, msg)

    # Replicate to all secondaries (blocking)
    acks = []
    for sec in SECONDARIES:
        try:
            url = f"{sec}/replicate"
            r = requests.post(url, json={"msg": msg}, timeout=30)
            r.raise_for_status()
            ack = r.json().get("status", "ok")
            acks.append({"secondary": sec, "ack": ack})
            logger.info("Replicated to %s ok", sec)
        except Exception as e:
            logger.exception("Replication to %s failed: %s", sec, e)
            return jsonify({"error": f"Replication to {sec} failed", "detail": str(e)}), 502

    duration_ms = int((time.time() - start) * 1000)
    logger.info("POST /messages completed with %d acks in %d ms", len(acks), duration_ms)
    return jsonify({"messages": MESSAGES, "acks": acks, "duration_ms": duration_ms}), 201

@app.get("/health")
def health():
    return jsonify({"status": "ok", "secondaries": SECONDARIES, "count": len(MESSAGES)})

if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
