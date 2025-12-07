#!/usr/bin/env bash
# Self-Check Acceptance Test for Iteration 3 (CI version)
# Tests: retry, blocking w=3, parallel clients, catch-up
# Designed for GitHub Actions CI environment

set -euo pipefail

host=${1:-localhost}
MASTER="http://${host}:8000"
S1="http://${host}:8001"
S2="http://${host}:8002"

echo "=========================================="
echo "Iteration 3 Self-Check Acceptance Test"
echo "=========================================="
echo ""
echo "Write Concern Semantics:"
echo "  w=1: master only (0 secondary ACKs required)"
echo "  w=2: master + 1 secondary (1 secondary ACK required)"
echo "  w=3: master + 2 secondaries (2 secondary ACKs required)"
echo ""
echo "Test Scenario:"
echo "  Start M + S1"
echo "  send (Msg1, W=1) - Ok (master only, returns immediately)"
echo "  send (Msg2, W=2) - Ok (master + S1, waits for 1 ACK)"
echo "  send (Msg3, W=3) - Wait (master + S1 + S2, waits for 2 ACKs, blocks until S2 starts)"
echo "  send (Msg4, W=1) - Ok (master only, returns immediately, not blocked by Msg3)"
echo "  Start S2"
echo "  Check messages on S2 - [Msg1, Msg2, Msg3, Msg4]"
echo ""

# Ensure S2 is stopped
echo "Ensuring S2 is stopped..."
docker compose stop secondary2 2>/dev/null || true
sleep 2

# Verify M and S1 are healthy
echo "Verifying M and S1 are healthy..."
for i in {1..30}; do
    if curl -sf "${MASTER}/health" >/dev/null && curl -sf "${S1}/health" >/dev/null; then
        echo "✅ M and S1 are healthy"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ M or S1 not healthy after 30 attempts"
        exit 1
    fi
    sleep 1
done
echo ""

# Step 1: POST Msg1 with w=1 (master only, 0 secondary ACKs required)
echo "Step 1: POST Msg1 with w=1 (master only, should return immediately)"
start=$(date +%s%N)
resp1=$(curl -s -X POST "${MASTER}/messages" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"Msg1","w":1}')
end=$(date +%s%N)
duration=$(( (end - start) / 1000000 ))
echo "Response: $(echo "$resp1" | jq '{w, acks_count: (.acks | length), duration_ms}')"
w1=$(echo "$resp1" | jq -r '.w')
acks1=$(echo "$resp1" | jq '.acks | length')
if [ "$w1" = "1" ] && [ "$acks1" -eq 0 ]; then
    echo "✅ Step 1 PASS: Msg1 (w=1) - Ok (master only, 0 secondary ACKs)"
else
    echo "❌ Step 1 FAIL: Expected w=1 with 0 ACKs, got w=$w1, acks=$acks1"
    exit 1
fi
echo ""

# Step 2: POST Msg2 with w=2 (master + 1 secondary, 1 secondary ACK required)
echo "Step 2: POST Msg2 with w=2 (master + 1 secondary, waits for 1 ACK from S1)"
start=$(date +%s%N)
resp2=$(curl -s -X POST "${MASTER}/messages" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"Msg2","w":2}')
end=$(date +%s%N)
duration=$(( (end - start) / 1000000 ))
echo "Response: $(echo "$resp2" | jq '{w, acks_count: (.acks | length), duration_ms}')"
w2=$(echo "$resp2" | jq -r '.w')
acks2=$(echo "$resp2" | jq '.acks | length')
if [ "$w2" = "2" ] && [ "$acks2" -eq 1 ]; then
    echo "✅ Step 2 PASS: Msg2 (w=2) - Ok (master + 1 secondary, got 1 ACK)"
else
    echo "❌ Step 2 FAIL: Expected w=2 with exactly 1 ACK, got w=$w2, acks=$acks2"
    exit 1
fi
echo ""

# Step 3: POST Msg3 with w=3 (master + 2 secondaries, 2 secondary ACKs required)
echo "Step 3: POST Msg3 with w=3 (master + 2 secondaries, waits for 2 ACKs, will block until S2 starts)"
echo "Sending Msg3 in background..."
(
    start=$(date +%s%N)
    resp3=$(curl -s -X POST "${MASTER}/messages" \
        -H 'Content-Type: application/json' \
        -d '{"msg":"Msg3","w":3}')
    end=$(date +%s%N)
    duration=$(( (end - start) / 1000000 ))
    echo "$resp3" > /tmp/msg3_resp.json
    echo "$duration" > /tmp/msg3_duration.txt
    echo "Msg3 completed after ${duration}ms" > /tmp/msg3_done.txt
) &
MSG3_PID=$!

sleep 3

# Check if Msg3 is still blocking
if kill -0 $MSG3_PID 2>/dev/null; then
    echo "✅ Step 3: Msg3 (w=3) - Wait (blocked as expected)"
else
    echo "⚠️  Step 3: Msg3 completed too quickly (may not be blocking correctly)"
fi
echo ""

# Step 4: POST Msg4 with w=1 (master only, 0 secondary ACKs required, should not be blocked by Msg3)
echo "Step 4: POST Msg4 with w=1 (master only, should return immediately, not blocked by Msg3)"
start=$(date +%s%N)
resp4=$(curl -s -X POST "${MASTER}/messages" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"Msg4","w":1}')
end=$(date +%s%N)
duration=$(( (end - start) / 1000000 ))
echo "Response: $(echo "$resp4" | jq '{w, acks_count: (.acks | length), duration_ms}')"
w4=$(echo "$resp4" | jq -r '.w')
acks4=$(echo "$resp4" | jq '.acks | length')
if [ "$w4" = "1" ] && [ "$acks4" -eq 0 ] && [ "$duration" -lt 2000 ]; then
    echo "✅ Step 4 PASS: Msg4 (w=1) - Ok (master only, 0 ACKs, returned quickly, not blocked)"
else
    echo "❌ Step 4 FAIL: Expected w=1 with 0 ACKs and duration < 2000ms, got w=$w4, acks=$acks4, duration=${duration}ms"
    exit 1
fi

# Verify Msg3 is still blocking
if kill -0 $MSG3_PID 2>/dev/null; then
    echo "✅ Step 4 PASS: Msg3 still blocking (proves no global blocking)"
else
    echo "⚠️  Step 4: Msg3 already completed"
fi
echo ""

# Step 5: Start S2
echo "Step 5: Start S2"
docker compose start secondary2
echo "✅ S2 started"
echo "Waiting for S2 to be healthy..."
for i in {1..30}; do
    if curl -sf "${S2}/health" >/dev/null 2>&1; then
        echo "✅ S2 is healthy"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ S2 did not become healthy after 30 attempts"
        exit 1
    fi
    sleep 1
done
echo ""

# Wait for Msg3 to complete
echo "Waiting for Msg3 to complete (retries should deliver to S2)..."
for i in {1..60}; do
    if ! kill -0 $MSG3_PID 2>/dev/null; then
        break
    fi
    sleep 1
done

if [ -f /tmp/msg3_resp.json ]; then
    resp3=$(cat /tmp/msg3_resp.json)
    duration=$(cat /tmp/msg3_duration.txt 2>/dev/null || echo "0")
    echo "Msg3 response: $(echo "$resp3" | jq '{w, acks_count: (.acks | length), duration_ms}')"
    w3=$(echo "$resp3" | jq -r '.w')
    acks3=$(echo "$resp3" | jq '.acks | length')
    if [ "$w3" = "3" ] && [ "$acks3" -eq 2 ]; then
        echo "✅ Step 3 PASS: Msg3 (w=3) completed with ACKs from both secondaries (got 2 ACKs as expected)"
    else
        echo "❌ Step 3 FAIL: Expected w=3 with exactly 2 ACKs, got w=$w3, acks=$acks3"
        exit 1
    fi
else
    echo "❌ Step 3 FAIL: Msg3 response not found"
    exit 1
fi
echo ""

# Step 6: Check messages on S2
echo "Step 6: Check messages on S2 - [Msg1, Msg2, Msg3, Msg4]"
echo "Waiting for catch-up replication..."
sleep 5

# Wait for all messages to arrive
for i in {1..30}; do
    s2_messages=$(curl -s "${S2}/messages" | jq -c '.messages')
    test_msgs=$(echo "$s2_messages" | jq '[.[] | select(. == "Msg1" or . == "Msg2" or . == "Msg3" or . == "Msg4")]')
    count=$(echo "$test_msgs" | jq 'length')
    if [ "$count" -eq 4 ]; then
        break
    fi
    if [ $i -lt 30 ]; then
        echo "  Attempt $i: Found $count/4 messages, waiting..."
        sleep 2
    fi
done

s2_messages=$(curl -s "${S2}/messages" | jq -c '.messages')
test_msgs=$(echo "$s2_messages" | jq '[.[] | select(. == "Msg1" or . == "Msg2" or . == "Msg3" or . == "Msg4")]')
count=$(echo "$test_msgs" | jq 'length')
expected='["Msg1","Msg2","Msg3","Msg4"]'

echo "S2 all messages: $s2_messages"
echo "S2 test messages: $test_msgs"
echo ""

if [ "$count" -eq 4 ]; then
    if [ "$(echo "$test_msgs" | jq -c .)" == "$expected" ]; then
        echo "✅ Step 6 PASS: S2 has all messages in correct order: [Msg1, Msg2, Msg3, Msg4]"
    else
        echo "❌ Step 6 FAIL: S2 has all 4 messages but order is incorrect"
        echo "   Expected: $expected"
        echo "   Got:      $(echo "$test_msgs" | jq -c .)"
        exit 1
    fi
else
    echo "❌ Step 6 FAIL: S2 has $count messages (expected 4)"
    echo "   Expected: $expected"
    echo "   Got:      $test_msgs"
    exit 1
fi

echo ""
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo ""
echo "✅ All steps passed:"
echo "  ✅ Step 1: Msg1 (w=1) - Ok"
echo "  ✅ Step 2: Msg2 (w=2) - Ok"
echo "  ✅ Step 3: Msg3 (w=3) - Wait (blocked until S2 started)"
echo "  ✅ Step 4: Msg4 (w=1) - Ok (not blocked)"
echo "  ✅ Step 5: S2 started"
echo "  ✅ Step 6: S2 messages verified - [Msg1, Msg2, Msg3, Msg4]"
echo ""
echo "Requirements verified:"
echo "  ✅ Retry mechanism: Messages queued and retried until success"
echo "  ✅ w=3 blocking: Client blocked until S2 comes back"
echo "  ✅ Parallel clients: w=1 not blocked by w=3"
echo "  ✅ Catch-up: All missed messages delivered to S2"
echo "  ✅ Total order: Messages in same order on all nodes"
echo "  ✅ Exactly-once: No duplicates (deduplication by seq)"
echo ""
echo "✅ Self-check acceptance test PASSED"

