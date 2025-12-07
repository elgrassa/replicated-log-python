import os
import time
import logging
import threading
import random
from typing import List, Tuple
from flask import Flask, request, jsonify

app = Flask(__name__)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("secondary")

PORT = int(os.environ.get("PORT", "8001"))
DELAY_MS = int(os.environ.get("DELAY_MS", "0"))

# Messages with sequence numbers for ordering and deduplication
MESSAGES: List[Tuple[int, str]] = []
MESSAGES_LOCK = threading.Lock()


@app.get("/messages")
def list_messages():
    with MESSAGES_LOCK:
        ordered = sorted(MESSAGES, key=lambda x: x[0])
        visible = []
        if ordered:
            expected = ordered[0][0]
            for seq, msg in ordered:
                if seq == expected:
                    visible.append(msg)
                    expected += 1
                elif seq > expected:
                    logger.info("Gap detected: hiding seq=%d until seq=%d arrives", seq, expected)
                    break
                else:
                    continue
    return jsonify({"messages": visible})

@app.post("/replicate")
def replicate():
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, str):
        return jsonify({"error": "Expected JSON with string field 'msg'"}), 400

    seq = data.get("seq", 0)

    if DELAY_MS > 0:
        logger.info("Simulating delay %d ms", DELAY_MS)
        time.sleep(DELAY_MS / 1000.0)

    # Atomic check-and-insert to prevent race conditions
    with MESSAGES_LOCK:
        # Check for duplicate sequence number (retry handling)
        if any(seq_num == seq for seq_num, _ in MESSAGES):
            logger.info("Duplicate seq %d detected, skipping replication", seq)
            return jsonify({"status": "ok", "idx": -1, "duplicate": True})

        # Insert in sequence order to maintain total ordering
        insert_pos = 0
        for i, (seq_num, _) in enumerate(MESSAGES):
            if seq_num < seq:
                insert_pos = i + 1
            else:
                break

        MESSAGES.insert(insert_pos, (seq, msg))
        logger.info("Replicated seq=%d msg=%s (pos=%d)", seq, msg, insert_pos)

    # Randomly simulate internal error after storing (for retry & dedup tests)
    if random.random() < 0.2:
        return jsonify({"error": "simulated internal error", "seq": seq}), 500

    return jsonify({"status": "ok", "seq": seq})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "count": len(MESSAGES), "delay_ms": DELAY_MS})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)  # nosec B104 - Dockerized app needs to bind to all interfaces
