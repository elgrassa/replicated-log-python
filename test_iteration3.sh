#!/usr/bin/env bash
# Iteration 3 Test Suite
# Tests retry mechanism, write concern blocking, catch-up, deduplication, total order

set -euo pipefail

host=${1:-localhost}
MASTER="http://${host}:8000"

echo "=========================================="
echo "Iteration 3 Test Suite"
echo "=========================================="
echo ""

# Helper functions
check_health() {
    curl -sf "${MASTER}/health" >/dev/null || { echo "❌ Master health check failed"; exit 1; }
    echo "✅ Master healthy"
}

get_messages() {
    local url=$1
    curl -s "${url}/messages" | jq -c '.messages'
}

post_message() {
    local msg=$1
    local w=${2:-}
    local json_data
    if [ -z "$w" ]; then
        json_data="{\"msg\":\"${msg}\"}"
    else
        json_data="{\"msg\":\"${msg}\",\"w\":${w}}"
    fi
    curl -s -X POST "${MASTER}/messages" \
        -H 'Content-Type: application/json' \
        -d "${json_data}"
}

echo "Test 1: Self-Check Acceptance Test"
echo "-----------------------------------"
echo ""

# Start with M + S1 only (S2 should be stopped)
echo "Prerequisites: Ensure only master and secondary1 are running"
echo "  (Stop secondary2: docker compose stop secondary2)"
echo ""
read -p "Press Enter when ready to continue..."

check_health

echo ""
echo "Step 1: POST Msg1 with w=1 (should return quickly)"
start_time=$(date +%s%N)
resp1=$(post_message "Msg1" 1)
end_time=$(date +%s%N)
duration=$(( (end_time - start_time) / 1000000 ))
echo "Response: $(echo "$resp1" | jq '{w, acks_count: (.acks | length), duration_ms}')"
echo "Duration: ${duration}ms"
if [ "$duration" -lt 100 ]; then
    echo "✅ w=1 returned quickly (< 100ms)"
else
    echo "⚠️  w=1 took ${duration}ms (expected < 100ms)"
fi

echo ""
echo "Step 2: POST Msg2 with w=2 (should wait for one secondary)"
start_time=$(date +%s%N)
resp2=$(post_message "Msg2" 2)
end_time=$(date +%s%N)
duration=$(( (end_time - start_time) / 1000000 ))
echo "Response: $(echo "$resp2" | jq '{w, acks_count: (.acks | length), duration_ms}')"
acks_count=$(echo "$resp2" | jq '.acks | length')
if [ "$acks_count" -ge 1 ]; then
    echo "✅ w=2 got at least 1 ACK"
else
    echo "❌ w=2 expected at least 1 ACK, got ${acks_count}"
fi

echo ""
echo "Step 3: POST Msg3 with w=3 (should block until S2 starts)"
echo "Starting Msg3 in background..."
(
    start_time=$(date +%s%N)
    resp3=$(post_message "Msg3" 3)
    end_time=$(date +%s%N)
    duration=$(( (end_time - start_time) / 1000000 ))
    echo "$resp3" > /tmp/msg3_response.json
    echo "$duration" > /tmp/msg3_duration.txt
    echo "Msg3 completed after ${duration}ms"
) &
MSG3_PID=$!

sleep 1

echo ""
echo "Step 4: POST Msg4 with w=1 (should return immediately, not blocked by Msg3)"
start_time=$(date +%s%N)
resp4=$(post_message "Msg4" 1)
end_time=$(date +%s%N)
duration=$(( (end_time - start_time) / 1000000 ))
echo "Response: $(echo "$resp4" | jq '{w, acks_count: (.acks | length), duration_ms}')"
echo "Duration: ${duration}ms"
if [ "$duration" -lt 100 ]; then
    echo "✅ Msg4 returned quickly while Msg3 is still blocking"
else
    echo "⚠️  Msg4 took ${duration}ms (might be blocked by Msg3?)"
fi

# Check if Msg3 is still running
if kill -0 $MSG3_PID 2>/dev/null; then
    echo "✅ Msg3 is still blocking (as expected)"
else
    echo "⚠️  Msg3 already completed (unexpected if S2 is down)"
fi

echo ""
echo "Step 5: Start S2"
echo "  Run: docker compose start secondary2"
read -p "Press Enter after starting S2..."

# Wait for S2 to be healthy
echo "Waiting for S2 to be healthy..."
for i in {1..30}; do
    if curl -sf "http://${host}:8002/health" >/dev/null 2>&1; then
        echo "✅ S2 is healthy"
        break
    fi
    sleep 1
done

# Wait for Msg3 to complete
echo "Waiting for Msg3 to complete..."
wait $MSG3_PID 2>/dev/null || true

if [ -f /tmp/msg3_response.json ]; then
    resp3=$(cat /tmp/msg3_response.json)
    duration=$(cat /tmp/msg3_duration.txt 2>/dev/null || echo "0")
    echo "Msg3 response: $(echo "$resp3" | jq '{w, acks_count: (.acks | length), duration_ms}')"
    acks_count=$(echo "$resp3" | jq '.acks | length')
    if [ "$acks_count" -ge 2 ]; then
        echo "✅ Msg3 got ACKs from both secondaries"
    else
        echo "⚠️  Msg3 got ${acks_count} ACKs (expected 2)"
    fi
fi

echo ""
echo "Step 6: Check messages on S2"
sleep 2
s2_messages=$(get_messages "http://${host}:8002")
echo "S2 messages: $s2_messages"

expected='["Msg1","Msg2","Msg3","Msg4"]'
if [ "$s2_messages" == "$expected" ]; then
    echo "✅ S2 has all messages in correct order: [Msg1, Msg2, Msg3, Msg4]"
else
    echo "❌ S2 messages don't match expected"
    echo "   Expected: $expected"
    echo "   Got:      $s2_messages"
fi

echo ""
echo "=========================================="
echo "Test 2: Deduplication Test"
echo "=========================================="
echo ""

echo "Sending same (seq, msg) multiple times to secondary1..."
seq=999999
msg="dedup_test_${seq}"

# Direct replicate calls
for i in {1..3}; do
    echo "Call $i: POST /replicate with seq=${seq}"
    resp=$(curl -s -X POST "http://${host}:8001/replicate" \
        -H 'Content-Type: application/json' \
        -d "{\"msg\":\"${msg}\",\"seq\":${seq}}")
    echo "  Response: $(echo "$resp" | jq -c '{status, duplicate}')"
done

echo ""
echo "Checking messages on secondary1..."
s1_messages=$(get_messages "http://${host}:8001")
dedup_count=$(echo "$s1_messages" | jq "[.[] | select(. == \"${msg}\")] | length")
echo "Occurrences of '${msg}': ${dedup_count}"

if [ "$dedup_count" -eq 1 ]; then
    echo "✅ Deduplication works: message appears exactly once"
else
    echo "❌ Deduplication failed: message appears ${dedup_count} times"
fi

echo ""
echo "=========================================="
echo "Test 3: Total Order (Hide Gaps)"
echo "=========================================="
echo ""

echo "Injecting messages with gaps: seq 1, 2, 4 (missing 3)"
curl -s -X POST "http://${host}:8001/replicate" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"gap_test_1","seq":1}' >/dev/null
curl -s -X POST "http://${host}:8001/replicate" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"gap_test_2","seq":2}' >/dev/null
curl -s -X POST "http://${host}:8001/replicate" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"gap_test_4","seq":4}' >/dev/null

sleep 1

echo "Checking messages (should hide msg4 until msg3 arrives)..."
s1_messages=$(get_messages "http://${host}:8001")
gap_messages=$(echo "$s1_messages" | jq '[.[] | select(startswith("gap_test_"))]')
echo "Gap test messages: $gap_messages"

has_gap4=$(echo "$gap_messages" | jq 'any(. == "gap_test_4")')
if [ "$has_gap4" == "true" ]; then
    echo "❌ Total order violated: gap_test_4 visible before gap_test_3"
else
    echo "✅ Total order correct: gap_test_4 hidden (gap detected)"
fi

echo ""
echo "Injecting missing seq 3..."
curl -s -X POST "http://${host}:8001/replicate" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"gap_test_3","seq":3}' >/dev/null

sleep 1

echo "Checking messages again (should now show all)..."
s1_messages=$(get_messages "http://${host}:8001")
gap_messages=$(echo "$s1_messages" | jq '[.[] | select(startswith("gap_test_"))]')
echo "Gap test messages: $gap_messages"

expected_gaps='["gap_test_1","gap_test_2","gap_test_3","gap_test_4"]'
if [ "$(echo "$gap_messages" | jq -c .)" == "$expected_gaps" ]; then
    echo "✅ Total order correct: all messages visible in order after gap filled"
else
    echo "⚠️  Messages: $gap_messages"
fi

echo ""
echo "=========================================="
echo "Test 4: Heartbeats"
echo "=========================================="
echo ""

echo "Checking master /health endpoint..."
health=$(curl -s "${MASTER}/health")
echo "$health" | jq '{
    status,
    count,
    secondaries,
    secondary_statuses
}'

has_statuses=$(echo "$health" | jq 'has("secondary_statuses")')
if [ "$has_statuses" == "true" ]; then
    echo "✅ Master /health shows secondary statuses"
else
    echo "❌ Master /health missing secondary_statuses"
fi

echo ""
echo "=========================================="
echo "Test 5: Quorum Append"
echo "=========================================="
echo ""

echo "Stopping all secondaries to test quorum..."
echo "  Run: docker compose stop secondary1 secondary2"
read -p "Press Enter after stopping secondaries..."

sleep 3

echo "Attempting POST with no quorum..."
quorum_resp=$(post_message "quorum_test" 1)
quorum_status=$(echo "$quorum_resp" | jq -r 'if type == "object" then .error // "success" else "error" end')

if echo "$quorum_resp" | jq -e '.error' >/dev/null 2>&1; then
    error_msg=$(echo "$quorum_resp" | jq -r '.error')
    if echo "$error_msg" | grep -qi "quorum"; then
        echo "✅ Quorum check works: POST rejected with quorum error"
        echo "   Error: $error_msg"
    else
        echo "⚠️  POST rejected but error doesn't mention quorum: $error_msg"
    fi
else
    echo "❌ Quorum check failed: POST succeeded without quorum"
fi

echo ""
echo "Starting secondaries again..."
echo "  Run: docker compose start secondary1 secondary2"
read -p "Press Enter after starting secondaries..."

sleep 3

echo "Attempting POST with quorum restored..."
quorum_resp2=$(post_message "quorum_test_after" 1)
if echo "$quorum_resp2" | jq -e '.messages' >/dev/null 2>&1; then
    echo "✅ Quorum restored: POST accepted"
else
    echo "⚠️  POST still rejected after quorum restored"
fi

echo ""
echo "=========================================="
echo "All Tests Complete"
echo "=========================================="

