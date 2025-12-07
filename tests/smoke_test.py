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
            cmd = "docker compose ps --format json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
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
    t0 = time.time()
    r = _post(f"{MASTER}/messages", {"msg": "pytest"})
    dur = r["duration_ms"]
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


