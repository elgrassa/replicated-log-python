#!/usr/bin/env python3
"""Test parallel clients: w=3 blocked, w=1 not blocked"""
import requests
import threading
import time
import sys

MASTER = "http://localhost:8000"

def post_with_timing(msg, w=None):
    """POST message and return (response, duration_ms)"""
    data = {"msg": msg}
    if w is not None:
        data["w"] = w
    
    start = time.time()
    try:
        resp = requests.post(f"{MASTER}/messages", json=data, timeout=60)
        duration_ms = int((time.time() - start) * 1000)
        return resp.json(), duration_ms
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return {"error": str(e)}, duration_ms

def test_parallel_clients():
    """Test that w=1 is not blocked by w=3"""
    print("Test: Parallel Clients (w=3 blocked, w=1 not blocked)")
    print("=" * 60)
    print()
    
    # Ensure S2 is stopped
    print("Prerequisites: Ensure secondary2 is stopped")
    print("  Run: docker compose stop secondary2")
    input("Press Enter when ready...")
    print()
    
    # Start w=3 request in background
    result_w3 = {"done": False, "response": None, "duration": 0}
    
    def send_w3():
        resp, dur = post_with_timing("Msg3_parallel", w=3)
        result_w3["response"] = resp
        result_w3["duration"] = dur
        result_w3["done"] = True
    
    thread_w3 = threading.Thread(target=send_w3, daemon=False)
    print("Step 1: Starting Msg3 with w=3 in background thread...")
    thread_w3.start()
    
    # Wait a bit for it to enter blocked state
    time.sleep(0.5)
    
    # Check if thread is still alive (blocked)
    if thread_w3.is_alive():
        print("✅ Msg3 (w=3) is blocking (as expected)")
    else:
        print("⚠️  Msg3 (w=3) already completed (unexpected if S2 is down)")
    
    # Send w=1 request
    print()
    print("Step 2: Sending Msg4 with w=1 (should return quickly)...")
    resp_w1, dur_w1 = post_with_timing("Msg4_parallel", w=1)
    
    print(f"Response: w={resp_w1.get('w')}, acks={len(resp_w1.get('acks', []))}, duration={dur_w1}ms")
    
    if dur_w1 < 1000:
        print(f"✅ w=1 returned quickly ({dur_w1}ms) while w=3 is still blocking")
    else:
        print(f"⚠️  w=1 took {dur_w1}ms (might be blocked?)")
    
    # Check if w=3 is still blocking
    if thread_w3.is_alive():
        print("✅ w=3 is still blocking (proves no global blocking)")
    else:
        print("⚠️  w=3 already completed")
    
    # Start S2 and wait for w=3 to complete
    print()
    print("Step 3: Start S2 and wait for w=3 to complete...")
    print("  Run: docker compose start secondary2")
    input("Press Enter after starting S2...")
    
    # Wait for S2 to be healthy
    print("Waiting for S2 to be healthy...")
    for i in range(30):
        try:
            resp = requests.get("http://localhost:8002/health", timeout=2)
            if resp.status_code == 200:
                print("✅ S2 is healthy")
                break
        except:
            pass
        time.sleep(1)
    
    # Wait for w=3 to complete
    print("Waiting for w=3 request to complete...")
    thread_w3.join(timeout=30)
    
    if result_w3["done"]:
        resp = result_w3["response"]
        dur = result_w3["duration"]
        acks_count = len(resp.get("acks", []))
        print(f"✅ w=3 completed: {acks_count} ACKs, duration={dur}ms")
        if acks_count >= 2:
            print("✅ w=3 got ACKs from both secondaries")
        else:
            print(f"⚠️  w=3 got {acks_count} ACKs (expected 2)")
    else:
        print("❌ w=3 did not complete within timeout")
    
    print()
    print("=" * 60)
    print("Test Complete")

if __name__ == "__main__":
    test_parallel_clients()

