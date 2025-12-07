import os
import time
import logging
import threading
from typing import List, Tuple, Dict
from enum import Enum
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

HOST = os.environ.get("HOST", "0.0.0.0")  # nosec B104 - Dockerized app needs to bind to all interfaces
PORT = int(os.environ.get("PORT", "8000"))


class SecondaryStatus(Enum):
    HEALTHY = "healthy"
    SUSPECTED = "suspected"
    UNHEALTHY = "unhealthy"


HB_INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "2.0"))
HB_TIMEOUT = float(os.environ.get("HEARTBEAT_TIMEOUT", "5.0"))
SUSPECT_THRESH = int(os.environ.get("SUSPECTED_THRESHOLD", "2"))
UNHEALTHY_THRESH = int(os.environ.get("UNHEALTHY_THRESHOLD", "5"))

SECONDARY_STATUS: Dict[str, Dict] = {}
STATUS_LOCK = threading.Lock()


def init_secondary_statuses():
    with STATUS_LOCK:
        for sec_url in SECONDARIES:
            if sec_url not in SECONDARY_STATUS:
                SECONDARY_STATUS[sec_url] = {
                    "status": SecondaryStatus.HEALTHY,
                    "last_heartbeat": time.time(),
                    "failures": 0,
                    "last_success": time.time()
                }


def check_secondary_health(sec_url: str) -> bool:
    try:
        response = requests.get(f"{sec_url}/health", timeout=HB_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.debug("Heartbeat to %s failed: %s", sec_url, e)
        return False


def update_secondary_status(sec_url: str, is_healthy: bool):
    with STATUS_LOCK:
        if sec_url not in SECONDARY_STATUS:
            SECONDARY_STATUS[sec_url] = {
                "status": SecondaryStatus.HEALTHY,
                "last_heartbeat": time.time(),
                "failures": 0,
                "last_success": time.time()
            }

        status_info = SECONDARY_STATUS[sec_url]
        status_info["last_heartbeat"] = time.time()

        if is_healthy:
            old_failures = status_info["failures"]
            status_info["failures"] = 0
            status_info["last_success"] = time.time()
            
            if status_info["status"] != SecondaryStatus.HEALTHY:
                logger.info("Heartbeat OK for %s, recovered from %s", sec_url, status_info["status"].value)
                status_info["status"] = SecondaryStatus.HEALTHY
        else:
            status_info["failures"] += 1
            failures = status_info["failures"]
            old_status = status_info["status"]

            if old_status == SecondaryStatus.HEALTHY:
                if failures >= SUSPECT_THRESH:
                    status_info["status"] = SecondaryStatus.SUSPECTED
                    logger.warning("Heartbeat failed for %s (%d failures) - marking as suspected", sec_url, failures)
            elif old_status == SecondaryStatus.SUSPECTED:
                if failures >= UNHEALTHY_THRESH:
                    status_info["status"] = SecondaryStatus.UNHEALTHY
                    logger.error("Heartbeat failed for %s (%d failures) - marking as unhealthy", sec_url, failures)
                elif failures < SUSPECT_THRESH:
                    status_info["status"] = SecondaryStatus.HEALTHY
                    logger.info("Heartbeat OK for %s, back to healthy", sec_url)
            elif old_status == SecondaryStatus.UNHEALTHY:
                if failures < SUSPECT_THRESH:
                    status_info["status"] = SecondaryStatus.HEALTHY
                    logger.info("Heartbeat OK for %s, recovered from unhealthy", sec_url)
                elif failures < UNHEALTHY_THRESH:
                    status_info["status"] = SecondaryStatus.SUSPECTED
                    logger.info("Heartbeat OK for %s, improved to suspected", sec_url)


def heartbeat_worker():
    init_secondary_statuses()
    logger.info("Heartbeat worker started (interval=%.1fs)", HB_INTERVAL)

    while True:
        time.sleep(HB_INTERVAL)
        for sec_url in SECONDARIES:
            is_healthy = check_secondary_health(sec_url)
            update_secondary_status(sec_url, is_healthy)


heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
heartbeat_thread.start()


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
    # Count only healthy/suspected secondaries for write concern validation
    init_secondary_statuses()
    with STATUS_LOCK:
        available_secondaries = [
            sec for sec in SECONDARIES
            if SECONDARY_STATUS.get(sec, {}).get("status", SecondaryStatus.HEALTHY) != SecondaryStatus.UNHEALTHY
        ]
    
    w = data.get("w")
    if w is None:
        w = len(available_secondaries) + 1
    else:
        w = int(w)
        max_w = len(available_secondaries) + 1
        if w < 1 or w > max_w:
            return jsonify({"error": f"Write concern w must be between 1 and {max_w} (only {len(available_secondaries)} healthy/suspected secondaries available)"}), 400

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
        with STATUS_LOCK:
            sec_status = SECONDARY_STATUS.get(sec, {}).get("status", SecondaryStatus.HEALTHY)
            if sec_status == SecondaryStatus.UNHEALTHY:
                logger.debug("Skipping %s (unhealthy) for seq=%d", sec, seq)
                return {"secondary": sec, "success": False, "error": "secondary is unhealthy", "skipped": True}
        
        try:
            url = f"{sec}/replicate"
            r = requests.post(url, json={"msg": msg, "seq": seq}, timeout=30)
            r.raise_for_status()
            ack_data = r.json()
            update_secondary_status(sec, True)
            return {"secondary": sec, "ack": ack_data.get("status", "ok"), "status_code": r.status_code, "success": True}
        except Exception as e:
            logger.warning("Replication to %s failed for seq=%d: %s", sec, seq, e)
            update_secondary_status(sec, False)
            return {"secondary": sec, "success": False, "error": str(e)}

    if required_acks == 0:
        logger.info("w=1: Replicating asynchronously (not waiting for ACKs)")
        for sec in available_secondaries:
            # Fire and forget - start replication in background
            def async_replicate(secondary):
                try:
                    url = f"{secondary}/replicate"
                    requests.post(url, json={"msg": msg, "seq": seq}, timeout=30)
                    logger.info("Replication completed to %s for seq=%d", secondary, seq)
                    update_secondary_status(secondary, True)
                except Exception as e:
                    logger.warning("Async replication to %s failed for seq=%d: %s", secondary, seq, e)
                    update_secondary_status(secondary, False)

            thread = threading.Thread(target=async_replicate, args=(sec,))
            thread.daemon = True
            thread.start()
            logger.info("Replication initiated to %s for seq=%d", sec, seq)

        # Calculate duration for w=1 (fast response, no waiting)
        duration_ms = int((time.time() - start) * 1000)
    else:
        if len(available_secondaries) == 0:
            return jsonify({
                "error": f"Write concern w={w} cannot be satisfied",
                "detail": "No healthy or suspected secondaries available",
                "required_acks": required_acks
            }), 502
        
        executor = ThreadPoolExecutor(max_workers=len(available_secondaries))
        try:
            futures = {executor.submit(replicate_to_secondary, sec): sec for sec in available_secondaries}

            response_ready = False
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    acks.append({"secondary": result["secondary"], "ack": result["ack"]})
                    logger.info("ACK from %s for seq=%d status=%d", result["secondary"], seq, result["status_code"])

                    if len(acks) >= required_acks and not response_ready:
                        response_ready = True
                        logger.info("Write concern w=%d satisfied with %d ACKs", w, len(acks))
                        duration_ms = int((time.time() - start) * 1000)
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

        if len(acks) < required_acks:
            return jsonify({
                "error": f"Write concern w={w} not satisfied",
                "detail": f"Required {required_acks} ACKs, got {len(acks)}",
                "failed_secondaries": failed_secondaries
            }), 502

        if 'duration_ms' not in locals():
            duration_ms = int((time.time() - start) * 1000)
    msg_list = [m for _, m in sorted(MESSAGES, key=lambda x: x[0])]
    logger.info("POST /messages completed w=%d, acks=%d in %d ms", w, len(acks), duration_ms)
    return jsonify({"messages": msg_list, "acks": acks, "w": w, "duration_ms": duration_ms}), 201


@app.get("/health")
def health():
    init_secondary_statuses()
    
    with STATUS_LOCK:
        secondary_statuses = {}
        for sec_url in SECONDARIES:
            if sec_url in SECONDARY_STATUS:
                status_info = SECONDARY_STATUS[sec_url]
                secondary_statuses[sec_url] = {
                    "status": status_info["status"].value,
                    "last_heartbeat": status_info["last_heartbeat"],
                    "failures": status_info["failures"],
                    "last_success": status_info["last_success"]
                }
            else:
                secondary_statuses[sec_url] = {
                    "status": SecondaryStatus.HEALTHY.value,
                    "last_heartbeat": 0,
                    "failures": 0,
                    "last_success": 0
                }
    
    return jsonify({
        "status": "ok",
        "count": len(MESSAGES),
        "secondaries": SECONDARIES,
        "secondary_statuses": secondary_statuses
    })


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)  # nosec B104 - Dockerized app needs to bind to all interfaces
