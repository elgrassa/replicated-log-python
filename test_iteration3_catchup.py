#!/usr/bin/env python3
"""Test catch-up after downtime: all missed messages delivered"""
import requests
import time
import sys

MASTER = "http://localhost:8000"
SECONDARY2 = "http://localhost:8002"

def post_message(msg, w=None):
    """POST message to master"""
    data = {"msg": msg}
    if w is not None:
        data["w"] = w
    resp = requests.post(f"{MASTER}/messages", json=data, timeout=60)
    return resp.json()

def get_messages(url):
    """GET messages from URL"""
    resp = requests.get(f"{url}/messages", timeout=10)
    return resp.json().get("messages", [])

def test_catchup():
    """Test that all missed messages are delivered after downtime"""
    print("Test: Catch-up After Downtime")
    print("=" * 60)
    print()
    
    print("Prerequisites: Stop secondary2")
    print("  Run: docker compose stop secondary2")
    input("Press Enter when ready...")
    print()
    
    # Send multiple messages with various w values
    messages = []
    print("Sending messages M1..M10 with various w values...")
    
    for i in range(1, 11):
        w = 1 if i % 3 == 0 else (2 if i % 3 == 1 else None)
        msg = f"M{i}"
        messages.append(msg)
        
        if w:
            print(f"  POST {msg} with w={w}")
        else:
            print(f"  POST {msg} (default w)")
        
        resp = post_message(msg, w)
        if "error" in resp:
            print(f"    ❌ Error: {resp['error']}")
        else:
            acks = len(resp.get("acks", []))
            print(f"    ✅ w={resp.get('w')}, acks={acks}")
        
        time.sleep(0.1)
    
    print()
    print("Starting secondary2...")
    print("  Run: docker compose start secondary2")
    input("Press Enter after starting S2...")
    
    # Wait for S2 to be healthy
    print("Waiting for S2 to be healthy...")
    for i in range(30):
        try:
            resp = requests.get(f"{SECONDARY2}/health", timeout=2)
            if resp.status_code == 200:
                print("✅ S2 is healthy")
                break
        except:
            pass
        time.sleep(1)
    
    # Wait for catch-up
    print()
    print("Waiting for catch-up replication...")
    time.sleep(5)
    
    # Check messages on S2
    print()
    print("Checking messages on S2...")
    s2_messages = get_messages(SECONDARY2)
    print(f"S2 has {len(s2_messages)} messages")
    
    # Check master messages
    master_messages = get_messages(MASTER)
    print(f"Master has {len(master_messages)} messages")
    
    # Filter to our test messages
    s2_test = [m for m in s2_messages if m.startswith("M") and m[1:].isdigit()]
    master_test = [m for m in master_messages if m.startswith("M") and m[1:].isdigit()]
    
    print()
    print(f"Master test messages: {master_test}")
    print(f"S2 test messages:      {s2_test}")
    
    if len(s2_test) == len(master_test):
        print(f"✅ Catch-up complete: S2 has all {len(s2_test)} messages")
    else:
        print(f"⚠️  S2 has {len(s2_test)} messages, master has {len(master_test)}")
    
    if s2_test == master_test:
        print("✅ Messages in correct order and no duplicates")
    else:
        print("⚠️  Message order or content mismatch")
    
    # Check for duplicates
    if len(s2_test) == len(set(s2_test)):
        print("✅ No duplicates detected")
    else:
        print("❌ Duplicates detected!")
    
    print()
    print("=" * 60)
    print("Test Complete")

if __name__ == "__main__":
    test_catchup()

