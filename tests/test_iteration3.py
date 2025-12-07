"""
Iteration 3 Tests: Retry, Blocking, Catch-up, Total Order
"""
import os
import time
import json
import threading
import subprocess
from typing import List

import requests
import yaml

BASE = "http://localhost"
MASTER_PORT = int(os.environ.get("MASTER_PORT", "8000"))
MASTER = f"{BASE}:{MASTER_PORT}"


def get_secondary_ports() -> List[int]:
    """Discover secondary ports from docker-compose.yml or docker compose ps"""
    ports: List[int] = []
    try:
        with open("docker-compose.yml", "r") as f:
            compose = yaml.safe_load(f)

        for svc_name, cfg in compose.get("services", {}).items():
            if svc_name.startswith("secondary"):
                for port_mapping in cfg.get("ports", []):
                    if isinstance(port_mapping, str):
                        host_port = int(port_mapping.split(":")[0])
                    else:
                        host_port = port_mapping.get("published", 0)
                    if host_port != MASTER_PORT and host_port not in ports:
                        ports.append(host_port)
        ports.sort()
    except (FileNotFoundError, yaml.YAMLError, KeyError, ValueError):
        pass

    if not ports:
        try:
            cmd = ["docker", "compose", "ps", "--format", "json"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True  # nosec B603
            )
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if not line:
                    continue
                c = json.loads(line)
                if c.get("Service", "").startswith("secondary"):
                    for pub in c.get("Publishers", []):
                        host_port = pub.get("PublishedPort")
                        if host_port and host_port != MASTER_PORT and host_port not in ports:
                            ports.append(host_port)
            ports.sort()
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            pass

    # Fallback for local dev
    if not ports:
        return [8001, 8002]

    return ports


SECONDARY_PORTS = get_secondary_ports()
SECONDARIES = [f"{BASE}:{p}" for p in SECONDARY_PORTS]


def _get(url: str):
    """GET request helper"""
    return requests.get(url, timeout=5).json()


def _post(url: str, json_body: dict):
    """POST request helper"""
    r = requests.post(url, json=json_body, timeout=60)
    r.raise_for_status()
    return r


def stop_secondary2():
    """Stop secondary2 container"""
    subprocess.run(
        ["docker", "compose", "stop", "secondary2"],
        capture_output=True,
        text=True,
    )


def start_secondary2():
    """Start secondary2 container and wait for it to be healthy"""
    subprocess.run(
        ["docker", "compose", "start", "secondary2"],
        capture_output=True,
        text=True,
    )
    # Wait for health check
    s2_url = SECONDARIES[1] if len(SECONDARIES) > 1 else SECONDARIES[0]
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            resp = requests.get(f"{s2_url}/health", timeout=2)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise TimeoutError("Secondary2 did not become healthy in time")


def test_iter3_self_check_scenario():
    """
    Self-check acceptance test:
    Start M+S1, keep S2 down.
    Msg1 (w=1)   -> OK
    Msg2 (w=2)   -> OK
    Msg3 (w=3)   -> Wait until S2 up
    Msg4 (w=1)   -> OK
    After S2 up  -> S2 has [Msg1, Msg2, Msg3, Msg4]
    """
    # Ensure secondary2 is stopped
    stop_secondary2()
    time.sleep(2)

    # Msg1, w=1
    r1 = _post(f"{MASTER}/messages", {"msg": "Msg1", "w": 1})
    body1 = r1.json()
    assert body1["w"] == 1, f"Expected w=1, got {body1.get('w')}"
    assert body1.get("duration_ms", 0) < 100, "w=1 should return quickly"

    # Msg2, w=2
    r2 = _post(f"{MASTER}/messages", {"msg": "Msg2", "w": 2})
    body2 = r2.json()
    assert body2["w"] == 2, f"Expected w=2, got {body2.get('w')}"
    assert len(body2.get("acks", [])) >= 1, "w=2 should get at least 1 ACK"

    # Msg3, w=3 (blocked until S2 up)
    msg3_result = {"response": None, "error": None}

    def post_msg3():
        try:
            msg3_result["response"] = _post(f"{MASTER}/messages", {"msg": "Msg3", "w": 3})
        except Exception as e:
            msg3_result["error"] = e

    t_msg3 = threading.Thread(target=post_msg3)
    t_msg3.start()

    # Give msg3 time to reach the "waiting for S2" state
    time.sleep(1)
    assert t_msg3.is_alive(), "Msg3 (w=3) should be blocked while S2 is down"

    # Msg4, w=1 should still succeed quickly
    start_w1 = time.time()
    r4 = _post(f"{MASTER}/messages", {"msg": "Msg4", "w": 1})
    elapsed_w1 = time.time() - start_w1
    body4 = r4.json()
    assert body4["w"] == 1, f"Expected w=1, got {body4.get('w')}"
    assert elapsed_w1 < 2, f"w=1 write must not be blocked by w=3 request (took {elapsed_w1}s)"

    # Verify Msg3 is still blocked
    assert t_msg3.is_alive(), "Msg3 should still be blocked"

    # Start S2 so that retries can succeed and Msg3 can complete
    start_secondary2()

    t_msg3.join(timeout=30)
    assert not t_msg3.is_alive(), "Msg3 (w=3) should finish after S2 is up"
    assert msg3_result["response"] is not None, "Msg3 should have completed successfully"
    body3 = msg3_result["response"].json()
    assert body3["w"] == 3, f"Expected w=3, got {body3.get('w')}"
    assert len(body3.get("acks", [])) >= 2, "w=3 should get ACKs from both secondaries"

    # Eventually, S2 should have all four messages in order
    s2_url = SECONDARIES[1] if len(SECONDARIES) > 1 else SECONDARIES[0]

    deadline = time.time() + 30
    final_msgs = []
    while time.time() < deadline:
        final_msgs = _get(f"{s2_url}/messages")["messages"]
        test_msgs = [m for m in final_msgs if m in ["Msg1", "Msg2", "Msg3", "Msg4"]]
        if test_msgs == ["Msg1", "Msg2", "Msg3", "Msg4"]:
            break
        time.sleep(1)

    test_msgs = [m for m in final_msgs if m in ["Msg1", "Msg2", "Msg3", "Msg4"]]
    assert test_msgs == ["Msg1", "Msg2", "Msg3", "Msg4"], \
        f"S2 messages mismatch: expected ['Msg1', 'Msg2', 'Msg3', 'Msg4'], got {test_msgs}"


def test_iter3_parallel_clients_w3_blocked_w1_free():
    """
    Explicitly verify that a w=3 request doesn't block a concurrent w=1 request.
    """
    stop_secondary2()
    time.sleep(2)

    w3_result = {"response": None, "error": None}

    def post_w3():
        try:
            w3_result["response"] = _post(f"{MASTER}/messages", {"msg": "Parallel_Msg3", "w": 3})
        except Exception as e:
            w3_result["error"] = e

    t = threading.Thread(target=post_w3)
    t.start()
    time.sleep(1)
    assert t.is_alive(), "w=3 request should be waiting for S2"

    start = time.time()
    r = _post(f"{MASTER}/messages", {"msg": "Parallel_Msg4", "w": 1})
    elapsed = time.time() - start
    assert r.json()["w"] == 1
    assert elapsed < 2.0, f"w=1 request must complete fast even while w=3 is blocked (took {elapsed}s)"

    # w=3 still blocked
    assert t.is_alive(), "w=3 should still be blocked"

    # Bring S2 back and ensure w=3 eventually completes
    start_secondary2()
    t.join(timeout=30)
    assert not t.is_alive(), "w=3 request must finish after S2 rejoin"
    assert w3_result["response"] is not None, "w=3 request should have completed"
    assert w3_result["response"].json()["w"] == 3

