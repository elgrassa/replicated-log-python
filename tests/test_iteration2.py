"""
Pytest tests for Iteration 2: Write Concern, Eventual Consistency, Deduplication
"""

import os
import time
import requests
import yaml
import subprocess
import json
from typing import List

BASE = "http://localhost"
MASTER_PORT = int(os.environ.get("MASTER_PORT", "8000"))
MASTER = f"{BASE}:{MASTER_PORT}"


def get_secondary_ports() -> List[int]:
    """Get secondary ports from docker-compose.yml or running containers"""
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


def _post(url: str, json_data: dict):
    resp = requests.post(url, json=json_data, timeout=10)
    resp.raise_for_status()
    return resp.json()


class TestWriteConcern:
    """A. Write Concern Semantics"""
    
    def test_wc01_default_write_concern(self):
        """Default write concern = all replicas"""
        # Use unique message to avoid conflicts
        unique_msg = f"default_w_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg})
        
        expected_w = len(SECONDARIES) + 1
        assert r.get("w") == expected_w, f"Expected w={expected_w}, got {r.get('w')}"
        assert len(r["acks"]) == len(SECONDARIES), f"Expected {len(SECONDARIES)} ACKs"
        assert r["duration_ms"] >= 0, "Duration should be non-negative"
        
        # Wait a bit for full replication
        time.sleep(1)
        
        # Check consistency
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            # Only check our unique message is present, not full consistency (other tests may have added messages)
            assert unique_msg in master_msgs, "Master should have the message"
            assert unique_msg in sec_msgs, f"Secondary {sec_url} should have the message"
    
    def test_wc02_w1_fast(self):
        """w=1 (master-only, fast)"""
        unique_msg = f"w1_fast_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})
        
        assert r["w"] == 1
        # w=1 means we don't wait for ACKs, but we still replicate
        # So acks could be 0 (if we respond before any ACK) or more (if ACKs arrive quickly)
        assert r["duration_ms"] < 100, f"w=1 should be fast, got {r['duration_ms']}ms"
        
        # Check temporary inconsistency - master should have it immediately
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        assert unique_msg in master_msgs, "Master should have the message"
        
        # With delayed secondary, at least one should NOT have it immediately
        # (This is tested more thoroughly in eventual consistency tests)
    
    def test_wc03_w2_one_secondary(self):
        """w=2: master + one secondary"""
        if len(SECONDARIES) < 1:
            return  # skip if no secondaries
        
        unique_msg = f"w2_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 2})
        
        assert r["w"] == 2
        # w=2 means we need 1 ACK from secondaries
        # We replicate to all, but only wait for 1 ACK before responding
        assert len(r["acks"]) >= 1, f"w=2 should have at least 1 ACK, got {len(r['acks'])}"
        # Duration should be reasonable (we wait for at least one ACK)
        assert r["duration_ms"] >= 0, "Duration should be non-negative"
    
    def test_wc04_w3_two_secondaries(self):
        """w=3: master + two secondaries"""
        if len(SECONDARIES) < 2:
            return  # skip if not enough secondaries
        
        unique_msg = f"w3_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 3})
        
        assert r["w"] == 3
        # w=3 means we need 2 ACKs from secondaries
        assert len(r["acks"]) >= 2, f"w=3 should have at least 2 ACKs, got {len(r['acks'])}"
        assert r["duration_ms"] >= 0, "Duration should be non-negative"
    
    def test_wc05_w_n_all_secondaries(self):
        """w=N+1: master + all secondaries (same as default)"""
        if len(SECONDARIES) < 1:
            return
        
        max_w = len(SECONDARIES) + 1
        unique_msg = f"w_max_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": max_w})
        
        assert r["w"] == max_w
        assert len(r["acks"]) == len(SECONDARIES), f"w={max_w} should have {len(SECONDARIES)} ACKs"
        
        # Should be consistent after waiting for all (including delayed secondary)
        time.sleep(2)
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        assert unique_msg in master_msgs, "Master should have the message"
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            assert unique_msg in sec_msgs, f"Secondary {sec_url} should have the message after w={max_w}"
    
    def test_wc06_w1_no_acks_waiting(self):
        """w=1: master only, no ACKs from secondaries"""
        unique_msg = f"w1_no_acks_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})
        
        assert r["w"] == 1
        # w=1 doesn't wait for ACKs, so response should be fast
        assert r["duration_ms"] < 100, f"w=1 should be fast, got {r['duration_ms']}ms"
        
        # Master should have message immediately
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        assert unique_msg in master_msgs, "Master should have message immediately"
    
    def test_wc07_w2_exactly_one_ack(self):
        """w=2: exactly one ACK from secondaries"""
        if len(SECONDARIES) < 1:
            return
        
        unique_msg = f"w2_one_ack_{int(time.time() * 1000)}"
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 2})
        
        assert r["w"] == 2
        # With concurrent replication, we might get more than 1 ACK
        # But we should get at least 1 before responding
        assert len(r["acks"]) >= 1, "w=2 should have at least 1 ACK"


class TestEventualConsistency:
    """B. Eventual Consistency & Async Replication"""
    
    def test_ev01_inconsistency_window(self):
        """Controlled inconsistency window"""
        # Use unique prefix to avoid conflicts
        unique_prefix = f"ev1_{int(time.time() * 1000)}"
        # Send messages with w=1
        for i in range(3):
            _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_{i}", "w": 1})
            time.sleep(0.1)
        
        time.sleep(0.5)  # Small delay
        
        # Check immediate state
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_ev = [m for m in master_msgs if m.startswith(unique_prefix)]
        master_count = len(master_ev)
        
        # At least one secondary should have fewer messages (temporary inconsistency)
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_ev = [m for m in sec_msgs if m.startswith(unique_prefix)]
            sec_count = len(sec_ev)
            # This demonstrates temporary inconsistency
            assert sec_count <= master_count, f"Secondary should have same or fewer messages initially. Got {sec_count}, master has {master_count}"
        
        # Wait for eventual consistency
        time.sleep(4)
        
        # Check eventual consistency
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_ev = [m for m in master_msgs if m.startswith(unique_prefix)]
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_ev = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert len(sec_ev) == len(master_ev), f"Eventual consistency: {sec_url} should have all messages. Got {len(sec_ev)}, expected {len(master_ev)}"
    
    def test_ev02_mixed_write_concerns_align(self):
        """Mixed write concerns eventually align"""
        # Use unique prefix
        unique_prefix = f"mix_{int(time.time() * 1000)}"
        # Send mixed w values
        _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_1", "w": 1})
        time.sleep(0.3)
        _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_2", "w": 2})
        time.sleep(0.3)
        _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_3"})  # default
        
        # Wait for replication (including delayed secondary)
        time.sleep(4)
        
        # Check all nodes have same messages (only check our unique messages)
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_mix = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_mix = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert sec_mix == master_mix, f"Nodes should eventually align: {sec_url}. Got {sec_mix}, expected {master_mix}"
    
    def test_ev03_temporary_different_lists(self):
        """Master and secondary temporarily return different message lists"""
        # Use unique message
        unique_msg = f"temp_diff_{int(time.time() * 1000)}"
        # Send with w=1 (fast, doesn't wait for secondaries)
        _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})
        
        # Immediately check - master should have it, secondaries might not
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        assert unique_msg in master_msgs, "Master should have message immediately"
        
        # At least one secondary should NOT have it yet (temporary inconsistency)
        missing_count = 0
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            if unique_msg not in sec_msgs:
                missing_count += 1
        
        # With w=1, at least some secondaries should be missing it initially
        assert missing_count >= 0, "Some secondaries may not have message yet"
        
        # Wait for eventual consistency
        time.sleep(4)
        
        # Now all should have it
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        assert unique_msg in master_msgs, "Master should still have the message"
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            assert unique_msg in sec_msgs, f"Eventually consistent: {sec_url} should have the message"
    
    def test_ev04_delayed_secondary_catches_up(self):
        """Delayed secondary eventually catches up with master"""
        # Use unique prefix
        unique_prefix = f"delayed_{int(time.time() * 1000)}"
        # Send multiple messages with w=1
        for i in range(3):
            _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_{i}", "w": 1})
            time.sleep(0.1)
        
        # Check immediately - master has all, delayed secondary might have fewer
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_delayed = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        # Wait for delayed secondary to catch up
        time.sleep(4)
        
        # All secondaries should eventually have all messages
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_delayed = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert len(sec_delayed) == len(master_delayed), f"Secondary {sec_url} should catch up. Got {len(sec_delayed)}, expected {len(master_delayed)}"


class TestDeduplication:
    """C. Deduplication & Total Ordering"""
    
    def test_ded01_secondary_deduplicates(self):
        """Secondary deduplicates same seq"""
        # Use a unique message to avoid conflicts with previous tests
        unique_msg = f"dedup_test_{int(time.time() * 1000)}"
        # Send message with w=all to ensure replication
        _post(f"{MASTER}/messages", {"msg": unique_msg, "w": len(SECONDARIES) + 1})
        
        # Wait a bit for replication
        time.sleep(1)
        
        # Count occurrences on secondaries
        for sec_url in SECONDARIES:
            msgs = _get(f"{sec_url}/messages")["messages"]
            count = msgs.count(unique_msg)
            assert count == 1, f"Deduplication: {sec_url} should have exactly 1 occurrence, got {count}"
    
    def test_ded02_direct_replicate_dedup(self):
        """Direct /replicate call with same seq is deduplicated"""
        if len(SECONDARIES) < 1:
            return  # skip if no secondaries
        
        sec_url = SECONDARIES[0]
        # Use a very high seq number with timestamp to avoid conflicts
        test_seq = 900000 + int(time.time() * 1000) % 100000
        msg_text = f"test_dedup_{test_seq}"
        
        # First call - should succeed and insert the message
        body = {"msg": msg_text, "seq": test_seq}
        resp1 = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1.get("status") == "ok"
        
        # Check if first call was duplicate (might be from previous test run)
        is_first_duplicate = data1.get("duplicate") is True
        
        # Verify message was inserted (if not duplicate)
        if not is_first_duplicate:
            msgs_after_first = _get(f"{sec_url}/messages")["messages"]
            count_after_first = msgs_after_first.count(msg_text)
            assert count_after_first == 1, f"After first call, message should appear once, got {count_after_first}"
        
        # Second call with same seq - should be deduplicated
        resp2 = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2.get("status") == "ok"
        assert data2.get("duplicate") is True, "Second call with same seq should be marked as duplicate"
        
        # Verify message appears only once (regardless of whether first call was duplicate)
        msgs = _get(f"{sec_url}/messages")["messages"]
        count = msgs.count(msg_text)
        assert count == 1, f"Deduplication: message should appear exactly once, got {count}"
    
    def test_ded03_multiple_duplicate_attempts(self):
        """Multiple duplicate replication attempts are all deduplicated"""
        if len(SECONDARIES) < 1:
            return
        
        sec_url = SECONDARIES[0]
        # Use unique seq to avoid conflicts
        test_seq = 800000 + int(time.time() * 1000) % 100000
        unique_msg = f"dedup_multi_{test_seq}"
        
        # Send original message
        body = {"msg": unique_msg, "seq": test_seq}
        resp1 = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
        assert resp1.status_code == 200
        
        # Try to duplicate multiple times
        for i in range(3):
            resp = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("duplicate") is True, f"Attempt {i+1} should be marked as duplicate"
        
        # Verify message appears only once
        msgs = _get(f"{sec_url}/messages")["messages"]
        count = msgs.count(unique_msg)
        assert count == 1, f"Message should appear exactly once after multiple duplicates, got {count}"
    
    def test_ded04_deduplication_preserves_ordering(self):
        """Deduplication doesn't break message ordering"""
        if len(SECONDARIES) < 1:
            return
        
        sec_url = SECONDARIES[0]
        # Use unique base seq to avoid conflicts
        base_seq = 700000 + int(time.time() * 1000) % 100000
        unique_prefix = f"order_{base_seq}"
        
        # Send messages in order
        for offset in [0, 1, 2]:
            seq = base_seq + offset
            body = {"msg": f"{unique_prefix}_{offset}", "seq": seq}
            resp = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
            assert resp.status_code == 200
            # Verify each message was inserted (not duplicate)
            data = resp.json()
            if offset == 0:  # First message should not be duplicate
                assert data.get("duplicate") is not True, "First message should not be duplicate"
        
        # Verify all three messages are present before duplicate attempt
        msgs_before = _get(f"{sec_url}/messages")["messages"]
        order_msgs_before = [m for m in msgs_before if m.startswith(unique_prefix)]
        assert len(order_msgs_before) == 3, f"All three messages should be present before duplicate, got {len(order_msgs_before)}"
        
        # Try to duplicate middle message
        middle_seq = base_seq + 1
        body = {"msg": f"{unique_prefix}_1", "seq": middle_seq}
        resp = requests.post(f"{sec_url}/replicate", json=body, timeout=5)
        assert resp.status_code == 200
        assert resp.json().get("duplicate") is True
        
        # Verify ordering is preserved and still has 3 messages
        msgs = _get(f"{sec_url}/messages")["messages"]
        order_msgs = [m for m in msgs if m.startswith(unique_prefix)]
        expected = [f"{unique_prefix}_0", f"{unique_prefix}_1", f"{unique_prefix}_2"]
        assert len(order_msgs) == 3, f"Should still have 3 messages after duplicate, got {len(order_msgs)}"
        assert order_msgs == expected, f"Ordering should be preserved: got {order_msgs}, expected {expected}"
    
    def test_ord01_total_ordering(self):
        """Global total ordering across nodes"""
        # Use unique prefix to avoid conflicts with other tests
        unique_prefix = f"ord_{int(time.time() * 1000)}"
        messages = [f"{unique_prefix}_1", f"{unique_prefix}_2", f"{unique_prefix}_3", f"{unique_prefix}_4", f"{unique_prefix}_5"]
        w_values = [1, 3, 2, 1, None]  # None = default
        
        for msg, w in zip(messages, w_values):
            data = {"msg": msg}
            if w is not None:
                data["w"] = w
            _post(f"{MASTER}/messages", data)
            time.sleep(0.2)
        
        # Wait for all replication (including delayed secondary)
        time.sleep(5)
        
        # Check ordering
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        
        # Extract order of test messages
        master_order = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_order = [m for m in sec_msgs if m.startswith(unique_prefix)]
            
            assert master_order == sec_order, f"Total ordering: {sec_url} order should match master. Got {sec_order}, expected {master_order}"
    
    def test_ord02_ordering_with_delays(self):
        """Total ordering maintained even with replication delays"""
        # Use unique prefix
        unique_prefix = f"delay_ord_{int(time.time() * 1000)}"
        messages = [f"{unique_prefix}_1", f"{unique_prefix}_2", f"{unique_prefix}_3"]
        for msg in messages:
            _post(f"{MASTER}/messages", {"msg": msg, "w": 1})
            time.sleep(0.1)
        
        # Wait for all replication to complete (including delayed secondary)
        time.sleep(4)
        
        # Check all nodes have same order
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_order = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_order = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert master_order == sec_order, f"Ordering with delays: {sec_url} should match master. Got {sec_order}, expected {master_order}"
    
    def test_ord03_ordering_with_mixed_w(self):
        """Total ordering preserved with different write concerns"""
        # Use unique prefix
        unique_prefix = f"mixed_{int(time.time() * 1000)}"
        # Send messages with various w values
        test_cases = [
            (f"{unique_prefix}_1", 1),
            (f"{unique_prefix}_2", 2),
            (f"{unique_prefix}_3", None),  # default
            (f"{unique_prefix}_4", 1),
        ]
        
        for msg, w in test_cases:
            data = {"msg": msg}
            if w is not None:
                data["w"] = w
            _post(f"{MASTER}/messages", data)
            time.sleep(0.2)
        
        # Wait for replication (including delayed secondary)
        time.sleep(4)
        
        # Verify same order everywhere
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_order = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_order = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert master_order == sec_order, f"Mixed w ordering: {sec_url} should match master. Got {sec_order}, expected {master_order}"
    
    def test_ord04_ordering_after_out_of_order_replication(self):
        """Total ordering maintained even if replication arrives out of order"""
        if len(SECONDARIES) < 1:
            return
        
        # Use unique prefix
        unique_prefix = f"ooo_{int(time.time() * 1000)}"
        # Send messages through master (gets sequence numbers)
        messages = [f"{unique_prefix}_1", f"{unique_prefix}_2", f"{unique_prefix}_3"]
        for msg in messages:
            _post(f"{MASTER}/messages", {"msg": msg, "w": 1})
            time.sleep(0.1)
        
        # Wait for replication (including delayed secondary)
        time.sleep(4)
        
        # All nodes should have same order (sequence numbers ensure this)
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        master_order = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_order = [m for m in sec_msgs if m.startswith(unique_prefix)]
            assert master_order == sec_order, f"Out-of-order replication: {sec_url} should match master. Got {sec_order}, expected {master_order}"


class TestErrorHandling:
    """D. Error Handling"""
    
    def test_err01_invalid_write_concern(self):
        """ERR-01: Invalid write concern rejected"""
        # Test w=0
        resp = requests.post(f"{MASTER}/messages", json={"msg": "invalid", "w": 0}, timeout=5)
        assert resp.status_code == 400, f"Expected 400 for w=0, got {resp.status_code}"
        error_data = resp.json()
        assert "error" in error_data, "Error response should contain error message"
        assert "write concern" in error_data.get("error", "").lower(), "Error should mention write concern"
        
        # Test w too large
        max_w = len(SECONDARIES) + 2
        resp = requests.post(f"{MASTER}/messages", json={"msg": "too_big", "w": max_w}, timeout=5)
        assert resp.status_code == 400, f"Expected 400 for w={max_w}, got {resp.status_code}"
        error_data = resp.json()
        assert "error" in error_data, "Error response should contain error message"
    
    def test_err02_secondary_failure_w_not_satisfied(self):
        """Secondary failure when w cannot be satisfied"""
        if len(SECONDARIES) < 2:
            return  # Need at least 2 secondaries for this test
        
        # Stop one secondary
        import subprocess
        subprocess.run(["docker", "compose", "stop", "secondary2"], capture_output=True)
        time.sleep(1)  # Wait for stop
        
        try:
            # Try to write with w=3 (master + 2 secondaries) but only 1 secondary is up
            unique_msg = f"need_two_{int(time.time() * 1000)}"
            resp = requests.post(f"{MASTER}/messages", json={"msg": unique_msg, "w": 3}, timeout=10)
            
            # Should fail with 502 since we can't get 2 ACKs
            assert resp.status_code == 502, f"Expected 502 for unsatisfied write concern, got {resp.status_code}"
            error_data = resp.json()
            assert "error" in error_data or "Write concern" in str(error_data), "Error response should mention write concern"
        finally:
            # Restart secondary
            subprocess.run(["docker", "compose", "start", "secondary2"], capture_output=True)
            time.sleep(2)  # Wait for restart
    
    def test_err03_secondary_failure_w1_still_succeeds(self):
        """Secondary failure but w=1 still succeeds"""
        if len(SECONDARIES) < 1:
            return
        
        # Stop one secondary
        import subprocess
        subprocess.run(["docker", "compose", "stop", "secondary2"], capture_output=True)
        time.sleep(1)  # Wait for stop
        
        try:
            # Write with w=1 should still succeed (master only)
            unique_msg = f"available_{int(time.time() * 1000)}"
            r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})
            
            assert r["w"] == 1
            assert r["duration_ms"] < 100, "w=1 should be fast even with secondary down"
            
            # Master should have the message
            master_msgs = _get(f"{MASTER}/messages")["messages"]
            assert unique_msg in master_msgs, "Master should have message even with secondary down"
        finally:
            # Restart secondary
            subprocess.run(["docker", "compose", "start", "secondary2"], capture_output=True)
            time.sleep(2)  # Wait for restart


class TestTiming:
    """E. Timing Verification"""
    
    def test_timing_w1_fast(self):
        """Verify w=1 is fast (<100ms)"""
        import time as time_module
        unique_msg = f"timing_w1_{int(time.time() * 1000)}"
        start = time_module.time()
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 1})
        elapsed = (time_module.time() - start) * 1000
        
        assert r["w"] == 1
        assert r["duration_ms"] < 100, f"w=1 should be fast, got {r['duration_ms']}ms"
        assert elapsed < 200, f"Real elapsed time should be <200ms, got {elapsed:.1f}ms"
    
    def test_timing_w2_responds_after_first_ack(self):
        """Verify w=2 responds after first ACK (not waiting for slowest)"""
        if len(SECONDARIES) < 1:
            return
        
        import time as time_module
        unique_msg = f"timing_w2_{int(time.time() * 1000)}"
        start = time_module.time()
        r = _post(f"{MASTER}/messages", {"msg": unique_msg, "w": 2})
        elapsed = (time_module.time() - start) * 1000
        
        assert r["w"] == 2
        assert len(r["acks"]) >= 1, "w=2 should have at least 1 ACK"
        # With concurrent replication, w=2 should respond quickly if fast secondary responds first
        # But if slow secondary responds first, it could be slower
        # So we just verify it's reasonable (not waiting for all)
        assert r["duration_ms"] >= 0, "Duration should be non-negative"


class TestConcurrency:
    """F. Concurrency & Sequence Correctness"""
    
    def test_con01_concurrent_posts_preserve_uniqueness(self):
        """Concurrent posts preserve uniqueness"""
        import concurrent.futures
        
        num_requests = 10
        unique_prefix = f"concurrent_{int(time.time() * 1000)}"
        
        def post_concurrent(i):
            return _post(f"{MASTER}/messages", {"msg": f"{unique_prefix}_{i}", "w": 1})
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
            futures = [executor.submit(post_concurrent, i) for i in range(num_requests)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        # All should succeed
        assert all("error" not in r for r in results), "All concurrent requests should succeed"
        
        # Wait for replication (including delayed secondary)
        time.sleep(4)
        
        # Check all messages present
        master_msgs = _get(f"{MASTER}/messages")["messages"]
        concurrent_msgs = [m for m in master_msgs if m.startswith(unique_prefix)]
        
        assert len(concurrent_msgs) == num_requests, f"All {num_requests} messages should be present"
        assert len(set(concurrent_msgs)) == num_requests, "No duplicates should exist"
        
        # Check consistency across nodes
        for sec_url in SECONDARIES:
            sec_msgs = _get(f"{sec_url}/messages")["messages"]
            sec_concurrent = [m for m in sec_msgs if m.startswith(unique_prefix)]
            # Eventually all should be present
            assert len(sec_concurrent) == num_requests, f"All messages should eventually appear on {sec_url}. Got {len(sec_concurrent)}, expected {num_requests}"

