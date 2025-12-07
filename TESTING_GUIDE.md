# Local Testing Guide

Quick guide for testing the replicated log system locally.

## Prerequisites

- Docker Desktop running
- `curl` (and `jq` for pretty output)
- Python 3.11+ (for generating docker-compose.yml)

---

## Step 1: Checkout the Repository

```bash
git clone https://github.com/elgrassa/replicated-log-python.git
cd replicated-log-python
```

Or if you already pulled:
```bash
cd /path/to/replicated-log-python
git pull origin main
```

---

## Configure Number of Nodes

The default setup uses 2 secondaries. To use more:

### Default (2 secondaries)
Skip this step - `docker-compose.yml` is already configured.

### Custom N nodes

Generate `docker-compose.yml` with N secondary nodes:

```bash
# Set number of secondaries (default: 2)
export NUM_SECONDARIES=3

# Set delays for each secondary (comma-separated, default: "0,1500")
# Example: 3 secondaries with delays 0ms, 500ms, 2000ms
export SECONDARY_DELAYS="0,500,2000"

# Set base port for secondaries (default: 8001)
export SECONDARY_BASE_PORT=8001

# Set master port (default: 8000)
export MASTER_PORT=8000

# Generate docker-compose.yml
python3 generate_compose.py
```

**Example: 5 secondaries with different delays**
```bash
export NUM_SECONDARIES=5
export SECONDARY_DELAYS="0,100,500,1000,2000"
python3 generate_compose.py
```

**Output:**
```
Generated docker-compose.yml with 5 secondary nodes
Master: http://localhost:8000
Secondary 1: http://localhost:8001
Secondary 2: http://localhost:8002
Secondary 3: http://localhost:8003
Secondary 4: http://localhost:8004
Secondary 5: http://localhost:8005
```

---

## Build and Run

```bash
docker compose up --build -d
```

Verify containers are running:
```bash
docker compose ps
```

Should show (with 2 secondaries):
```
NAME             STATUS         PORTS
rl-master        Up             0.0.0.0:8000->8000/tcp
rl-secondary-1   Up             0.0.0.0:8001->8001/tcp
rl-secondary-2   Up             0.0.0.0:8002->8001/tcp
```

**View logs (optional):**
```bash
# Follow all logs
docker compose logs -f

# Or view specific service logs
docker compose logs -f master
docker compose logs -f secondary1
```

---

## Verify Services

Check health endpoints:

**Default (2 secondaries):**
```bash
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8001/health | jq
curl -s http://localhost:8002/health | jq
```

**If you configured more than 2 secondaries:**
```bash
# Example: If you configured 5 secondaries, check ports 8001-8005
# Replace 5 with your actual NUM_SECONDARIES value
for port in {8001..8005}; do
  echo "Checking port $port:"
  curl -s http://localhost:$port/health | jq
done
```

---

## Step 2: Test Master GET Method (Returns All Messages)

**Requirement:** Master should expose GET method that returns all messages from the in-memory list.

**Test:**
```bash
curl -s http://localhost:8000/messages
```

**Expected:** Empty list initially
```json
{"messages":[]}
```

**Test with jq (optional):**
```bash
curl -s http://localhost:8000/messages | jq
```

---

## Step 3: Test Master POST Method (Appends Message)

**Requirement:** Master should expose POST method that appends a message into the in-memory list.

**Test:**
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"test message 1"}'
```

**Expected:** Returns the message list with the new message
```json
{"messages":["test message 1"],"acks":[...],"duration_ms":...}
```

**Verify the message was appended:**
```bash
curl -s http://localhost:8000/messages | jq
```

**Expected:**
```json
{
  "messages": ["test message 1"]
}
```

---

## Step 5: Test Secondary GET Method (Returns Replicated Messages)

**Requirement:** Secondary should expose GET method that returns all replicated messages from the in-memory list.

**Test all secondaries:**

**For default setup (2 secondaries):**
```bash
curl -s http://localhost:8001/messages | jq
curl -s http://localhost:8002/messages | jq
```

**Note:** If you configured more than 2 secondaries, check all of them:
```bash
# Example: If you configured 5 secondaries using generate_compose.py
# Replace 5 with your actual NUM_SECONDARIES value
for port in {8001..8005}; do
  echo "Secondary on port $port:"
  curl -s http://localhost:$port/messages | jq
done
```

**Expected:** All secondaries return the same messages as master
```json
{
  "messages": ["test message 1"]
}
```

**✅ Requirement Verified:** Messages are replicated to all secondaries.

---

## Step 6: Test Blocking Replication (ACK Requirement)

**Requirement:** Master's POST should finish only after receiving ACKs from all Secondaries (blocking replication).

**Test with timing:**
```bash
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"blocking test"}'
```

**Expected:** 
- The `time` command shows the request takes as long as the slowest secondary
- The response includes `duration_ms` field showing the replication duration
- Response includes `acks` array with N entries (one per secondary)

**Check the response structure:**
```bash
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"blocking test"}' | jq '.acks, .duration_ms'
```

**Expected (for 2 secondaries):**
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
1517
```

**For N secondaries:** The `acks` array will have N entries, one for each secondary.

**✅ Requirement Verified:** 
- Master waits for ACKs from all secondaries
- Response time reflects the slowest secondary (longest delay)

---

## Step 7: Test Multiple Messages (Replication After Each POST)

**Requirement:** After each POST request, the message should be replicated on every Secondary server.

**Test sequence:**
```bash
# Post message 1
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"message 1"}'

# Post message 2
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"message 2"}'

# Post message 3
curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"message 3"}'
```

**Verify consistency across all nodes:**

For default 2 secondaries:
```bash
echo "Master:"
curl -s http://localhost:8000/messages | jq '.messages'

echo "Secondary 1:"
curl -s http://localhost:8001/messages | jq '.messages'

echo "Secondary 2:"
curl -s http://localhost:8002/messages | jq '.messages'
```

For N secondaries:
```bash
echo "Master:"
curl -s http://localhost:8000/messages | jq '.messages'

# Check all secondaries
for port in {8001..800N}; do  # Replace N with your number
  echo "Secondary on port $port:"
  curl -s http://localhost:$port/messages | jq '.messages'
done
```

**Expected:** All nodes return identical message arrays
```json
["test message 1", "blocking test", "message 1", "message 2", "message 3"]
```

**✅ Requirement Verified:** Each POST triggers replication to all secondaries.

---

## Step 8: Test Delay/Sleep on Secondary (Prove Blocking)

**Requirement:** To test that replication is blocking, introduce delay/sleep on the Secondary.

**Current setup:** Secondary 2 has `DELAY_MS=1500` in docker-compose.yml

**Test 1: Compare with delay**
```bash
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"with delay"}'
```

**Expected:** Takes ~1.5+ seconds

**Test 2: Increase delay and verify**

**⚠️ Note:** Restarting containers will clear all messages (in-memory storage). Test this before adding important messages, or accept that messages will be lost.

1. Edit `docker-compose.yml`:
   ```yaml
   secondary2:
     environment:
       - DELAY_MS=3000  # Change from 1500 to 3000
   ```

   **OR** regenerate with new delay:
   ```bash
   export NUM_SECONDARIES=2
   export SECONDARY_DELAYS="0,3000"
   python3 generate_compose.py
   ```

2. Restart (this will clear all existing messages):
   ```bash
   docker compose up --build -d
   sleep 3
   ```

3. Test again with a new message:
   ```bash
   time curl -s -X POST http://localhost:8000/messages \
     -H 'Content-Type: application/json' \
     -d '{"msg":"Blocking Test 3s"}' | jq '.duration_ms'
   ```

**Expected:** Now takes ~3+ seconds (messages from previous tests are lost, but delay is verified)

**✅ Requirement Verified:** Blocking replication is proven by delay affecting response time.

---

## Step 9: Test Logging Support

**Requirement:** Implementation should support logging.

**View Master logs:**
```bash
docker compose logs master
```

**Expected:** See log entries for:
- Server startup
- POST requests received
- Replication attempts to secondaries
- ACK responses

**View Secondary logs:**
```bash
docker compose logs secondary1
docker compose logs secondary2
```

**Expected:** See log entries for:
- Server startup
- Replication requests received
- Delay application (for secondary2)
- ACK responses sent

**Follow logs in real-time:**
```bash
docker compose logs -f master
```

**Test:** Make a POST request in another terminal and watch logs appear.

**✅ Requirement Verified:** Logging is working and shows request/replication details.

---

## Step 10: Test Error Handling (Edge Cases)

**Test 1: Invalid JSON payload**
```bash
curl -i -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"nope":123}'
```

**Expected:** HTTP 400 with error message
```json
{"error":"Expected JSON with string field 'msg'"}
```

**Test 2: Missing Content-Type header**
```bash
curl -i -X POST http://localhost:8000/messages \
  -d '{"msg":"test"}'
```

**Expected:** May return 400 or handle gracefully

**Test 3: Malformed JSON**
```bash
curl -i -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{invalid json}'
```

**Expected:** HTTP 400 with JSON parsing error

---

## Step 11: Test Docker Support

**Requirement:** Master and Secondaries should run in Docker.

**Verify containers are running:**
```bash
docker compose ps
```

**Verify containers are isolated:**
```bash
docker exec rl-master curl -s http://localhost:8000/health
docker exec rl-secondary-1 curl -s http://localhost:8001/health
docker exec rl-secondary-2 curl -s http://localhost:8001/health
```

**Note:** If you configured more than 2 secondaries, check all of them:
```bash
docker exec rl-master curl -s http://localhost:8000/health
# Replace N with your actual NUM_SECONDARIES value
for i in {1..N}; do
  docker exec rl-secondary-$i curl -s http://localhost:8001/health
done
```

**Expected:** All return health check responses

**Test container restart (data loss expected):**
```bash
docker compose restart master
curl -s http://localhost:8000/messages | jq
```

**Expected:** Messages list is empty (in-memory, no persistence)

**✅ Requirement Verified:** All services run in Docker containers.

---

## Step 12: Automated Verification Script

**Run the provided verification script:**
```bash
./scripts/verify.sh
```

**Expected Output:**
```
Health checks...
OK
Append via master (blocking) ...
1515
2
Consistency...
master:     ["message 1", "message 2", ...]
secondary1: ["message 1", "message 2", ...]
secondary2: ["message 1", "message 2", ...]
Consistent ✅
```

**Or use Makefile:**
```bash
make smoke
```

---

## Step 13: Python Test Suite

**Run pytest:**
```bash
pytest -q tests/smoke_test.py -v
```

**Expected:**
```
tests/smoke_test.py::test_health PASSED
tests/smoke_test.py::test_blocking_and_consistency PASSED
```

---

## Summary Checklist

- [x] **Master GET method** - Returns all messages from in-memory list
- [x] **Master POST method** - Appends message to in-memory list
- [x] **Secondary GET method** - Returns all replicated messages
- [x] **Replication after POST** - Each POST replicates to all secondaries
- [x] **ACK requirement** - Master ensures secondaries received message via ACK
- [x] **Blocking replication** - POST finishes only after all ACKs received
- [x] **Delay on Secondary** - Delay proves blocking behavior
- [x] **Perfect communication** - No failures assumed (as per requirement)
- [x] **Logging support** - Logs show request/replication details
- [x] **Docker support** - All services run in Docker containers

---

## Cleanup

**Stop containers:**
```bash
docker compose down
```

**Stop and remove volumes:**
```bash
docker compose down -v
```

