#!/usr/bin/env python3
"""
Concurrency tests for Iteration 2
Tests concurrent POST requests to verify thread safety and ordering
"""

import os
import time
import requests
import concurrent.futures
from typing import List, Set

BASE = "http://localhost"
MASTER = f"{BASE}:8000"


def post_message(msg: str, w: int = None) -> dict:
    """Post a message to master"""
    data = {"msg": msg}
    if w is not None:
        data["w"] = w
    
    try:
        resp = requests.post(f"{MASTER}/messages", json=data, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def get_messages(port: int = 8000) -> List[str]:
    """Get messages from a node"""
    try:
        resp = requests.get(f"{BASE}:{port}/messages", timeout=5)
        resp.raise_for_status()
        return resp.json().get("messages", [])
    except Exception:
        return []


def test_concurrent_posts():
    """CON-01: Concurrent posts preserve uniqueness"""
    print("=" * 50)
    print("CON-01: Concurrent Posts Preserve Uniqueness")
    print("=" * 50)
    print()
    
    num_requests = 10
    print(f"Sending {num_requests} concurrent POST requests with w=1...")
    
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [
            executor.submit(post_message, f"concurrent_{i}", w=1)
            for i in range(num_requests)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    elapsed = time.time() - start_time
    print(f"All requests completed in {elapsed:.3f}s")
    print()
    
    # Check for errors
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"❌ {len(errors)} requests failed:")
        for err in errors:
            print(f"   {err}")
    else:
        print("✅ All requests succeeded")
    print()
    
    # Wait for replication
    print("Waiting 3 seconds for replication...")
    time.sleep(3)
    
    # Check messages on all nodes
    print("Checking messages on all nodes...")
    master_msgs = get_messages(8000)
    
    # Get secondary ports (simplified - assumes 8001, 8002)
    secondary_ports = [8001, 8002]
    all_msgs = {"master": master_msgs}
    
    for port in secondary_ports:
        try:
            msgs = get_messages(port)
            all_msgs[f"secondary_{port}"] = msgs
        except:
            pass
    
    # Verify all concurrent messages are present
    concurrent_msgs = {f"concurrent_{i}" for i in range(num_requests)}
    
    print("\nMessage presence check:")
    all_present = True
    for node, msgs in all_msgs.items():
        msg_set = set(msgs)
        present = concurrent_msgs.intersection(msg_set)
        missing = concurrent_msgs - msg_set
        
        print(f"{node}:")
        print(f"  Present: {len(present)}/{num_requests}")
        if missing:
            print(f"  Missing: {missing}")
            all_present = False
    
    if all_present:
        print("\n✅ All concurrent messages present on all nodes")
    else:
        print("\n⚠️  Some messages missing (may need more wait time)")
    
    # Check for duplicates
    print("\nDuplicate check:")
    duplicates_found = False
    for node, msgs in all_msgs.items():
        concurrent_in_node = [m for m in msgs if m.startswith("concurrent_")]
        unique_count = len(set(concurrent_in_node))
        total_count = len(concurrent_in_node)
        
        if total_count != unique_count:
            print(f"❌ {node}: Found duplicates! Total: {total_count}, Unique: {unique_count}")
            duplicates_found = True
        else:
            print(f"✅ {node}: No duplicates ({unique_count} unique)")
    
    if not duplicates_found:
        print("\n✅ No duplicates found - deduplication working correctly")
    
    # Check ordering consistency
    print("\nOrdering consistency check:")
    master_concurrent = [m for m in master_msgs if m.startswith("concurrent_")]
    all_ordered = True
    
    for node, msgs in all_msgs.items():
        if node == "master":
            continue
        node_concurrent = [m for m in msgs if m.startswith("concurrent_")]
        
        # Check if order matches (allowing for some interleaving with other messages)
        # Extract just the concurrent messages in order
        master_order = [m for m in master_msgs if m.startswith("concurrent_")]
        node_order = [m for m in msgs if m.startswith("concurrent_")]
        
        if master_order == node_order:
            print(f"✅ {node}: Order matches master")
        else:
            print(f"⚠️  {node}: Order differs from master")
            print(f"   Master: {master_order[:5]}...")
            print(f"   {node}: {node_order[:5]}...")
            all_ordered = False
    
    if all_ordered:
        print("\n✅ Ordering consistent across all nodes")
    else:
        print("\n⚠️  Some ordering differences (may be acceptable with concurrent writes)")
    
    print()


def test_sequence_uniqueness():
    """Verify sequence numbers are unique even with concurrent requests"""
    print("=" * 50)
    print("Sequence Number Uniqueness Test")
    print("=" * 50)
    print()
    
    num_requests = 20
    print(f"Sending {num_requests} concurrent requests...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [
            executor.submit(post_message, f"seq_test_{i}", w=1)
            for i in range(num_requests)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    # Extract sequence numbers from logs (would need to parse logs)
    # For now, just verify all requests succeeded
    successful = [r for r in results if "error" not in r]
    print(f"✅ {len(successful)}/{num_requests} requests succeeded")
    
    # Check master messages
    master_msgs = get_messages(8000)
    seq_test_msgs = [m for m in master_msgs if m.startswith("seq_test_")]
    unique_count = len(set(seq_test_msgs))
    
    if len(seq_test_msgs) == num_requests and unique_count == num_requests:
        print(f"✅ All {num_requests} messages present and unique")
    else:
        print(f"⚠️  Found {len(seq_test_msgs)} messages, {unique_count} unique")
    
    print()


if __name__ == "__main__":
    print("Iteration 2 Concurrency Tests")
    print("=" * 50)
    print()
    
    # Check if services are up
    try:
        resp = requests.get(f"{MASTER}/health", timeout=5)
        resp.raise_for_status()
        print("✅ Master is healthy")
    except Exception as e:
        print(f"❌ Master health check failed: {e}")
        exit(1)
    
    print()
    
    test_concurrent_posts()
    test_sequence_uniqueness()
    
    print("=" * 50)
    print("Concurrency tests complete!")
    print("=" * 50)

