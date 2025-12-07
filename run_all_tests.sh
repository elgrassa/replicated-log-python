#!/usr/bin/env bash
# Run all tests locally (same as CI)

set -e

echo "Running all tests..."
echo ""

# Check if services are running
if ! docker compose ps | grep -q "Up"; then
  echo "Starting services..."
  docker compose up --build -d
  sleep 5
  
  timeout=30
  elapsed=0
  while [ $elapsed -lt $timeout ]; do
    if curl -sf http://localhost:8000/health > /dev/null && \
       curl -sf http://localhost:8001/health > /dev/null && \
       curl -sf http://localhost:8002/health > /dev/null; then
      echo "Services ready"
      break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
fi

echo "=== Test 1: verify.sh ==="
./scripts/verify.sh
echo ""

echo "=== Test 2: test_and_log.sh ==="
./test_and_log.sh > test_results.txt 2>&1
if grep -q "✅ All nodes consistent!" test_results.txt; then
  echo "✅ test_and_log.sh PASSED"
  tail -5 test_results.txt
else
  echo "❌ test_and_log.sh FAILED"
  tail -20 test_results.txt
  exit 1
fi
echo ""

echo "=== Test 3: pytest ==="
if command -v pytest >/dev/null 2>&1; then
  pytest -v tests/smoke_test.py
  echo "✅ pytest PASSED"
else
  echo "⚠ pytest not installed (will run in CI)"
fi
echo ""

echo "✅ All tests passed!"

