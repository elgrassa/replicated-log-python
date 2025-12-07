#!/usr/bin/env bash
# Collect logs for Iteration 3 after test execution

set -euo pipefail

OUTPUT_DIR="Iteration_3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "Collecting Iteration 3 Logs"
echo "=========================================="
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

echo "Collecting logs from all services..."
echo ""

# Collect individual service logs
echo "Collecting master logs..."
docker compose logs master > "$OUTPUT_DIR/replicated_log_master.txt" 2>&1 || true

echo "Collecting secondary1 logs..."
docker compose logs secondary1 > "$OUTPUT_DIR/replicated_log_secondary1.txt" 2>&1 || true

echo "Collecting secondary2 logs..."
docker compose logs secondary2 > "$OUTPUT_DIR/replicated_log_secondary2.txt" 2>&1 || true

# Collect combined logs
echo "Collecting combined logs..."
docker compose logs > "$OUTPUT_DIR/replicated_log_all.txt" 2>&1 || true

echo ""
echo "✅ Logs collected successfully!"
echo ""
echo "Log files created in $OUTPUT_DIR/:"
echo "  - replicated_log_master.txt"
echo "  - replicated_log_secondary1.txt"
echo "  - replicated_log_secondary2.txt"
echo "  - replicated_log_all.txt"
echo ""

# Show file sizes
echo "File sizes:"
ls -lh "$OUTPUT_DIR"/replicated_log_*.txt | awk '{print "  " $9 ": " $5}'

