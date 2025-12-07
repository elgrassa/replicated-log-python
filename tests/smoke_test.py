import os
import time
import requests
import yaml
import subprocess
import json

BASE = "http://localhost"
MASTER_PORT = int(os.environ.get("MASTER_PORT", "8000"))
MASTER = f"{BASE}:{MASTER_PORT}"


def get_secondary_ports():
    ports = []
    try:
        with open("docker-compose.yml", "r") as f:
            compose_config = yaml.safe_load(f)

        for service_name, service_config in compose_config.get("services", {}).items():
            if service_name.startswith("secondary"):
                for port_mapping in service_config.get("ports", []):
                    host_port = int(port_mapping.split(":")[0])
                    if host_port != MASTER_PORT and host_port not in ports:
                        ports.append(host_port)
        ports.sort()
    except (FileNotFoundError, yaml.YAMLError):
        pass

    if not ports:
        try:
            cmd = ["docker", "compose", "ps", "--format", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603 - safe command
            containers_info = json.loads(result.stdout)
            for container in containers_info:
                if container.get("Service", "").startswith("secondary"):
                    for publisher in container.get("Publishers", []):
                        host_port = publisher.get("PublishedPort")
                        if host_port and host_port != MASTER_PORT and host_port not in ports:
                            ports.append(host_port)
            ports.sort()
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass

    if not ports:
        return [8001, 8002]

    return ports


SECONDARY_PORTS = get_secondary_ports()
SECONDARIES = [f"{BASE}:{p}" for p in SECONDARY_PORTS]


def _get(url: str):
    return requests.get(url, timeout=5).json()


def _post(url: str, json: dict):
    return requests.post(url, json=json, timeout=10).json()


def test_health():
    j = _get(f"{MASTER}/health")
    assert j["status"] == "ok"

    for u in SECONDARIES:
        j = _get(f"{u}/health")
        assert j["status"] == "ok"


def test_blocking_and_consistency():
    # Test backward compatibility: no w parameter = all secondaries
    t0 = time.time()
    r = _post(f"{MASTER}/messages", {"msg": "pytest"})
    dur = r["duration_ms"]
    expected_w = len(SECONDARIES) + 1
    assert r.get("w") == expected_w, f"Write concern should default to {expected_w}, got {r.get('w')}"
    assert len(r["acks"]) == len(SECONDARIES), f"Expected {len(SECONDARIES)} ACKs, got {len(r['acks'])}"

    max_delay = 0
    for s_url in SECONDARIES:
        try:
            health_info = _get(f"{s_url}/health")
            max_delay = max(max_delay, health_info.get("delay_ms", 0))
        except Exception:
            pass

    expected_min_dur = max_delay - 100
    if expected_min_dur < 0:
        expected_min_dur = 0
    assert dur >= expected_min_dur, f"Expected duration >= {expected_min_dur}ms, got {dur}ms"

    t1 = time.time()
    assert (t1 - t0) >= (max_delay / 1000.0 - 0.2), f"Real time too short: {t1 - t0}s"

    m = _get(f"{MASTER}/messages")["messages"]

    for u in SECONDARIES:
        s = _get(f"{u}/messages")["messages"]
        assert m == s, f"Consistency mismatch with {u}"


def test_write_concern_w1():
    # w=1: master only, should be fast
    r = _post(f"{MASTER}/messages", {"msg": "w1_test", "w": 1})
    assert r["w"] == 1
    assert len(r["acks"]) == 0, "w=1 should have 0 ACKs from secondaries"
    assert r["duration_ms"] < 100, "w=1 should be fast (<100ms)"


def test_write_concern_w2():
    # w=2: master + 1 secondary
    if len(SECONDARIES) < 1:
        return  # skip if no secondaries

    r = _post(f"{MASTER}/messages", {"msg": "w2_test", "w": 2})
    assert r["w"] == 2
    assert len(r["acks"]) == 1, "w=2 should have 1 ACK from secondaries"


def test_eventual_consistency():
    # Test that w=1 can cause temporary inconsistency, but converges eventually
    if len(SECONDARIES) < 1:
        return

    unique_msg = f"eventual_test_{int(time.time() * 1000)}"
    _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})

    # Master should have the message immediately
    m = _get(f"{MASTER}/messages")["messages"]
    assert unique_msg in m, "Master should have the message"

    # Find secondaries configured with artificial delay
    delayed_secondaries = []
    for u in SECONDARIES:
        try:
            health_info = _get(f"{u}/health")
            if health_info.get("delay_ms", 0) > 0:
                delayed_secondaries.append(u)
        except Exception:
            pass

    # Immediately check secondaries
    missing_count = 0
    for u in SECONDARIES:
        s = _get(f"{u}/messages")["messages"]
        if unique_msg not in s:
            missing_count += 1

    # If at least one secondary is delayed, we expect at least one miss initially
    if delayed_secondaries:
        assert (
            missing_count >= 1
        ), f"Expected at least one delayed secondary to miss the message initially, got {missing_count}"
    # Otherwise (no artificial delay), we don't assert anything about the initial state

    # Wait for async replication to complete
    time.sleep(2)

    # After waiting, all secondaries should have the message
    for u in SECONDARIES:
        s = _get(f"{u}/messages")["messages"]
        assert unique_msg in s, f"Secondary {u} should eventually have the message after async replication"
