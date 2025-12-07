#!/usr/bin/env bash
# Comprehensive test sequence covering all requirements from TESTING_GUIDE.md

set -eo pipefail

host=${1:-localhost}

# Detect secondary ports dynamically (same logic as verify.sh)
SECONDARY_PORTS=()

if [ -f "docker-compose.yml" ]; then
  while IFS= read -r port_line; do
    if [[ $port_line =~ \"([0-9]+):8001\" ]]; then
      host_port="${BASH_REMATCH[1]}"
      if [ "$host_port" != "8000" ]; then
        found=false
        for existing_port in "${SECONDARY_PORTS[@]+"${SECONDARY_PORTS[@]}"}"; do
          if [ "$existing_port" = "$host_port" ]; then
            found=true
            break
          fi
        done
        if [ "$found" = false ]; then
          SECONDARY_PORTS+=("$host_port")
        fi
      fi
    fi
  done < <(grep -E '\"[0-9]+:8001\"' docker-compose.yml)
  
  if [ ${#SECONDARY_PORTS[@]} -gt 0 ]; then
    IFS=$'\n' sorted=($(sort -n <<<"${SECONDARY_PORTS[*]}"))
    unset IFS
    SECONDARY_PORTS=("${sorted[@]}")
  fi
fi

if [ ${#SECONDARY_PORTS[@]} -eq 0 ]; then
  if command -v docker &> /dev/null && command -v jq &> /dev/null; then
    while IFS= read -r port; do
      if [ -n "$port" ] && [ "$port" != "null" ] && [ "$port" != "8000" ]; then
        SECONDARY_PORTS+=("$port")
      fi
    done < <(docker compose ps --format json 2>/dev/null | \
             jq -r '.[] | select(.Service | startswith("secondary")) | .Publishers[0].PublishedPort // empty' 2>/dev/null)
    
    if [ ${#SECONDARY_PORTS[@]} -gt 0 ]; then
      IFS=$'\n' sorted=($(sort -n <<<"${SECONDARY_PORTS[*]}"))
      unset IFS
      SECONDARY_PORTS=("${sorted[@]}")
    fi
  fi
fi

if [ ${#SECONDARY_PORTS[@]} -eq 0 ]; then
  SECONDARY_PORTS=(8001 8002)
fi

NUM_SECONDARIES=${#SECONDARY_PORTS[@]}

echo "Testing with $NUM_SECONDARIES secondary node(s) on ports: ${SECONDARY_PORTS[*]}"
echo ""

# Step 1: Verify containers are running
echo "Step 1: Verify Docker containers"
docker compose ps
echo ""

# Step 2: Health checks
echo "Step 2: Health checks"
curl -sf "http://$host:8000/health" >/dev/null || { echo "Master health failed"; exit 1; }
echo "Master health:"
curl -s "http://$host:8000/health" | jq
echo ""

for i in "${!SECONDARY_PORTS[@]}"; do
  port="${SECONDARY_PORTS[$i]}"
  sec_num=$((i + 1))
  curl -sf "http://$host:$port/health" >/dev/null || { echo "Secondary $sec_num health failed"; exit 1; }
  echo "Secondary $sec_num (port $port) health:"
  curl -s "http://$host:$port/health" | jq
  echo ""
done

# Step 3: Test Master GET Method (empty initially)
echo "Step 3: Master GET /messages (should be empty)"
MASTER_GET=$(curl -s "http://$host:8000/messages")
echo "$MASTER_GET" | jq
if echo "$MASTER_GET" | jq -e '.messages | length == 0' >/dev/null; then
  echo "✓ Empty list confirmed"
else
  echo "⚠ Warning: Expected empty list"
fi
echo ""

# Step 4: Test Master POST Method (first message)
echo "Step 4: Master POST /messages - First message"
RESPONSE1=$(curl -s -X POST "http://$host:8000/messages" \
  -H 'Content-Type: application/json' \
  -d '{"msg":"test message 1"}')
echo "$RESPONSE1" | jq '{messages, acks_count: (.acks | length), duration_ms}'
echo ""

# Step 5: Test Secondary GET Method (verify replication)
echo "Step 5: Verify replication to all secondaries"
echo "Master messages:"
curl -s "http://$host:8000/messages" | jq '.messages'
echo ""

for i in "${!SECONDARY_PORTS[@]}"; do
  port="${SECONDARY_PORTS[$i]}"
  sec_num=$((i + 1))
  echo "Secondary $sec_num (port $port) messages:"
  curl -s "http://$host:$port/messages" | jq '.messages'
  echo ""
done

# Step 6: Test Blocking Replication (ACK requirement)
echo "Step 6: Test blocking replication (ACK requirement)"
BLOCKING_RESP=$(curl -s -X POST "http://$host:8000/messages" \
  -H 'Content-Type: application/json' \
  -d '{"msg":"blocking test"}')
echo "Response with ACKs:"
echo "$BLOCKING_RESP" | jq '{acks, duration_ms}'
ACKS_COUNT=$(echo "$BLOCKING_RESP" | jq '.acks | length')
if [ "$ACKS_COUNT" = "$NUM_SECONDARIES" ]; then
  echo "✓ ACK count matches number of secondaries ($NUM_SECONDARIES)"
else
  echo "⚠ Warning: Expected $NUM_SECONDARIES ACKs, got $ACKS_COUNT"
fi
echo ""

# Step 7: Test Multiple Messages (replication after each POST)
echo "Step 7: Test multiple POSTs (replication after each)"
for i in {2..4}; do
  echo "Posting message $i..."
  curl -s -X POST "http://$host:8000/messages" \
    -H 'Content-Type: application/json' \
    -d "{\"msg\":\"message $i\"}" | jq '{message: .messages[-1], duration_ms, acks_count: (.acks | length)}'
  sleep 0.3
done
echo ""

# Step 8: Verify consistency after multiple messages
echo "Step 8: Consistency check after multiple messages"
MASTER_MSGS=$(curl -s "http://$host:8000/messages" | jq -c '.messages')
echo "Master: $MASTER_MSGS"
echo ""

all_match=true
for i in "${!SECONDARY_PORTS[@]}"; do
  port="${SECONDARY_PORTS[$i]}"
  sec_num=$((i + 1))
  SEC_MSGS=$(curl -s "http://$host:$port/messages" | jq -c '.messages')
  echo "Secondary $sec_num (port $port): $SEC_MSGS"
  if [ "$MASTER_MSGS" != "$SEC_MSGS" ]; then
    echo "⚠ Mismatch detected: master != secondary$sec_num"
    all_match=false
  fi
done
echo ""

if [ "$all_match" = true ]; then
  echo "✓ All nodes consistent"
else
  echo "✗ Inconsistency detected"
fi
echo ""

# Step 9: Test Delay/Sleep on Secondary (prove blocking)
echo "Step 9: Test blocking behavior with delay"
echo "Posting message and measuring time (should reflect slowest secondary delay)..."
START_TIME=$(date +%s.%N)
TIMING_RESP=$(curl -s -X POST "http://$host:8000/messages" \
  -H 'Content-Type: application/json' \
  -d '{"msg":"delay test"}')
END_TIME=$(date +%s.%N)
if command -v bc >/dev/null 2>&1; then
  ELAPSED=$(echo "$END_TIME - $START_TIME" | bc)
  echo "Response duration_ms: $(echo "$TIMING_RESP" | jq -r '.duration_ms')ms"
  echo "Real elapsed time: $(printf "%.3f" $ELAPSED)s"
else
  DURATION_MS=$(echo "$TIMING_RESP" | jq -r '.duration_ms')
  echo "Response duration_ms: ${DURATION_MS}ms"
fi
echo ""

# Step 10: Test Error Handling
echo "Step 10: Test error handling"
echo "Test 1: Invalid JSON payload (missing 'msg' field)"
ERROR_RESP1=$(curl -s -i -X POST "http://$host:8000/messages" \
  -H 'Content-Type: application/json' \
  -d '{"nope":123}' 2>&1)
HTTP_CODE1=$(echo "$ERROR_RESP1" | grep -i "HTTP" | head -1)
echo "$HTTP_CODE1"
if echo "$ERROR_RESP1" | grep -q "400\|Bad Request"; then
  echo "✓ HTTP 400 returned for invalid payload"
else
  echo "⚠ Expected HTTP 400"
fi
echo ""

echo "Test 2: Malformed JSON"
ERROR_RESP2=$(curl -s -i -X POST "http://$host:8000/messages" \
  -H 'Content-Type: application/json' \
  -d '{invalid json}' 2>&1)
HTTP_CODE2=$(echo "$ERROR_RESP2" | grep -i "HTTP" | head -1)
echo "$HTTP_CODE2"
if echo "$ERROR_RESP2" | grep -q "400\|Bad Request"; then
  echo "✓ HTTP 400 returned for malformed JSON"
else
  echo "⚠ Expected HTTP 400"
fi
echo ""

# Step 11: Test Docker Support (container isolation)
echo "Step 11: Test Docker container isolation"
echo "Testing health from inside containers..."
docker exec rl-master curl -sf http://localhost:8000/health >/dev/null && echo "✓ Master container health OK" || echo "✗ Master container health failed"

for i in "${!SECONDARY_PORTS[@]}"; do
  sec_num=$((i + 1))
  docker exec "rl-secondary-$sec_num" curl -sf http://localhost:8001/health >/dev/null && echo "✓ Secondary $sec_num container health OK" || echo "✗ Secondary $sec_num container health failed"
done
echo ""

# Step 12: Final verification
echo "Step 12: Final verification"
MASTER_COUNT=$(curl -s "http://$host:8000/messages" | jq '.messages | length')
echo "Master: $MASTER_COUNT messages"

all_consistent=true
for i in "${!SECONDARY_PORTS[@]}"; do
  port="${SECONDARY_PORTS[$i]}"
  sec_num=$((i + 1))
  SEC_COUNT=$(curl -s "http://$host:$port/messages" | jq '.messages | length')
  echo "Secondary $sec_num (port $port): $SEC_COUNT messages"
  if [ "$MASTER_COUNT" != "$SEC_COUNT" ]; then
    all_consistent=false
  fi
done
echo ""

if [ "$all_consistent" = true ] && [ "$MASTER_COUNT" -gt 0 ]; then
  echo "✅ All nodes consistent with $MASTER_COUNT messages"
else
  echo "❌ Inconsistency or empty state detected"
  exit 1
fi

echo ""
echo "Test sequence complete. Logs will be collected by workflow."
