import time

import requests


BASE = "http://localhost"
MASTER = f"{BASE}:8000"
S1 = f"{BASE}:8001"
S2 = f"{BASE}:8002"


def _get(url: str):
    return requests.get(url, timeout=5).json()


def _post(url: str, json: dict):
    return requests.post(url, json=json, timeout=10).json()


def test_health():
    for u in [MASTER, S1, S2]:
        j = _get(f"{u}/health")
        assert j["status"] == "ok"


def test_blocking_and_consistency():
    t0 = time.time()
    r = _post(f"{MASTER}/messages", {"msg": "pytest"})
    dur = r["duration_ms"]
    assert len(r["acks"]) == 2
    assert dur >= 1400  # secondary2 has 1500ms delay; allow some jitter
    t1 = time.time()
    assert (t1 - t0) >= 1.2

    m = _get(f"{MASTER}/messages")["messages"]
    s1 = _get(f"{S1}/messages")["messages"]
    s2 = _get(f"{S2}/messages")["messages"]
    assert m == s1 == s2


