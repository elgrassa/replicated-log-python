import os
import time
import json
import logging
import threading
from typing import List, Tuple
from flask import Flask, request, jsonify
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("master")

# Messages stored with sequence numbers for ordering and deduplication
MESSAGES: List[Tuple[int, str]] = []
SEQ_COUNTER = 0
SEQ_LOCK = threading.Lock()
SECONDARIES = [u.strip() for u in os.environ.get("SECONDARIES", "").split(",") if u.strip()]

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

@app.get("/messages")
def list_messages():
    msg_list = [msg for _, msg in sorted(MESSAGES, key=lambda x: x[0])]
    return jsonify({"messages": msg_list})

@app.post("/messages")
def append_message():
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, str):
        return jsonify({"error": "Expected JSON with string field 'msg'"}), 400

    # Write concern: w=1 means master only, w=2 means master+1 secondary, etc.
    # Defaults to all secondaries if not specified (backward compat)
    w = data.get("w")
    if w is None:
        w = len(SECONDARIES) + 1
    else:
        w = int(w)
        if w < 1 or w > len(SECONDARIES) + 1:
            return jsonify({"error": f"Write concern w must be between 1 and {len(SECONDARIES) + 1}"}), 400

    start = time.time()
    
    # Thread-safe sequence number assignment
    global SEQ_COUNTER
    with SEQ_LOCK:
        SEQ_COUNTER += 1
        seq = SEQ_COUNTER
    
    MESSAGES.append((seq, msg))
    logger.info("Appended locally seq=%d msg=%s", seq, msg)

    # w includes master, so we need w-1 ACKs from secondaries
    # We ALWAYS replicate to all secondaries for eventual consistency
    # w only controls how many ACKs we wait for before responding
    required_acks = w - 1
    acks = []
    failed_secondaries = []
    
    def replicate_to_secondary(sec: str) -> dict:
        """Replicate to a single secondary, return result"""
        try:
            url = f"{sec}/replicate"
            r = requests.post(url, json={"msg": msg, "seq": seq}, timeout=30)
            r.raise_for_status()
            ack_data = r.json()
            return {"secondary": sec, "ack": ack_data.get("status", "ok"), "status_code": r.status_code, "success": True}
        except Exception as e:
            logger.warning("Replication to %s failed for seq=%d: %s", sec, seq, e)
            return {"secondary": sec, "success": False, "error": str(e)}
    
    if required_acks == 0:
        # w=1: Start replication asynchronously, don't wait
        logger.info("w=1: Replicating to all secondaries asynchronously (not waiting for ACKs)")
        for sec in SECONDARIES:
            # Fire and forget - start replication in background
            def async_replicate(secondary):
                try:
                    url = f"{secondary}/replicate"
                    requests.post(url, json={"msg": msg, "seq": seq}, timeout=30)
                    logger.info("Replication completed to %s for seq=%d", secondary, seq)
                except Exception as e:
                    logger.warning("Async replication to %s failed for seq=%d: %s", secondary, seq, e)
            
            thread = threading.Thread(target=async_replicate, args=(sec,))
            thread.daemon = True
            thread.start()
            logger.info("Replication initiated to %s for seq=%d", sec, seq)
        
        # Calculate duration for w=1 (fast response, no waiting)
        duration_ms = int((time.time() - start) * 1000)
    else:
        # w > 1: Replicate to all secondaries concurrently, wait for required_acks
        # Use concurrent replication - respond as soon as we have enough ACKs
        executor = ThreadPoolExecutor(max_workers=len(SECONDARIES))
        try:
            futures = {executor.submit(replicate_to_secondary, sec): sec for sec in SECONDARIES}
            
            response_ready = False
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    acks.append({"secondary": result["secondary"], "ack": result["ack"]})
                    logger.info("ACK from %s for seq=%d status=%d", result["secondary"], seq, result["status_code"])
                    
                    # If we have enough ACKs, we can respond immediately
                    if len(acks) >= required_acks and not response_ready:
                        response_ready = True
                        logger.info("Write concern w=%d satisfied with %d ACKs, responding (remaining replications continue)", w, len(acks))
                        # Calculate duration now, right when we have enough ACKs
                        duration_ms = int((time.time() - start) * 1000)
                        # Cancel remaining futures - we have enough ACKs
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        break
                else:
                    failed_secondaries.append(result["secondary"])
                    logger.warning("Replication failed to %s for seq=%d", result["secondary"], seq)
        finally:
            # Shutdown executor (won't wait for cancelled futures)
            executor.shutdown(wait=False)
        
        # Check if we got enough ACKs to satisfy write concern
        if len(acks) < required_acks:
            return jsonify({
                "error": f"Write concern w={w} not satisfied",
                "detail": f"Required {required_acks} ACKs, got {len(acks)}",
                "failed_secondaries": failed_secondaries
            }), 502
        
        # If we didn't break early, calculate duration now
        if 'duration_ms' not in locals():
            duration_ms = int((time.time() - start) * 1000)
    msg_list = [m for _, m in sorted(MESSAGES, key=lambda x: x[0])]
    logger.info("POST /messages completed w=%d, acks=%d in %d ms", w, len(acks), duration_ms)
    return jsonify({"messages": msg_list, "acks": acks, "w": w, "duration_ms": duration_ms}), 201

@app.get("/health")
def health():
    return jsonify({"status": "ok", "secondaries": SECONDARIES, "count": len(MESSAGES)})

if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
