import os
import time
import logging
import threading
from typing import List, Tuple, Dict
from enum import Enum
from collections import defaultdict
from dataclasses import dataclass, field
from flask import Flask, request, jsonify
import requests

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

# Replication queues per secondary
REPLICATION_QUEUES: Dict[str, List[Tuple[int, str]]] = {sec: [] for sec in SECONDARIES}
REPLICATION_LOCK = threading.Lock()

# Track last delivered seq per secondary
DELIVERED_SEQ: Dict[str, int] = defaultdict(int)

# Write concern tracking
@dataclass
class AckTracker:
    required_acks: int
    acked_by: set = field(default_factory=set)
    event: threading.Event = field(default_factory=threading.Event)

ACK_TRACKERS: Dict[int, AckTracker] = {}
ACK_LOCK = threading.Lock()


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
                logger.info("Heartbeat: %s initialized as healthy", sec_url)


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
            logger.info("Heartbeat: %s initialized as healthy", sec_url)

        status_info = SECONDARY_STATUS[sec_url]
        status_info["last_heartbeat"] = time.time()

        if is_healthy:
            old_status = status_info["status"]
            status_info["failures"] = 0
            status_info["last_success"] = time.time()
            
            if old_status != SecondaryStatus.HEALTHY:
                if old_status == SecondaryStatus.UNHEALTHY:
                    logger.info("Heartbeat OK for %s: unhealthy -> healthy (recovered)", sec_url)
                elif old_status == SecondaryStatus.SUSPECTED:
                    logger.info("Heartbeat OK for %s: suspected -> healthy", sec_url)
                else:
                    logger.info("Heartbeat OK for %s: %s -> healthy", sec_url, old_status.value)
                status_info["status"] = SecondaryStatus.HEALTHY
        else:
            status_info["failures"] += 1
            failures = status_info["failures"]
            old_status = status_info["status"]

            if old_status == SecondaryStatus.HEALTHY:
                if failures >= SUSPECT_THRESH:
                    status_info["status"] = SecondaryStatus.SUSPECTED
                    logger.warning("Heartbeat failed for %s (%d failures): healthy -> suspected", sec_url, failures)
            elif old_status == SecondaryStatus.SUSPECTED:
                if failures >= UNHEALTHY_THRESH:
                    status_info["status"] = SecondaryStatus.UNHEALTHY
                    logger.error("Heartbeat failed for %s (%d failures): suspected -> unhealthy", sec_url, failures)
            elif old_status == SecondaryStatus.UNHEALTHY:
                # Still failing, remain unhealthy
                pass


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


def replication_worker(sec: str):
    """Background worker that retries replication to a specific secondary"""
    while True:
        seq_msg = None
        with REPLICATION_LOCK:
            q = REPLICATION_QUEUES.get(sec, [])
            if q:
                seq_msg = q[0]

        if seq_msg is None:
            time.sleep(0.05)
            continue

        seq, msg = seq_msg

        # Track replication attempts
        attempt_count = DELIVERED_SEQ.get(f"{sec}_attempts_{seq}", 0) + 1
        DELIVERED_SEQ[f"{sec}_attempts_{seq}"] = attempt_count
        logger.info("Replication attempt %d to %s for seq=%d", attempt_count, sec, seq)

        try:
            r = requests.post(f"{sec}/replicate", json={"msg": msg, "seq": seq}, timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    with REPLICATION_LOCK:
                        q = REPLICATION_QUEUES.get(sec, [])
                        if q and q[0] == seq_msg:
                            q.pop(0)

                    DELIVERED_SEQ[sec] = max(DELIVERED_SEQ[sec], seq)
                    # Clean up attempt counter
                    DELIVERED_SEQ.pop(f"{sec}_attempts_{seq}", None)

                    with ACK_LOCK:
                        tracker = ACK_TRACKERS.get(seq)
                        if tracker and sec not in tracker.acked_by:
                            tracker.acked_by.add(sec)
                            if len(tracker.acked_by) >= tracker.required_acks:
                                tracker.event.set()
                else:
                    time.sleep(0.2)
            else:
                time.sleep(0.2)
        except Exception as e:
            logger.warning("Replication to %s failed for seq=%d: %s", sec, seq, e)
            time.sleep(0.2)


def start_replication_workers():
    """Start daemon threads for each secondary"""
    for sec in SECONDARIES:
        t = threading.Thread(target=replication_worker, args=(sec,), daemon=True)
        t.start()
        logger.info("Started replication worker for %s", sec)

start_replication_workers()


@app.get("/messages")
def list_messages():
    msg_list = [msg for _, msg in sorted(MESSAGES, key=lambda x: x[0])]
    return jsonify({"messages": msg_list})

def has_quorum() -> bool:
    """Check if master has quorum (majority of nodes healthy)"""
    if not SECONDARIES:
        return True
    
    init_secondary_statuses()
    with STATUS_LOCK:
        healthy_count = sum(
            1 for sec in SECONDARIES
            if SECONDARY_STATUS.get(sec, {}).get("status", SecondaryStatus.HEALTHY) == SecondaryStatus.HEALTHY
        )
    
    majority = (len(SECONDARIES) + 1) // 2 + 1
    quorum = 1 + healthy_count
    return quorum >= majority


@app.post("/messages")
def append_message():
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, str):
        return jsonify({"error": "Expected JSON with string field 'msg'"}), 400

    # Quorum check
    if not has_quorum():
        return jsonify({
            "error": "no quorum, master is read-only",
            "detail": "Not enough healthy nodes to form a majority"
        }), 503

    start_ts = time.time()

    # Validate and normalize w
    w = data.get("w")
    if w is None:
        w = len(SECONDARIES) + 1
    else:
        w = int(w)
        if not (1 <= w <= len(SECONDARIES) + 1):
            return jsonify({"error": f"Write concern w must be between 1 and {len(SECONDARIES) + 1}"}), 400

    # Assign seq
    global SEQ_COUNTER
    with SEQ_LOCK:
        SEQ_COUNTER += 1
        seq = SEQ_COUNTER

    MESSAGES.append((seq, msg))
    logger.info("Appended locally seq=%d msg=%s w=%d", seq, msg, w)

    # Enqueue message for all secondaries
    with REPLICATION_LOCK:
        for sec in SECONDARIES:
            REPLICATION_QUEUES.setdefault(sec, []).append((seq, msg))

    required_acks = w - 1
    tracker = None

    if required_acks > 0 and SECONDARIES:
        tracker = AckTracker(required_acks=required_acks)
        with ACK_LOCK:
            ACK_TRACKERS[seq] = tracker

        # Wait until enough different secondaries have acked (with timeout to prevent indefinite hangs)
        # Timeout: 30 seconds per required ACK, minimum 60 seconds
        timeout_seconds = max(60, required_acks * 30)
        wait_result = tracker.event.wait(timeout=timeout_seconds)

        # Verify condition after wake-up
        if not wait_result or len(tracker.acked_by) < required_acks:
            with ACK_LOCK:
                ACK_TRACKERS.pop(seq, None)
            return jsonify({
                "error": f"Write concern w={w} not satisfied",
                "detail": f"Required {required_acks} ACKs, got {len(tracker.acked_by)} (timeout: {timeout_seconds}s)",
                "acked_by": list(tracker.acked_by)
            }), 502

    duration_ms = int((time.time() - start_ts) * 1000)

    # Prepare response
    msg_list = [m for _, m in sorted(MESSAGES, key=lambda x: x[0])]
    acks = []
    if tracker:
        acks = [{"secondary": s} for s in tracker.acked_by]
        with ACK_LOCK:
            ACK_TRACKERS.pop(seq, None)

    if required_acks > 0:
        logger.info("Write concern w=%d satisfied with %d ACKs in %d ms", w, len(acks), duration_ms)
    logger.info("POST /messages completed w=%d, acks=%d in %d ms", w, len(acks), duration_ms)
    return jsonify({
        "messages": msg_list,
        "acks": acks,
        "w": w,
        "duration_ms": duration_ms,
    }), 201


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
