#!/usr/bin/env bash
# Collect Iteration 2 logs after running tests locally

set -e

echo "Collecting Iteration 2 logs..."
mkdir -p Iteration_2

# Detect number of secondaries from docker-compose.yml
NUM_SECONDARIES=$(grep -c "secondary[0-9]:" docker-compose.yml 2>/dev/null || echo "2")

echo "Found $NUM_SECONDARIES secondary node(s)"
echo ""

# Collect master logs
echo "Collecting master logs..."
docker compose logs master > Iteration_2/replicated_log_master.txt 2>&1 || echo "Warning: Could not collect master logs"

# Collect all logs
echo "Collecting all logs..."
docker compose logs > Iteration_2/replicated_log_all.txt 2>&1 || echo "Warning: Could not collect all logs"

# Collect individual secondary logs (only for services that exist)
for i in $(seq 1 $NUM_SECONDARIES); do
  # Check if the service exists before trying to collect logs
  if docker compose ps --format json 2>/dev/null | grep -q "\"secondary$i\""; then
    echo "Collecting secondary$i logs..."
    docker compose logs secondary$i > Iteration_2/replicated_log_secondary$i.txt 2>&1
  else
    echo "Skipping secondary$i (service does not exist)"
    # Don't create empty file for non-existent services
  fi
done

echo ""
echo "✅ Logs collected in Iteration_2/ folder:"
ls -lh Iteration_2/replicated_log_*.txt 2>/dev/null || echo "No log files found"
echo ""
echo "Log file sizes:"
du -h Iteration_2/replicated_log_*.txt 2>/dev/null || echo "No log files found"
