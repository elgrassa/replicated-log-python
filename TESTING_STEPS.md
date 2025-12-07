# Testing Guide for Documentation

Commands and steps for testing each requirement. Useful for screenshots and collecting logs.

---

## Prerequisites

Check Docker:
```bash
docker --version
docker compose version
```

Check tools:
```bash
curl --version
jq --version
python3 --version
```

---

## Setup

### Checkout
```bash
# If cloning fresh
git clone https://github.com/elgrassa/replicated-log-python.git
cd replicated-log-python

# Or if already cloned
cd /path/to/replicated-log-python
git pull origin main
```

---

### Generate Docker Compose (optional - for N nodes)

**Default (2 secondaries):**
```bash
# Use existing docker-compose.yml (already configured)
ls -la docker-compose.yml
```

**Custom N nodes:**
```bash
export NUM_SECONDARIES=3
export SECONDARY_DELAYS="0,500,2000"
python3 generate_compose.py
cat docker-compose.yml
```

---

### Build and Start
```bash
# Build images and start containers
docker compose up --build -d

# Wait a few seconds for services to start
sleep 3

# Verify all containers are running
docker compose ps
```

Should show:
```
NAME             STATUS         PORTS
rl-master        Up             0.0.0.0:8000->8000/tcp
rl-secondary-1   Up             0.0.0.0:8001->8001/tcp
rl-secondary-2   Up             0.0.0.0:8002->8001/tcp
```

---

## Master GET Method

Tests that master returns all messages from the in-memory list.

```bash
curl -s http://localhost:8000/messages | jq
```

Returns:
```json
{
  "messages": []
}
```

**Log Entry:**
```
[TEST] Master GET /messages - Returns empty list initially
Response: {"messages":[]}
Status: 200 OK
```

---

## Master POST Method

Tests that master appends messages to the in-memory list.

### Test Command:
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Hello World"}' | jq
```

Response includes:
```json
{
  "messages": ["Hello World"],
  "acks": [
    {
      "ack": "ok",
      "secondary": "http://secondary1:8001"
    },
    {
      "ack": "ok",
      "secondary": "http://secondary2:8001"
    }
  ],
  "duration_ms": 1517
}
```

**Screenshot:** Terminal showing POST request and response with message appended

**Verify message was appended:**
```bash
curl -s http://localhost:8000/messages | jq
```

**Screenshot:** Terminal showing GET request confirming message is in the list

**Log Entry:**
```
[TEST] Master POST /messages - Appends message "Hello World"
Request: {"msg":"Hello World"}
Response: {"messages":["Hello World"],"acks":[...],"duration_ms":1517}
Status: 201 Created
```

---

## Secondary GET Method

Tests that secondaries return replicated messages from their in-memory lists.

### Test Commands:

**Secondary 1:**
```bash
curl -s http://localhost:8001/messages | jq
```

**Secondary 2:**
```bash
curl -s http://localhost:8002/messages | jq
```

Both should return:
```json
{
  "messages": ["Hello World"]
}
```

**Screenshot:** Terminal showing both secondary GET requests with identical messages

**Log Entry:**
```
[TEST] Secondary GET /messages - Returns replicated messages
Secondary 1: {"messages":["Hello World"]}
Secondary 2: {"messages":["Hello World"]}
Status: 200 OK (both)
```

---

## Replication After Each POST

Verifies that each POST triggers replication to all secondaries.

### Test Sequence:

**1. Post multiple messages:**
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Message 1"}' | jq '.messages'

curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Message 2"}' | jq '.messages'

curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Message 3"}' | jq '.messages'
```

**2. Verify consistency across all nodes:**
```bash
echo "=== Master ==="
curl -s http://localhost:8000/messages | jq '.messages'

echo "=== Secondary 1 ==="
curl -s http://localhost:8001/messages | jq '.messages'

echo "=== Secondary 2 ==="
curl -s http://localhost:8002/messages | jq '.messages'
```

All three should return identical arrays:
```json
["Hello World", "Message 1", "Message 2", "Message 3"]
```

**Screenshot:** Terminal showing all three nodes with identical message lists

**Log Entry:**
```
[TEST] Replication After Each POST
POST 1: "Message 1" - Replicated to all secondaries
POST 2: "Message 2" - Replicated to all secondaries
POST 3: "Message 3" - Replicated to all secondaries
Consistency Check:
  Master:     ["Hello World", "Message 1", "Message 2", "Message 3"]
  Secondary1: ["Hello World", "Message 1", "Message 2", "Message 3"]
  Secondary2: ["Hello World", "Message 1", "Message 2", "Message 3"]
✅ All nodes consistent
```

---

## ACK Requirement

Verifies that master waits for ACKs from all secondaries.

### Test Command:
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"ACK Test"}' | jq '.acks'
```

Response should include ACKs array:
```json
[
  {
    "ack": "ok",
    "secondary": "http://secondary1:8001"
  },
  {
    "ack": "ok",
    "secondary": "http://secondary2:8001"
  }
]
```

**Screenshot:** Terminal showing POST response with ACKs array

**Verify in logs:**
```bash
docker compose logs master | grep -i ack | tail -5
```

**Screenshot:** Terminal showing master logs with ACK confirmations

**Log Entry:**
```
[TEST] ACK Requirement
POST message "ACK Test"
Response ACKs:
  - secondary1: "ok"
  - secondary2: "ok"
Master logs show: "Replicated to http://secondary1:8001 ok"
Master logs show: "Replicated to http://secondary2:8001 ok"
✅ All secondaries ACKed
```

---

## Blocking Replication

Verifies that master blocks until all secondaries ACK (blocking replication).

### Test with Timing:

**1. Measure request duration:**
```bash
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Blocking Test"}' | jq '.duration_ms'
```

Should show:
- `time` command: ~1.5+ seconds
- `duration_ms` in response: ~1500ms+

**Screenshot:** Terminal showing `time` output with duration

**2. Check response duration field:**
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Blocking Test 2"}' | jq '{duration_ms, acks_count: (.acks | length)}'
```

Response:
```json
{
  "duration_ms": 1517,
  "acks_count": 2
}
```

**Screenshot:** Terminal showing duration_ms and acks count

**3. Increase delay and verify blocking:**
```bash
# Edit docker-compose.yml to change secondary2 DELAY_MS to 3000
# Or regenerate with new delay
export NUM_SECONDARIES=2
export SECONDARY_DELAYS="0,3000"
python3 generate_compose.py
docker compose up --build -d
sleep 3

# Test again
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Blocking Test 3s"}' | jq '.duration_ms'
```

Should now take ~3+ seconds

**Screenshot:** Terminal showing increased duration with 3s delay

**Log Entry:**
```
[TEST] Blocking Replication
Test 1 (1500ms delay on secondary2):
  Real time: ~1.5 seconds
  duration_ms: 1517
  ✅ Request blocked until all ACKs received

Test 2 (3000ms delay on secondary2):
  Real time: ~3.0 seconds
  duration_ms: 3002
  ✅ Request blocked until slowest secondary ACKs
```

---

## Delay on Secondary

Tests blocking behavior by introducing delay on a secondary.

### Test Configuration:

**1. Check current delays:**
```bash
curl -s http://localhost:8001/health | jq '.delay_ms'
curl -s http://localhost:8002/health | jq '.delay_ms'
```

Should show:
- Secondary 1: `0`
- Secondary 2: `1500`

**Screenshot:** Terminal showing health checks with delay_ms values

**2. View secondary logs showing delay:**
```bash
# Clear logs and make a request
docker compose logs secondary2 | tail -10

# Make a POST request
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Delay Test"}' > /dev/null

# Check logs
docker compose logs secondary2 | grep -i delay | tail -3
```

**Screenshot:** Terminal showing secondary2 logs with "Simulating delay 1500 ms"

**Log Entry:**
```
[TEST] Delay/Sleep on Secondary
Secondary 1 delay_ms: 0
Secondary 2 delay_ms: 1500
Secondary 2 logs: "Simulating delay 1500 ms"
✅ Delay applied before ACK
```

---

## Perfect Communication Channel

Assumes perfect communication (no failures or message loss).

### Test: Verify No Message Loss

**1. Post messages and verify all are present:**
```bash
# Post 10 messages
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/messages \
    -H 'Content-Type: application/json' \
    -d "{\"msg\":\"Message $i\"}" > /dev/null
done

# Verify all messages on all nodes
echo "Master messages count:"
curl -s http://localhost:8000/messages | jq '.messages | length'

echo "Secondary 1 messages count:"
curl -s http://localhost:8001/messages | jq '.messages | length'

echo "Secondary 2 messages count:"
curl -s http://localhost:8002/messages | jq '.messages | length'
```

All nodes should show the same count (13 if you had 3 previous messages)

**Screenshot:** Terminal showing identical message counts across all nodes

**2. Verify message content matches:**
```bash
curl -s http://localhost:8000/messages | jq -c '.messages' > master.json
curl -s http://localhost:8001/messages | jq -c '.messages' > secondary1.json
curl -s http://localhost:8002/messages | jq -c '.messages' > secondary2.json

diff master.json secondary1.json
diff master.json secondary2.json
```

Should show no differences (empty diff output)

**Screenshot:** Terminal showing `diff` commands with no output (files identical)

**Log Entry:**
```
[TEST] Perfect Communication Channel
Posted 10 messages sequentially
Message counts:
  Master: 13
  Secondary 1: 13
  Secondary 2: 13
Content comparison:
  master.json == secondary1.json ✅
  master.json == secondary2.json ✅
✅ No message loss, perfect replication
```

---

## Logging

Verifies that logging is working.

### Test Logging:

**1. Master logs:**
```bash
docker compose logs master | tail -20
```

Should see:
- Server startup
- POST requests received
- Replication attempts
- ACK confirmations
- Request completion

**Screenshot:** Terminal showing master logs with request/replication details

**2. Secondary logs:**
```bash
docker compose logs secondary1 | tail -10
docker compose logs secondary2 | tail -10
```

Should see:
- Server startup
- Replication requests received
- Delay application (for secondary2)
- Message replication

**Screenshot:** Terminal showing secondary logs

**3. Follow logs in real-time:**
```bash
# In one terminal, follow master logs
docker compose logs -f master

# In another terminal, make a request
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Log Test"}' > /dev/null
```

**Screenshot:** Terminal showing real-time log output

**Log Entry:**
```
[TEST] Logging Support
Master logs show:
  - "Appended locally idx=13 msg=Log Test"
  - "Replicated to http://secondary1:8001 ok"
  - "Replicated to http://secondary2:8001 ok"
  - "POST /messages completed with 2 acks in 1517 ms"

Secondary 1 logs show:
  - "Replicated idx=13 msg=Log Test"

Secondary 2 logs show:
  - "Simulating delay 1500 ms"
  - "Replicated idx=13 msg=Log Test"

✅ Comprehensive logging implemented
```

---

## Docker Support

Verifies that all services run in Docker containers.

### Test Docker Support:

**1. Verify containers are running:**
```bash
docker compose ps
```

**Screenshot:** Terminal showing all containers in "Up" status

**2. Verify container isolation:**
```bash
docker exec rl-master curl -s http://localhost:8000/health | jq
docker exec rl-secondary-1 curl -s http://localhost:8001/health | jq
docker exec rl-secondary-2 curl -s http://localhost:8001/health | jq
```

**Screenshot:** Terminal showing health checks from inside containers

**3. Verify container images:**
```bash
docker images | grep replicated-log-python
```

**Screenshot:** Terminal showing Docker images

**4. Test container restart (data loss expected):**
```bash
docker compose restart master
sleep 2
curl -s http://localhost:8000/messages | jq
```

Messages list should be empty (in-memory, no persistence)

**Screenshot:** Terminal showing empty messages after restart

**Log Entry:**
```
[TEST] Docker Support
Containers running:
  - rl-master (Up)
  - rl-secondary-1 (Up)
  - rl-secondary-2 (Up)
Container isolation verified:
  - Each container can access its own service ✅
Docker images:
  - replicated-log-python-master
  - replicated-log-python-secondary1
  - replicated-log-python-secondary2
Container restart test:
  - Restarted master container
  - Messages list empty (in-memory storage) ✅
✅ All services run in Docker containers
```

---

## Complete Test Sequence

Run this to test everything at once:

```bash
# 1. Start fresh
docker compose down
docker compose up --build -d
sleep 3

# 2. Health checks
echo "=== Health Checks ==="
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8001/health | jq
curl -s http://localhost:8002/health | jq

# 3. Master GET (empty)
echo -e "\n=== Master GET (empty) ==="
curl -s http://localhost:8000/messages | jq

# 4. Master POST
echo -e "\n=== Master POST ==="
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Test Message"}' | jq

# 5. Verify replication
echo -e "\n=== Verify Replication ==="
echo "Master:"
curl -s http://localhost:8000/messages | jq '.messages'
echo "Secondary 1:"
curl -s http://localhost:8001/messages | jq '.messages'
echo "Secondary 2:"
curl -s http://localhost:8002/messages | jq '.messages'

# 6. Blocking test
echo -e "\n=== Blocking Replication Test ==="
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"Blocking Test"}' | jq '.duration_ms, .acks | length'

# 7. Logs
echo -e "\n=== Recent Logs ==="
docker compose logs master | tail -5
docker compose logs secondary2 | tail -3
```

**Screenshot:** Full terminal output showing complete test sequence

---

## Log File Template

Use this structure for `replicated.log`:

```
================================================================================
REPLICATED LOG SYSTEM - TEST RESULTS
================================================================================
Date: [DATE]
Tester: [NAME]
Environment: Docker Compose
Configuration: 1 Master, 2 Secondaries

--------------------------------------------------------------------------------
REQUIREMENT 1: Master GET Method
--------------------------------------------------------------------------------
Test: GET http://localhost:8000/messages
Expected: Returns all messages from in-memory list
Result: ✅ PASS
Response: {"messages":[]}
Status: 200 OK

--------------------------------------------------------------------------------
REQUIREMENT 2: Master POST Method
--------------------------------------------------------------------------------
Test: POST http://localhost:8000/messages with {"msg":"Hello World"}
Expected: Appends message to in-memory list
Result: ✅ PASS
Response: {"messages":["Hello World"],"acks":[...],"duration_ms":1517}
Status: 201 Created

[... Continue for all requirements ...]

================================================================================
SUMMARY
================================================================================
Total Requirements: 10
Passed: 10
Failed: 0
Status: ✅ ALL REQUIREMENTS MET
================================================================================
```

---

## Screenshot Tips

- Clear terminal before each test
- Include full commands with prompts
- Highlight important outputs
- Show timestamps
- Capture error cases
- Compare all nodes side-by-side
- Include relevant log snippets

---

## Cleanup

After testing:
```bash
# Stop containers
docker compose down

# Remove volumes (if any)
docker compose down -v

# Clean up generated files
rm -f master.json secondary1.json secondary2.json
```

