# Iteration 2 Testing Guide

This guide covers how to verify all Iteration 2 requirements locally.

## Quick Start

```bash
# Start services
make up

# Run comprehensive tests
make test-iter2

# Run concurrency tests
make test-iter2-concurrent

# Run pytest tests
pytest tests/test_iteration2.py -v
```

## Test Scenarios

### A. Write Concern Semantics

#### WC-01: Default Write Concern (All Replicas)
**Goal**: Verify backward compatibility - no `w` parameter = all secondaries

```bash
curl -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"default_w"}'
```

**Expected**:
- `w` = number_of_secondaries + 1
- `acks` count = number_of_secondaries
- `duration_ms` ≈ slowest secondary delay
- All nodes return identical message lists

#### WC-02: w=1 (Master-Only, Fast)
**Goal**: Verify semi-sync - master responds fast, doesn't wait for secondaries

```bash
curl -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"w1_test","w":1}'
```

**Expected**:
- `w` = 1
- `acks` count = 0
- `duration_ms` < 100ms
- **Immediately after**: Master has message, secondaries may not
- **After 2-3 seconds**: All nodes have message (eventual consistency)

#### WC-03: w=2 (Master + One Secondary)
**Goal**: Verify master waits for exactly one secondary

```bash
curl -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"w2_test","w":2}'
```

**Expected**:
- `w` = 2
- `acks` count = 1
- `duration_ms` ≈ fastest secondary (not slowest)

### B. Eventual Consistency

#### EV-01: Controlled Inconsistency Window
**Goal**: Show temporary divergence between master and delayed secondary

```bash
# Send 3 messages quickly with w=1
for i in 1 2 3; do
  curl -X POST http://localhost:8000/messages \
    -H 'Content-Type: application/json' \
    -d "{\"msg\":\"e$i\",\"w\":1}"
done

# Immediately check
curl http://localhost:8000/messages | jq '.messages'
curl http://localhost:8002/messages | jq '.messages'  # delayed secondary
```

**Expected**:
- Master has all 3 messages
- Delayed secondary has 0-2 messages (temporary inconsistency)
- After delay period: All nodes match

#### EV-02: Mixed Write Concerns Eventually Align
**Goal**: With mixed w values, all logs eventually align

```bash
curl -X POST http://localhost:8000/messages -d '{"msg":"mix1","w":1}'
curl -X POST http://localhost:8000/messages -d '{"msg":"mix2","w":2}'
curl -X POST http://localhost:8000/messages -d '{"msg":"mix3"}'  # default

# Wait 4 seconds, then check all nodes
sleep 4
curl http://localhost:8000/messages | jq '.messages'
curl http://localhost:8001/messages | jq '.messages'
curl http://localhost:8002/messages | jq '.messages'
```

**Expected**: All nodes return same ordered list

### C. Deduplication & Total Ordering

#### DED-01: Secondary Deduplicates Same Seq
**Goal**: Prove idempotency - same sequence number not duplicated

This is automatically handled by the system. The secondary's `/replicate` endpoint checks for duplicate sequence numbers before inserting.

**Verify in logs**:
```bash
docker compose logs secondary1 | grep -i duplicate
```

**Expected**: "Duplicate seq X detected, skipping replication"

#### ORD-01: Global Total Ordering
**Goal**: Same order across all nodes regardless of delays

```bash
curl -X POST http://localhost:8000/messages -d '{"msg":"ord1","w":1}'
curl -X POST http://localhost:8000/messages -d '{"msg":"ord2","w":3}'
curl -X POST http://localhost:8000/messages -d '{"msg":"ord3","w":2}'
curl -X POST http://localhost:8000/messages -d '{"msg":"ord4","w":1}'
curl -X POST http://localhost:8000/messages -d '{"msg":"ord5"}'  # default

sleep 5
# Check all nodes - should have same order
```

**Expected**: All nodes return `["...", "ord1", "ord2", "ord3", "ord4", "ord5"]` in same order

### D. Error Handling

#### ERR-01: Invalid Write Concern Rejected
```bash
# Test w=0
curl -X POST http://localhost:8000/messages \
  -d '{"msg":"invalid","w":0}'
# Expected: HTTP 400

# Test w too large
curl -X POST http://localhost:8000/messages \
  -d '{"msg":"too_big","w":10}'
# Expected: HTTP 400
```

#### ERR-02: Secondary Failure When w Cannot Be Satisfied
```bash
# Stop a secondary
docker compose stop secondary2

# Try w=3 (needs 2 secondaries)
curl -X POST http://localhost:8000/messages \
  -d '{"msg":"need_two","w":3}'
# Expected: HTTP 502
```

#### ERR-03: w=1 Still Succeeds With Secondary Failure
```bash
# With secondary2 still stopped
curl -X POST http://localhost:8000/messages \
  -d '{"msg":"available","w":1}'
# Expected: HTTP 201 OK
```

### E. Concurrency

#### CON-01: Concurrent Posts Preserve Uniqueness
```bash
# Run Python concurrency test
python3 test_iteration2_concurrent.py
```

**Expected**:
- All 10 concurrent messages present
- No duplicates
- Consistent ordering across nodes

## Logging Evidence

For each test, check logs to see:

**Master logs**:
```
Appended locally seq=X msg=...
ACK from http://secondaryY:8001 for seq=X status=200
POST /messages completed w=W, acks=K in D ms
```

**Secondary logs**:
```
Simulating delay 1500 ms
Replicated seq=X msg=... (pos=Y)
# OR
Duplicate seq X detected, skipping replication
```

## Running All Tests

### Option 1: Comprehensive Shell Script
```bash
./test_iteration2.sh
```

Covers all scenarios A-D automatically.

### Option 2: Pytest (Automated)
```bash
pytest tests/test_iteration2.py -v
```

Runs all test classes with detailed output.

### Option 3: Concurrency Tests
```bash
python3 test_iteration2_concurrent.py
```

Tests concurrent request handling.

## Manual Verification Checklist

- [ ] WC-01: Default w works, waits for all secondaries
- [ ] WC-02: w=1 is fast, shows temporary inconsistency
- [ ] WC-03: w=2 waits for one secondary only
- [ ] EV-01: Temporary inconsistency visible, then resolves
- [ ] EV-02: Mixed w values eventually align
- [ ] DED-01: Duplicate seq numbers are skipped
- [ ] ORD-01: All nodes maintain same message order
- [ ] ERR-01: Invalid w values rejected
- [ ] ERR-02: Insufficient secondaries cause failure
- [ ] ERR-03: w=1 succeeds even with failures
- [ ] CON-01: Concurrent requests don't create duplicates

## Tips

1. **Timing**: Use `time curl ...` to measure real request duration
2. **Logs**: Use `docker compose logs -f` to watch real-time replication
3. **Consistency**: Always wait a few seconds after w=1 writes before checking secondaries
4. **Delays**: Secondary delays are configured in `docker-compose.yml` via `DELAY_MS`

## Troubleshooting

- **Tests fail immediately**: Services may not be ready - wait a few seconds after `make up`
- **Inconsistency persists**: Increase wait time (some delays are 1500ms)
- **Missing messages**: Check secondary health with `curl http://localhost:8001/health`
- **Concurrency issues**: Ensure you're testing with multiple concurrent requests, not sequential

