#!/usr/bin/env bash
set -euo pipefail

# Read configuration
NUM_SECONDARIES=${NUM_SECONDARIES:-2}
SECONDARY_BASE_PORT=${SECONDARY_BASE_PORT:-8001}
MASTER_PORT=${MASTER_PORT:-8000}
SECONDARY_DELAYS=${SECONDARY_DELAYS:-0,1500}

# Parse delays into array
IFS=',' read -ra DELAY_ARRAY <<< "$SECONDARY_DELAYS"

# Generate docker-compose.yml
cat > docker-compose.yml <<EOF
services:
  master:
    build: ./master
    container_name: rl-master
    environment:
      - HOST=0.0.0.0
      - PORT=${MASTER_PORT}
      - SECONDARIES=\$(SECONDARIES_LIST)
      - LOG_LEVEL=INFO
    ports:
      - "${MASTER_PORT}:${MASTER_PORT}"
    depends_on:
EOF

# Generate secondary services and SECONDARIES list
SECONDARIES_LIST=""
for i in $(seq 1 $NUM_SECONDARIES); do
  SECONDARY_NAME="secondary${i}"
  HOST_PORT=$((SECONDARY_BASE_PORT + i - 1))
  DELAY=${DELAY_ARRAY[$((i-1))]:-0}
  
  # Build SECONDARIES list for master
  if [ -n "$SECONDARIES_LIST" ]; then
    SECONDARIES_LIST="${SECONDARIES_LIST},"
  fi
  SECONDARIES_LIST="${SECONDARIES_LIST}http://${SECONDARY_NAME}:8001"
  
  # Add depends_on entry
  echo "      - ${SECONDARY_NAME}" >> docker-compose.yml
  
  # Generate secondary service
  cat >> docker-compose.yml <<EOF

  ${SECONDARY_NAME}:
    build: ./secondary
    container_name: rl-${SECONDARY_NAME}
    environment:
      - PORT=8001
      - DELAY_MS=${DELAY}
      - LOG_LEVEL=INFO
    ports:
      - "${HOST_PORT}:8001"
EOF
done

# Replace SECONDARIES_LIST placeholder in master environment
sed -i.bak "s|\$(SECONDARIES_LIST)|${SECONDARIES_LIST}|g" docker-compose.yml
rm -f docker-compose.yml.bak

echo "Generated docker-compose.yml with ${NUM_SECONDARIES} secondary nodes"
echo "Master: http://localhost:${MASTER_PORT}"
for i in $(seq 1 $NUM_SECONDARIES); do
  HOST_PORT=$((SECONDARY_BASE_PORT + i - 1))
  echo "Secondary ${i}: http://localhost:${HOST_PORT}"
done

