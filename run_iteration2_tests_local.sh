#!/usr/bin/env bash
# Run Iteration 2 tests locally and collect logs

set -e

echo "=========================================="
echo "Running Iteration 2 Tests Locally"
echo "=========================================="
echo ""

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker is not running. Please start Docker first."
  exit 1
fi

# Check if services are running
if ! docker compose ps | grep -q "Up"; then
  echo "⚠️  Docker services are not running."
  echo "Starting services..."
  export NUM_SECONDARIES=${NUM_SECONDARIES:-3}
  export SECONDARY_DELAYS=${SECONDARY_DELAYS:-"0,500,1500"}
  python3 generate_compose.py
  docker compose up -d
  echo "Waiting for services to be ready..."
  sleep 5
fi

echo "Running Iteration 2 shell tests..."
./test_iteration2.sh localhost > test_iter2_results.txt 2>&1 || echo "Shell tests completed (check exit code)"

echo ""
echo "Running Iteration 2 pytest tests..."
pytest -v tests/test_iteration2.py > pytest_iter2_results.txt 2>&1 || echo "Pytest tests completed (check exit code)"

echo ""
echo "Running concurrency tests..."
python3 test_iteration2_concurrent.py > concurrent_results.txt 2>&1 || echo "Concurrency tests completed (check exit code)"

echo ""
echo "Running smoke tests..."
pytest -v tests/smoke_test.py::test_write_concern_w1 tests/smoke_test.py::test_write_concern_w2 tests/smoke_test.py::test_eventual_consistency > pytest_smoke_results.txt 2>&1 || echo "Smoke tests completed (check exit code)"

echo ""
echo "Collecting logs..."
./collect_iteration2_logs.sh

echo ""
echo "=========================================="
echo "✅ Tests completed!"
echo "=========================================="
echo ""
echo "Results:"
echo "  - test_iter2_results.txt"
echo "  - pytest_iter2_results.txt"
echo "  - concurrent_results.txt"
echo "  - pytest_smoke_results.txt"
echo ""
echo "Logs collected in Iteration_2/ folder"
