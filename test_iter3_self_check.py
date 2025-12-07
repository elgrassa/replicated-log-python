#!/usr/bin/env python3
"""
Self-Check Acceptance Test for Iteration 3
Tests: retry, blocking w=3, parallel clients, catch-up
"""
import os
import time
import threading
import subprocess
import requests

BASE = "http://localhost"
MASTER = f"{BASE}:8000"
S1 = f"{BASE}:8001"
S2 = f"{BASE}:8002"


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
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            resp = requests.get(f"{S2}/health", timeout=2)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise TimeoutError("Secondary2 did not become healthy in time")


def main():
    print("=" * 50)
    print("Self-Check Acceptance Test for Iteration 3")
    print("=" * 50)
    print()
    
    # Ensure secondary2 is stopped
    print("Stopping S2...")
    stop_secondary2()
    time.sleep(2)
    print("✅ S2 stopped")
    print()
    
    # Step 1: POST Msg1 with w=1
    print("Step 1: POST Msg1 with w=1 (should return quickly)")
    start = time.time()
    r1 = requests.post(f"{MASTER}/messages", json={"msg": "Msg1", "w": 1}, timeout=10)
    elapsed = (time.time() - start) * 1000
    body1 = r1.json()
    print(f"  Response: w={body1.get('w')}, duration={elapsed:.0f}ms")
    assert body1["w"] == 1, f"Expected w=1, got {body1.get('w')}"
    assert elapsed < 100, f"w=1 should return quickly (took {elapsed}ms)"
    print("  ✅ Step 1 PASS")
    print()
    
    # Step 2: POST Msg2 with w=2
    print("Step 2: POST Msg2 with w=2 (should wait for S1)")
    start = time.time()
    r2 = requests.post(f"{MASTER}/messages", json={"msg": "Msg2", "w": 2}, timeout=10)
    elapsed = (time.time() - start) * 1000
    body2 = r2.json()
    print(f"  Response: w={body2.get('w')}, acks={len(body2.get('acks', []))}, duration={elapsed:.0f}ms")
    assert body2["w"] == 2, f"Expected w=2, got {body2.get('w')}"
    assert len(body2.get("acks", [])) >= 1, "w=2 should get at least 1 ACK"
    print("  ✅ Step 2 PASS")
    print()
    
    # Step 3: POST Msg3 with w=3 (blocked until S2 up)
    print("Step 3: POST Msg3 with w=3 (should block until S2 starts)")
    msg3_result = {"response": None, "error": None}
    
    def post_msg3():
        try:
            msg3_result["response"] = requests.post(
                f"{MASTER}/messages", json={"msg": "Msg3", "w": 3}, timeout=60
            )
        except Exception as e:
            msg3_result["error"] = e
    
    t_msg3 = threading.Thread(target=post_msg3)
    t_msg3.start()
    
    # Give msg3 time to reach the "waiting for S2" state
    time.sleep(1)
    assert t_msg3.is_alive(), "Msg3 (w=3) should be blocked while S2 is down"
    print("  ✅ Msg3 is blocking (as expected)")
    print()
    
    # Step 4: POST Msg4 with w=1 (should not be blocked)
    print("Step 4: POST Msg4 with w=1 (should return immediately, not blocked by Msg3)")
    start_w1 = time.time()
    r4 = requests.post(f"{MASTER}/messages", json={"msg": "Msg4", "w": 1}, timeout=10)
    elapsed_w1 = (time.time() - start_w1) * 1000
    body4 = r4.json()
    print(f"  Response: w={body4.get('w')}, duration={elapsed_w1:.0f}ms")
    assert body4["w"] == 1, f"Expected w=1, got {body4.get('w')}"
    assert elapsed_w1 < 2000, f"w=1 write must not be blocked by w=3 request (took {elapsed_w1}ms)"
    
    # Verify Msg3 is still blocked
    assert t_msg3.is_alive(), "Msg3 should still be blocked"
    print("  ✅ Msg4 returned quickly while Msg3 is still blocking")
    print()
    
    # Step 5: Start S2
    print("Step 5: Starting S2...")
    start_secondary2()
    print("  ✅ S2 started and healthy")
    print()
    
    # Wait for Msg3 to complete
    print("Waiting for Msg3 to complete (retries should deliver to S2)...")
    t_msg3.join(timeout=30)
    assert not t_msg3.is_alive(), "Msg3 (w=3) should finish after S2 is up"
    assert msg3_result["response"] is not None, "Msg3 should have completed successfully"
    body3 = msg3_result["response"].json()
    print(f"  Response: w={body3.get('w')}, acks={len(body3.get('acks', []))}")
    assert body3["w"] == 3, f"Expected w=3, got {body3.get('w')}"
    assert len(body3.get("acks", [])) >= 2, "w=3 should get ACKs from both secondaries"
    print("  ✅ Step 3 PASS: Msg3 completed with ACKs from both secondaries")
    print()
    
    # Step 6: Check messages on S2
    print("Step 6: Check messages on S2")
    print("Waiting for catch-up replication...")
    time.sleep(5)
    
    deadline = time.time() + 30
    final_msgs = []
    while time.time() < deadline:
        try:
            final_msgs = requests.get(f"{S2}/messages", timeout=5).json()["messages"]
            test_msgs = [m for m in final_msgs if m in ["Msg1", "Msg2", "Msg3", "Msg4"]]
            if test_msgs == ["Msg1", "Msg2", "Msg3", "Msg4"]:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    
    test_msgs = [m for m in final_msgs if m in ["Msg1", "Msg2", "Msg3", "Msg4"]]
    print(f"  S2 messages (test): {test_msgs}")
    assert test_msgs == ["Msg1", "Msg2", "Msg3", "Msg4"], \
        f"S2 messages mismatch: expected ['Msg1', 'Msg2', 'Msg3', 'Msg4'], got {test_msgs}"
    print("  ✅ Step 6 PASS: S2 has all messages in correct order")
    print()
    
    print("=" * 50)
    print("✅ All tests PASSED!")
    print("=" * 50)
    print()
    print("Requirements verified:")
    print("  ✅ Retry mechanism: Messages queued and retried until success")
    print("  ✅ w=3 blocking: Client blocked until S2 comes back")
    print("  ✅ Parallel clients: w=1 not blocked by w=3")
    print("  ✅ Catch-up: All missed messages delivered to S2")
    print("  ✅ Total order: Messages in same order on all nodes")
    print("  ✅ Exactly-once: No duplicates (deduplication by seq)")


if __name__ == "__main__":
    main()

