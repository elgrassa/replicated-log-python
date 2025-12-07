# Test Commands Location Guide

Test commands are organized in test files that work both locally and in CI.

## Test Files

### 1. `test_iteration2.sh`
**Location**: Root directory  
**Usage**: `./test_iteration2.sh` or `make test-iter2`

**Contains test scenarios:**

#### A. Write Concern Tests
- **Default write concern**
  - Tests default write concern (no w parameter)
  - Verifies w=N+1, ACK count, duration, consistency
  - Includes timing measurements

- **Write concern w=1**
  - Tests w=1 fast response
  - Verifies temporary inconsistency
  - Checks eventual consistency after wait
  - Includes log checking with docker compose logs

- **Write concern w=2**
  - Tests w=2 with timing verification
  - Checks ACK count and response time
  - Includes log checking for timing details

#### B. Eventual Consistency Tests
- **Temporary inconsistency**
  - Tests controlled inconsistency window
  - Sends multiple w=1 messages quickly
  - Verifies immediate divergence and eventual convergence

- **Mixed write concerns**
  - Tests mixed write concerns eventually align
  - Sends mix1(w=1), mix2(w=2), mix3(default)

#### C. Deduplication & Ordering
- **Message deduplication**
  - Tests secondary deduplication

- **Total ordering**
  - Tests global total ordering
  - Sends 5 messages with mixed w values
  - Verifies same order across all nodes

#### D. Error Handling
- **Invalid write concern**
  - Tests invalid write concern rejection
  - Tests w=0 and w too large

- **Secondary failure scenarios**
  - Manual test instructions (requires stopping containers)

#### E. Additional Timing Tests
- **w=1 timing verification**
  - Verifies w=1 is fast (<100ms)
  - Includes timing measurements

- **w=2 timing verification**
  - Verifies w=2 responds after 1st ACK
  - Includes log checking with docker compose logs

**All commands use:**
- Relative paths (works in CI)
- `$DOCKER_COMPOSE_CMD` variable (adapts to CI if needed)
- `$host` variable (defaults to localhost, can be overridden)

### 2. `tests/test_iteration2.py`
**Location**: `tests/` directory  
**Usage**: `pytest tests/test_iteration2.py -v`

**Contains pytest test classes:**
- `TestWriteConcern`: Default w, w=1, w=2
- `TestEventualConsistency`: Temporary inconsistency, mixed write concerns
- `TestDeduplication`: Message deduplication, direct replicate test
- `TestErrorHandling`: Invalid write concern
- `TestTiming`: w=1 and w=2 timing verification
- `TestConcurrency`: Concurrent posts

**All tests use:**
- Relative paths (works in CI)
- Dynamic secondary port detection
- Proper assertions

### 3. `test_iteration2_concurrent.py`
**Location**: Root directory  
**Usage**: `python3 test_iteration2_concurrent.py` or `make test-iter2-concurrent`

**Contains:**
- `test_concurrent_posts()`: Concurrent posts test
- `test_sequence_uniqueness()`: Sequence number uniqueness verification

## Test Commands

### w=1 Timing Test
**Location**: `test_iteration2.sh` (timing section), `tests/test_iteration2.py` TestTiming.test_timing_w1_fast

```bash
# In test_iteration2.sh:
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"w1_timing_verify","w":1}' | jq '{w, acks_count: (.acks | length), duration_ms}'
```

### w=2 Timing Test
**Location**: `test_iteration2.sh` (timing section), `tests/test_iteration2.py` TestTiming.test_timing_w2_responds_after_first_ack

```bash
# In test_iteration2.sh:
time curl -s -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"msg":"w2_timing_verify","w":2}' | jq '{w, acks_count: (.acks | length), duration_ms}'
docker compose logs master | grep -E "(w2_timing_verify|satisfied)" | tail -3
```

### Log Checking Commands
**Location**: Throughout `test_iteration2.sh`

- w=1 section: `docker compose logs master | grep -E "(w1_test|w=1|asynchronously)"`
- w=2 section: `docker compose logs master | grep -E "(w2_test|satisfied)"`
- Timing section: `docker compose logs master | grep -E "(w2_timing_verify|satisfied)"`

All use `$DOCKER_COMPOSE_CMD` variable for CI compatibility.

## Running Tests

### Local
```bash
# All tests
make test-iter2

# Just timing tests
./test_iteration2.sh localhost | grep -A 20 "E. Additional Timing"

# Pytest
pytest tests/test_iteration2.py::TestTiming -v

# Concurrency
make test-iter2-concurrent
```

### CI (GitHub Actions)
The same commands work because:
- All paths are relative
- `docker compose` command is detected automatically
- Host defaults to `localhost` (works in Docker network)

## Key Features

✅ All manual test commands are in test files  
✅ Relative paths only (CI-compatible)  
✅ Timing measurements included  
✅ Log checking included  
✅ Comprehensive coverage of all Iteration 2 requirements

## Quick Reference

| Test Scenario | Shell Script | Pytest Test |
|--------------|--------------|-------------|
| Default write concern | test_iteration2.sh | test_wc01_default_write_concern |
| Write concern w=1 | test_iteration2.sh | test_wc02_w1_fast |
| Write concern w=2 | test_iteration2.sh | test_wc03_w2_one_secondary |
| Temporary inconsistency | test_iteration2.sh | test_ev01_inconsistency_window |
| Mixed write concerns | test_iteration2.sh | test_ev02_mixed_write_concerns_align |
| Message deduplication | test_iteration2.sh | test_ded01_secondary_deduplicates |
| Direct replicate dedup | - | test_ded02_direct_replicate_dedup |
| Total ordering | test_iteration2.sh | test_ord01_total_ordering |
| Invalid write concern | test_iteration2.sh | test_err01_invalid_write_concern |
| Write concern w=1 timing | test_iteration2.sh | test_timing_w1_fast |
| Write concern w=2 timing | test_iteration2.sh | test_timing_w2_responds_after_first_ack |
| Concurrent posts | test_iteration2_concurrent.py | test_con01_concurrent_posts_preserve_uniqueness |
